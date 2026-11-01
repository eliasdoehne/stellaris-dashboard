"""Data-driven descriptor registry for historical events.

Each ``HistoricalEventType`` maps to an :class:`EventDescriptor` carrying its
``category`` (topic group) and ``tier`` (importance), which drive the ledger's
filters, an ``icon`` name (not rendered yet), whether it is a point in time or
a ``SPAN`` (council seats, envoy assignments, ...), and the Jinja ``sentence``
that :func:`render_sentence` renders for one event dict.

Multi-stage arcs (:class:`~stellarisdashboard.datamodel.Storyline`) have a
:class:`StorylineDescriptor` in :data:`STORYLINE_REGISTRY`.

Event types that the parser doesn't emit yet carry ``sentence=None`` and render
as empty markup.
"""
import enum
import json
import re
from dataclasses import dataclass
from typing import Optional

from markupsafe import Markup, escape


class Category(enum.Enum):
    WARFARE = "Warfare"
    DIPLOMACY = "Diplomacy"
    GALAXY = "Galaxy"
    EXPANSION = "Expansion"
    SCIENCE = "Science"
    STATECRAFT = "Statecraft"
    LEADERS = "Leaders"
    EXPLORATION = "Exploration"


class Tier(enum.IntEnum):
    HEADLINE = 4
    MAJOR = 3
    ROUTINE = 2
    TRIVIA = 1


# How an event is dated: a point in time, or a span with an optional end date.
POINT = "point"
SPAN = "span"
ENVOY = "envoy"   # a span that is merged per envoy group (see event_grouping)

# Helper macros, made available to every sentence template via a shared prefix.
# the(x) / The(x) render an entity link (or a plain name) after "the", see with_article.
_MACROS = """
{% macro render_leader(event, fallback="an unknown leader") -%}
    {% if "leader" in event %}{{ event["leader"].leader_class.capitalize() }} {{ links[event["leader"]] | safe }}{% else %}{{ fallback }}{% endif %}
{%- endmacro %}
{% macro country_under_ruler(event) -%}
    {% if "leader" in event %}Under {{ render_leader(event) }}, {{ the(event["country"]) }}{% else %}{{ The(event["country"]) }}{% endif %}
{%- endmacro %}
{% macro leader_of_country(event) -%}
    {% if "leader" in event %}{{ render_leader(event) }} of {{ the(event["country"]) }}{% else %}{{ The(event["country"]) }}{% endif %}
{%- endmacro %}
{% macro the_war(war) -%}
    {{ the(war) }}{% if not war.ingame_name %} war{% endif %}
{%- endmacro %}
{% macro in_system(event) -%}
    {% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %}
{%- endmacro %}
{% macro during_war(event) -%}
    {% if "war" in event %} during {{ the_war(event["war"]) }}{% endif %}
{%- endmacro %}
{% macro under_governor(event) -%}
    {% if "leader" in event %} under the governorship of {{ render_leader(event) }}{% endif %}
{%- endmacro %}
{% macro render_envoys(event) -%}
    {% if "leaders" in event %}
        {% set others = event["other_envoy_count"] %}
        {% for leader in event["leaders"] %}{{ leader.leader_class.capitalize() }} {{ links[leader] | safe }}{% if loop.revindex == 2 and others <= 0 %} and {% elif not loop.last %}, {% endif %}{% endfor %}
        {% if others > 0 %} and {{ others }} other envoy{% if others > 1 %}s{% endif %}{% endif %}
    {% else %}
        {{ render_leader(event) }}
    {% endif %}
{%- endmacro %}
{% macro render_assignment_count(event) -%}
    {% if "assignment_count" in event %} ({{ event["assignment_count"] }} assignments){% endif %}
{%- endmacro %}
{% macro render_planet_and_class(event, fallback="an unknown planet") -%}
    {% if "planet" in event %}
        {% if event["planet"].planet_class is not none %}the {{ event["planet"].planetclass }} {% endif %}{{ links[event["planet"]] | safe }}
    {% else %}
        {{ fallback }}
    {% endif %}
{%- endmacro %}
{% macro render_a_an(text) -%}
    {{ "an" if text.lstrip('"').lower().startswith(("a", "e", "i", "o", "u")) else "a" }}
{%- endmacro %}
{% macro combat_exhaustion(event) -%}
    (exhaustion: attackers {{ event["attacker_exhaustion"] }}, defenders {{ event["defender_exhaustion"] }})
{%- endmacro %}
"""


def has_own_article(name: str) -> bool:
    """Whether a name starts with its own article, like "The Chosen" or "The War in Heaven"."""
    return name.lower().startswith("the ")


def with_article(entity, links=None, capitalize=False) -> Markup:
    """An entity's link (or a plain name) after "the", unless the name brings its own.

    "the Tzynn Empire", but "The Chosen" instead of "the The Chosen".
    """
    if isinstance(entity, str):
        name, shown = entity, escape(entity)
    else:
        name, shown = entity.rendered_name, Markup(links[entity])
    if has_own_article(name):
        return shown
    return Markup("The " if capitalize else "the ") + shown


def with_articles(entities, links=None, capitalize=False) -> Markup:
    """Several entities, each with its article: "the A, the B and the C"."""
    parts = [with_article(e, links, capitalize and i == 0) for i, e in enumerate(entities)]
    if len(parts) < 2:
        return Markup("").join(parts)
    return Markup(", ").join(parts[:-1]) + Markup(" and ") + parts[-1]


# galactic_crisis event description (see parsing/galaxy_events.py) -> what happened to its country
GALACTIC_CRISIS_PHRASES = {
    "unbidden_arrived": "came through a dimensional portal and invaded the galaxy, starting the end-game crisis",
    "aberrant_arrived": "came through a second dimensional portal",
    "vehement_arrived": "came through a third dimensional portal",
    "unbidden_portal_destroyed": "lost their dimensional portal",
    "aberrant_portal_destroyed": "lost their dimensional portal",
    "vehement_portal_destroyed": "lost their dimensional portal",
    "extradimensional_defeated": "were defeated, ending the extradimensional invasion",
    "prethoryn_arrived": "arrived from beyond the galaxy, starting the end-game crisis",
    "prethoryn_defeated": "were defeated, ending the Prethoryn invasion",
    "contingency_activated": "awoke, starting the end-game crisis",
    "contingency_defeated": "were defeated when their last machine world fell",
    "synth_queen_arrived": "appeared, starting the end-game crisis",
    "synth_queen_war": "declared war on the galaxy",
    "synth_queen_victorious": "prevailed over the galaxy",
    "synth_queen_defeated": "were defeated, ending the crisis",
    "gray_tempest_arrived": "emerged as the Gray Tempest",
    "gray_tempest_defeated": "were defeated, ending the Gray Tempest",
    "voidworm_plague_started": "spread across the galaxy, starting the Voidworm Plague",
    "voidworm_plague_ended": "receded, ending the Voidworm Plague",
    "great_khan_rose": "united under the Great Khan and set out to conquer the galaxy",
    "great_khan_defeated": "lost the Great Khan's armada in battle; the Khan fled to raise a new one",
    "great_khan_died": "lost the Great Khan, who died, leaving the horde without its leader",
    "war_in_heaven_started": "entered the War in Heaven",
    "war_in_heaven_ended": "saw the War in Heaven end",
    "crisis_stage_2": "spread, escalating the crisis to stage 2 of 4",
    "crisis_stage_3": "spread further, escalating the crisis to stage 3 of 4",
    "crisis_stage_4": "overran the galaxy, escalating the crisis to its final stage",
    "declared_crisis": "were declared a crisis to the galaxy",
    "fallen_empire_awakened": "awoke from their long slumber",
    "fallen_empire_awakened_upstart": "awoke from their long slumber, alarmed by the rise of the younger empires",
    "fallen_empire_awakened_guardian": "awoke from their long slumber to guard the galaxy against the end-game crisis",
    "war_in_heaven_ruled_out": "awoke without an ancient rival, so no War in Heaven will follow",
    "war_in_heaven_rival_chosen": "were chosen as the ancient rival of the awakened empire, fated to wake and contest it",
    "ancient_rival_woke": "awoke as the ancient rival of the awakened empire, setting the War in Heaven in motion",
    "nonaligned_league_formed": "founded the League of Non-Aligned Powers, to stay out of the War in Heaven",
    "nonaligned_league_war": "led the League of Non-Aligned Powers into war against the awakened empires",
    "fallen_empire_lost_war": "lost a war, shattering the myth of Fallen Empire invincibility",
}


@dataclass(frozen=True)
class EventDescriptor:
    category: Category
    tier: Tier
    icon: str
    dateblock: Optional[str] = None
    sentence: Optional[str] = None


# event_type -> EventDescriptor. category is the topic group, tier the importance.
REGISTRY: dict[str, EventDescriptor] = {
    # ---- Warfare --------------------------------------------------------
    "war": EventDescriptor(
        Category.WARFARE, Tier.HEADLINE, "swords", POINT,
        # "..., honoring their alliance with the X." but "... as a primary party."
        '{{ country_under_ruler(event) }} entered {{ the_war(event["war"]) }}'
        '{% if "target_country" in event %}, {{ event["description"] }} {{ the(event["target_country"]) }}'
        '{% else %} {{ event["description"] }}{% endif %}.',
    ),
    "peace": EventDescriptor(
        Category.WARFARE, Tier.HEADLINE, "dove", POINT,
        '{{ country_under_ruler(event) }} made peace in {{ the_war(event["war"]) }}.',
    ),
    "conquered_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        '{{ The(event["country"]) }} conquered the {{ links[event["system"]] | safe }} system'
        '{% if "target_country" in event %} from {{ the(event["target_country"]) }}{% endif %}{{ during_war(event) }}.',
    ),
    "founded_with_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        '{{ The(event["country"]) }} was founded with the {{ links[event["system"]] | safe }} system'
        '{% if "target_country" in event %}, formerly held by {{ the(event["target_country"]) }}{% endif %}.',
    ),
    "lost_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        '{{ The(event["country"]) }} lost control of the {{ links[event["system"]] | safe }} system'
        '{% if "target_country" in event %} to {{ the(event["target_country"]) }}{% else %}, leaving it unclaimed{% endif %}.',
    ),
    "fleet_combat": EventDescriptor(
        Category.WARFARE, Tier.ROUTINE, "crosshair", POINT,
        '{% if event["attacker_victory"] %}'
        '{{ The_all(event["attackers"]) }} defeated {{ the_all(event["defenders"]) }}'
        '{% else %}'
        '{{ The_all(event["defenders"]) }} repelled an attack by {{ the_all(event["attackers"]) }}'
        '{% endif %}'
        ' in a space battle{{ in_system(event) }}{{ during_war(event) }} {{ combat_exhaustion(event) }}.',
    ),
    "army_combat": EventDescriptor(
        Category.WARFARE, Tier.ROUTINE, "crosshair", POINT,
        '{% set planet %}{% if "planet" in event %}the planet {{ links[event["planet"]] | safe }}{% else %}a planet{% endif %}{{ in_system(event) }}{% endset %}'
        '{% if event["attacker_victory"] %}'
        '{{ The_all(event["attackers"]) }} invaded {{ planet }}, defeating {{ the_all(event["defenders"]) }}'
        '{% else %}'
        '{{ The_all(event["defenders"]) }} fought off an invasion of {{ planet }} by {{ the_all(event["attackers"]) }}'
        '{% endif %}'
        '{{ during_war(event) }} {{ combat_exhaustion(event) }}.',
    ),
    # ---- Diplomacy ------------------------------------------------------
    "first_contact": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake", POINT,
        '{{ country_under_ruler(event) }} established first contact with {{ the(event["target_country"]) }}.',
    ),
    "first_contact_stage": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "handshake", POINT,
        '{{ The(event["country"]) }}\'s first contact with {{ the(event["target_country"]) }}: {{ event["description"] }}.',
    ),
    "first_contact_process": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake", SPAN,
        '{{ The(event["country"]) }}{% if "leader" in event %}\'s {{ event["leader"].leader_class }} {{ links[event["leader"]] | safe }}{% endif %}'
        ' {{ event["description"] }} {{ the(event["target_country"]) }}{{ in_system(event) }}.',
    ),
    "espionage_operation": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "eye", SPAN,
        '{{ leader_of_country(event) }} ran the "{{ event["description"] }}" operation against {{ the(event["target_country"]) }}.',
    ),
    "completed_espionage_operation": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "key", POINT,
        '{{ leader_of_country(event) }} completed the "{{ event["description"] }}" operation against {{ the(event["target_country"]) }}.',
    ),
    "formed_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", SPAN,
        '{{ country_under_ruler(event) }} allied with {{ the(event["target_country"]) }} in the "{{ event["description"] }}" federation.',
    ),
    "founded_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        '{{ The(event["country"]) }} founded the "{{ event["federation_name"] }}" federation'
        '{% if "federation_partners" in event %} together with {{ the_all(event["federation_partners"]) }}{% endif %}.',
    ),
    "joined_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        '{{ The(event["country"]) }}'
        '{% if event["changed_role"] %} became {{ "an associate" if event["is_associate"] else "a full" }} member of'
        '{% else %} joined{% endif %} the "{{ event["federation_name"] }}" federation'
        '{% if event["is_associate"] and not event["changed_role"] %} as an associate member{% endif %}'
        '{% if event["federation_member_count"] %} ({{ event["federation_member_count"] }} members){% endif %}.',
    ),
    "left_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        '{{ The(event["country"]) }} left the "{{ event["federation_name"] }}" federation.',
    ),
    "dissolved_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        'The "{{ event["federation_name"] }}" federation of {{ the(event["country"]) }} was dissolved.',
    ),
    "defensive_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "shield", SPAN,
        '{{ country_under_ruler(event) }} entered a defensive pact with {{ the(event["target_country"]) }}.',
    ),
    "non_aggression_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        '{{ country_under_ruler(event) }} entered a non-aggression pact with {{ the(event["target_country"]) }}.',
    ),
    "commercial_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "coins", SPAN,
        '{{ country_under_ruler(event) }} entered a commercial pact with {{ the(event["target_country"]) }}.',
    ),
    "research_agreement": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        '{{ country_under_ruler(event) }} entered a research agreement with {{ the(event["target_country"]) }}.',
    ),
    "migration_treaty": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        '{{ country_under_ruler(event) }} entered a migration treaty with {{ the(event["target_country"]) }}.',
    ),
    "embassy": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "building", SPAN,
        '{{ The(event["country"]) }} established an embassy with {{ the(event["target_country"]) }}.',
    ),
    "closed_borders": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "barrier", SPAN,
        '{{ country_under_ruler(event) }} closed their borders to {{ the(event["target_country"]) }}.',
    ),
    "received_closed_borders": EventDescriptor(
        Category.DIPLOMACY, Tier.TRIVIA, "barrier", SPAN,
        '{{ The(event["country"]) }} lost border access to {{ the(event["target_country"]) }}.',
    ),
    "sent_rivalry": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "rivalry", SPAN,
        '{{ country_under_ruler(event) }} declared {{ the(event["target_country"]) }} a rival.',
    ),
    "received_rivalry": EventDescriptor(
        Category.DIPLOMACY, Tier.TRIVIA, "rivalry", SPAN,
        '{{ country_under_ruler(event) }} was declared a rival by {{ the(event["target_country"]) }}.',
    ),
    "became_subject": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "chain", SPAN,
        '{{ The(event["country"]) }} became {{ render_a_an(event["description"]) }} {{ event["description"] }}'
        ' of {{ the(event["target_country"]) }}.',
    ),
    "federation_president": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "federation", SPAN,
        '{{ The(event["country"]) }} held the presidency of the {{ event["federation_title"] }}.',
    ),
    "federation_level_up": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "federation", POINT,
        'Under the presidency of {{ the(event["country"]) }}, the {{ event["federation_title"] }}'
        ' reached level {{ event["description"] }}.',
    ),
    "federation_renamed": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "federation", POINT,
        'Under the presidency of {{ the(event["country"]) }}, the "{{ event["old_federation_name"] }}" federation'
        ' was renamed "{{ event["federation_name"] }}".',
    ),
    "envoy_community": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", ENVOY,
        '{{ The(event["country"]) }} sent {{ render_envoys(event) }}'
        ' to increase their diplomatic weight in the Galactic Community{{ render_assignment_count(event) }}.',
    ),
    "envoy_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", ENVOY,
        '{{ The(event["country"]) }} sent {{ render_envoys(event) }}'
        ' to improve the cohesion of {{ the(event["description"]) }}{{ render_assignment_count(event) }}.',
    ),
    "envoy_improving_relations": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", SPAN,
        '{{ The(event["country"]) }} sent {{ render_envoys(event) }}'
        ' to improve relations with {{ the(event["target_country"]) }}{{ render_assignment_count(event) }}.',
    ),
    "envoy_harming_relations": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", SPAN,
        '{{ The(event["country"]) }} sent {{ render_envoys(event) }}'
        ' to harm relations with {{ the(event["target_country"]) }}{{ render_assignment_count(event) }}.',
    ),
    # ---- Galaxy ---------------------------------------------------------
    "joined_galactic_community": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        '{{ country_under_ruler(event) }} joined the Galactic Community.',
    ),
    "joined_galactic_council": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        '{{ country_under_ruler(event) }} joined the Galactic Council.',
    ),
    "left_galactic_community": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        '{{ country_under_ruler(event) }} left the Galactic Community.',
    ),
    "left_galactic_council": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        '{{ country_under_ruler(event) }} left the Galactic Council.',
    ),
    "galactic_resolution_passed": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "ballot", POINT,
        'The Galactic Community passed the resolution {{ event["description"] }}'
        '{% if "country" in event %}, proposed by {{ the(event["country"]) }}{% endif %}'
        '{% if "target_country" in event %}, concerning {{ the(event["target_country"]) }}{% endif %}.',
    ),
    "galactic_custodian": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "crown", SPAN,
        '{{ The(event["country"]) }} held the office of {{ event["description"] }}.',
    ),
    "country_destroyed": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "skull", POINT,
        '{{ The(event["country"]) }} ceased to exist'
        '{% if event["description"] and "target_country" in event %} {{ event["description"] }} {{ the(event["target_country"]) }}{% endif %}.',
    ),
    "voted_for_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    "voted_against_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    "galactic_crisis": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "crisis", POINT,
        '{% set phrases = ' + json.dumps(GALACTIC_CRISIS_PHRASES) + ' %}'
        '{{ The(event["country"]) }} {{ phrases.get(event["description"], event["description"]) }}.',
    ),
    # ---- Expansion ------------------------------------------------------
    "megastructure_construction": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "megastructure", SPAN,
        '{{ The(event["country"]) }} {% if event["end_date"] %}built{% else %}began building{% endif %}'
        ' the {{ event["description"] }}{{ in_system(event) }}.',
    ),
    "planet_destroyed": EventDescriptor(
        Category.EXPANSION, Tier.HEADLINE, "explosion", POINT,
        'The planet {{ links[event["planet"]] | safe }}{{ in_system(event) }}'
        '{% if "country" in event %}, owned by {{ the(event["country"]) }},{% endif %} was destroyed.'
        '{% if event["planet"].planet_class is not none %}'
        ' It is now {{ render_a_an(event["planet"].planetclass) }} {{ event["planet"].planetclass }}.'
        '{% else %} No trace remains.{% endif %}',
    ),
    "habitat_ringworld_construction": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "ringworld", POINT,
        '{% if "country" in event %}{{ The(event["country"]) }} finished building{% else %}Construction finished on{% endif %}'
        ' {{ event["description"] }}{{ in_system(event) }}{{ under_governor(event) }}.',
    ),
    "colonization": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "planet", SPAN,
        '{{ The(event["country"]) }} colonized {{ render_planet_and_class(event) }}{{ in_system(event) }}{{ under_governor(event) }}.',
    ),
    "capital_relocation": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "capital", POINT,
        '{{ country_under_ruler(event) }} relocated their capital to {{ render_planet_and_class(event) }}{{ in_system(event) }}.',
    ),
    "terraforming": EventDescriptor(
        Category.EXPANSION, Tier.ROUTINE, "planet", SPAN,
        '{{ The(event["country"]) }} terraformed the planet {{ links[event["planet"]] | safe }}{{ in_system(event) }}'
        ' {{ event["description"] }}{{ under_governor(event) }}.',
    ),
    "expanded_to_system": EventDescriptor(
        Category.EXPANSION, Tier.ROUTINE, "outpost", POINT,
        '{{ The(event["country"]) }} built an outpost in the {{ links[event["system"]] | safe }} system.',
    ),
    "sector_creation": EventDescriptor(
        Category.EXPANSION, Tier.TRIVIA, "sector", POINT,
        '{{ country_under_ruler(event) }} created {{ the(event["description"]) }} sector'
        '{% if "planet" in event %} with its capital on {{ links[event["planet"]] | safe }}{{ in_system(event) }}{% endif %}.',
    ),
    "planetary_unrest": EventDescriptor(Category.EXPANSION, Tier.ROUTINE, "unrest"),
    # ---- Science --------------------------------------------------------
    "discovered_new_system": EventDescriptor(
        Category.SCIENCE, Tier.TRIVIA, "telescope", POINT,
        '{{ The(event["country"]) }} discovered the previously unknown {{ links[event["system"]] | safe }} system.',
    ),
    "researched_technology": EventDescriptor(
        Category.SCIENCE, Tier.ROUTINE, "flask", SPAN,
        '{{ The(event["country"]) }} researched the "{{ event["description"] }}" technology.',
    ),
    "pre_ftl_age_change": EventDescriptor(
        Category.SCIENCE, Tier.MAJOR, "flask", POINT,
        '{{ The(event["country"]) }} {{ event["description"] }}.',
    ),
    # ---- Exploration ----------------------------------------------------
    "discovered_anomaly": EventDescriptor(
        Category.EXPLORATION, Tier.ROUTINE, "search", POINT,
        '{{ The(event["country"]) }} discovered the "{{ event["description"] }}" anomaly'
        ' on {{ render_planet_and_class(event) }}{{ in_system(event) }}.',
    ),
    "researched_anomaly": EventDescriptor(
        Category.EXPLORATION, Tier.ROUTINE, "microscope", POINT,
        '{{ leader_of_country(event) }} finished researching the "{{ event["description"] }}" anomaly'
        ' on {{ render_planet_and_class(event) }}{{ in_system(event) }}.',
    ),
    "excavated_dig_site": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "shovel", POINT,
        '{{ The(event["country"]) }} completed an excavation at the "{{ event["description"] }}" archaeological site'
        ' on {{ render_planet_and_class(event) }}{{ in_system(event) }}.',
    ),
    "explored_astral_rift": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "compass", POINT,
        '{{ leader_of_country(event) }} explored the astral rift "{{ event["description"] }}"{{ in_system(event) }}.',
    ),
    "acquired_relic": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "gem", POINT,
        '{{ The(event["country"]) }} acquired the "{{ event["description"] }}" relic.',
    ),
    "destroyed_leviathan": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "skull", POINT,
        '{{ The(event["country"]) }} destroyed a leviathan'
        '{% if "target_country" in event %} guarded by {{ the(event["target_country"]) }}{% endif %}.',
    ),
    # ---- Statecraft -----------------------------------------------------
    "crisis_level_up": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "star", POINT,
        '{{ The(event["country"]) }} became {{ render_a_an(event["description"]) }} {{ event["description"] }}.',
    ),
    "ascension_perk": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "star", POINT,
        '{{ country_under_ruler(event) }} unlocked the "{{ event["description"] }}" ascension perk.',
    ),
    "government_reform": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "gavel", POINT,
        '{{ country_under_ruler(event) }} reformed their government'
        '{% if event["description"] %}: {{ event["description"] }}{% else %}.{% endif %}',
    ),
    "empire_renamed": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "gavel", POINT,
        '{{ country_under_ruler(event) }} was renamed {{ event["description"] }}.',
    ),
    "species_rights_reform": EventDescriptor(Category.STATECRAFT, Tier.MAJOR, "gavel"),
    "new_faction": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "faction", POINT,
        '{% if event["faction_type"] == event["faction"].rendered_name %}'
        '{% if not has_own_article(event["faction_type"]) %}The {% endif %}"{{ event["faction_type"] }}" faction'
        '{% else %}'
        '{{ The(event["faction_type"]) }} "{{ event["faction"].rendered_name }}"'
        '{% endif %}'
        ' emerged in {{ the(event["country"]) }}.',
    ),
    "new_policy": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "policy", POINT,
        '{{ country_under_ruler(event) }} {{ event["description"] }}.',
    ),
    "changed_policy": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "policy", POINT,
        '{{ country_under_ruler(event) }} {{ event["description"] }}.',
    ),
    "tradition": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "tradition", POINT,
        '{{ country_under_ruler(event) }} {{ event["description"] }}.',
    ),
    "agenda_launch": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "agenda", POINT,
        '{{ country_under_ruler(event) }} launched the "{{ event["description"] }}" agenda.',
    ),
    "edict": EventDescriptor(
        Category.STATECRAFT, Tier.TRIVIA, "edict", SPAN,
        '{{ country_under_ruler(event) }} enacted the "{{ event["description"] }}" edict.',
    ),
    "agenda_preparation": EventDescriptor(
        Category.STATECRAFT, Tier.TRIVIA, "agenda", SPAN,
        '{{ country_under_ruler(event) }} prepared the "{{ event["description"] }}" agenda.',
    ),
    # ---- Leaders --------------------------------------------------------
    "ruled_empire": EventDescriptor(
        Category.LEADERS, Tier.MAJOR, "crown", SPAN,
        '{{ render_leader(event, "An unknown leader") }} ruled {{ the(event["country"]) }}'
        '{% if "planet" in event %} from the capital {{ links[event["planet"]] | safe }}{{ in_system(event) }}{% endif %}.',
    ),
    "leader_died": EventDescriptor(
        Category.LEADERS, Tier.MAJOR, "skull", POINT,
        '{{ render_leader(event, "An unknown leader") }} died or retired.',
    ),
    "leader_recruited": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "person", POINT,
        '{{ The(event["country"]) }} hired {{ render_leader(event) }}.',
    ),
    "councilor": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "council", SPAN,
        # hive minds' councilors are named after their seat, e.g. "Growth Node"
        '{{ render_leader(event, "An unknown leader") }} served'
        '{% if "leader" not in event or event["description"] != event["leader"].rendered_name %} as {{ event["description"] }}{% endif %}'
        ' on the council of {{ the(event["country"]) }}.',
    ),
    "gained_trait": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "dna", POINT,
        '{{ render_leader(event, "An unknown leader") }}'
        ' gained {% if event["description"] %}the "{{ event["description"] }}" trait{% else %}a trait{% endif %}.',
    ),
    "lost_trait": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "dna", POINT,
        '{{ render_leader(event, "An unknown leader") }}'
        ' lost {% if event["description"] %}the "{{ event["description"] }}" trait{% else %}a trait{% endif %}.',
    ),
    "leader_left_country": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "person", POINT,
        '{{ render_leader(event, "An unknown leader") }} left the service of {{ the(event["country"]) }}.',
    ),
    "faction_leader": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "faction", SPAN,
        '{{ render_leader(event, "An unknown leader") }} led the "{{ event["faction"].rendered_name }}" faction.',
    ),
    "governed_sector": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "sector", SPAN,
        '{{ render_leader(event, "An unknown leader") }} governed {{ the(event["description"]) }} sector'
        '{% if "planet" in event %} from {{ links[event["planet"]] | safe }}{{ in_system(event) }}{% endif %}.',
    ),
    "governed_planet": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "planet", SPAN,
        '{{ render_leader(event, "An unknown leader") }} governed the planet {{ links[event["planet"]] | safe }}{{ in_system(event) }}.',
    ),
    "fleet_command": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "fleet", SPAN,
        '{{ render_leader(event, "An unknown leader") }}'
        '{% if event["fleet"].is_civilian_fleet %} commanded the science ship{% else %} led the fleet{% endif %}'
        ' "{{ event["fleet"].rendered_name }}".',
    ),
    "level_up": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "chevron-up", POINT,
        '{{ render_leader(event, "An unknown leader") }} reached level {{ event["description"] }}.',
    ),
    "leader_changed_ethic": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "dna", POINT,
        '{{ render_leader(event, "An unknown leader") }} changed their ethic to {{ event["description"] }}.',
    ),
}


@dataclass(frozen=True)
class StorylineDescriptor:
    category: Category
    tier: Tier
    icon: str
    stage_noun: str  # singular, e.g. "chapter"
    sentence: str  # Jinja; the storyline item dict is named ``event``
    state_labels: dict[str, str]


_STORYLINE_LEADER = '{% if "leader" in event %} ({{ render_leader(event) | trim }}){% endif %}'
_STORYLINE_COUNTRY = '{% if "country" in event %} by {{ the(event["country"]) }}{% endif %}'

# StorylineType name -> StorylineDescriptor
STORYLINE_REGISTRY: dict[str, StorylineDescriptor] = {
    "dig_site": StorylineDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "shovel", "chapter",
        'Excavation of the "{{ event["title"] }}" archaeological site'
        '{% if "planet" in event %} on {{ render_planet_and_class(event) }}{% endif %}'
        '{{ in_system(event) }}' + _STORYLINE_COUNTRY + _STORYLINE_LEADER,
        {"active": "excavating", "paused": "paused", "completed": "fully excavated"},
    ),
    "astral_rift": StorylineDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "compass", "step",
        'Exploration of the astral rift "{{ event["title"] }}"'
        '{{ in_system(event) }}' + _STORYLINE_COUNTRY + _STORYLINE_LEADER,
        {"active": "exploring", "paused": "paused", "completed": "fully explored"},
    ),
    "first_contact": StorylineDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake", "stage",
        'First contact between {{ the(event["country"]) }} and {{ the(event["target_country"]) }}'
        # both sides' envoys once the two sides are merged
        '{{ in_system(event) }}{% if "leader" in event %} ({{ render_envoys(event) | trim }}){% endif %}',
        {"active": "in progress", "completed": "contact made", "abandoned": "abandoned"},
    ),
}

_COMPILED_SENTENCES: dict[str, object] = {}


def render_sentence(event, links) -> Markup:
    """Just the sentence of an event or storyline item, without the date or list markup."""
    if event.get("event_type") == "storyline":
        key = "storyline:" + event.get("storyline_type", "")
        descriptor = STORYLINE_REGISTRY.get(event.get("storyline_type"))
    else:
        key = event.get("event_type")
        descriptor = REGISTRY.get(key)
    if key not in _COMPILED_SENTENCES:
        template = None
        if descriptor is not None and descriptor.sentence is not None:
            from stellarisdashboard.dashboard_app import flask_app

            template = flask_app.jinja_env.from_string(_MACROS + descriptor.sentence)
        _COMPILED_SENTENCES[key] = template
    template = _COMPILED_SENTENCES[key]
    if template is None:
        return Markup("")
    text = template.render(
        event=event,
        links=links,
        has_own_article=has_own_article,
        the=lambda x: with_article(x, links),
        The=lambda x: with_article(x, links, capitalize=True),
        the_all=lambda xs: with_articles(xs, links),
        The_all=lambda xs: with_articles(xs, links, capitalize=True),
    )
    text = " ".join(text.split())
    return Markup(re.sub(r" ([,.;:)])", r"\1", text))


def is_span(event) -> bool:
    """Whether the event lasts over time (council seats, envoy assignments, ...)."""
    descriptor = REGISTRY.get(event.get("event_type"))
    return descriptor is not None and descriptor.dateblock in (SPAN, ENVOY)


def describe(event) -> tuple:
    """(category, tier) of an event or storyline item."""
    if event.get("event_type") == "storyline":
        descriptor = STORYLINE_REGISTRY.get(event.get("storyline_type"))
    else:
        descriptor = REGISTRY.get(event.get("event_type"))
    if descriptor is None:
        return None, None
    return descriptor.category, descriptor.tier
