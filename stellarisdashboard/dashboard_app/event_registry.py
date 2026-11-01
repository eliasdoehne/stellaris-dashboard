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

from markupsafe import Markup


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

# Helper macros, made available to every
# sentence template via a shared prefix.
_MACROS = """
{% macro render_leader(event, fallback="an unknown leader") -%}
    {% if "leader" in event %}
        {{ event["leader"].leader_class.capitalize() }} {{ links[event["leader"]] | safe }}
    {% else %}
        {{ fallback }}
    {% endif %}
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
    {% if event["planet"] is not none %}
        {% if event["planet"].planet_class is not none %}
            the {{ event["planet"].planetclass }} {{ links[event["planet"]] | safe }}
        {% else %}
            {{ links[event["planet"]] | safe }}
        {% endif %}
    {% else %}
        {{ fallback }}
    {% endif %}
{%- endmacro %}
{% macro render_a_an(text) -%}
    {% if text.lower().startswith(("a", "e", "i", "o", "u")) %} an {% else %} a {% endif %}
{%- endmacro %}
"""


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


# event_type -> EventDescriptor. Sentences are copied verbatim from the old
# per-branch markup. category is the topic group, tier the importance.
REGISTRY: dict[str, EventDescriptor] = {
    # ---- Warfare --------------------------------------------------------
    "war": EventDescriptor(
        Category.WARFARE, Tier.HEADLINE, "swords", POINT,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} entered the {{ links[event["war"]] | safe }}{% if not event["war"].ingame_name %} war{% endif %} {{ event["description"] }}{% if event["target_country"] %} {{ links[event["target_country"]] | safe}}{% endif %}.\n',
    ),
    "peace": EventDescriptor(
        Category.WARFARE, Tier.HEADLINE, "dove", POINT,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} made peace in the {{ links[event["war"]] | safe }}{% if not event["war"].ingame_name %} conflict{% endif %}.\n',
    ),
    "conquered_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} conquered the {{ links[event["system"]] | safe }} system from the\n'
        '                    {{ links[event["target_country"]] | safe }}{% if event["war"] %} during the {{ links[event["war"]] | safe }}{% endif %}.\n',
    ),
    "founded_with_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} was founded with the {{ links[event["system"]] | safe }} system, formerly held by the'
        ' {{ links[event["target_country"]] | safe }}.\n',
    ),
    "lost_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} lost control of the {{ links[event["system"]] | safe}}\n'
        '                    system{% if event["target_country"] %} to the {{ links[event["target_country"]] | safe }}.\n'
        '                    {% else %}, leaving it unclaimed.\n'
        '                    {% endif %}\n',
    ),
    "fleet_combat": EventDescriptor(
        Category.WARFARE, Tier.ROUTINE, "crosshair", POINT,
        '\n'
        '                    Space battle in the {{ links[event["system"]] | safe }} system:\n'
        '                    Attackers {{ event["attackers"] | safe }} ({{event["attacker_exhaustion"]}} exhaustion)\n'
        '                    {% if event["attacker_victory"] %} defeated {% else %} were defeated by {% endif %}\n'
        '                    {{ event["defenders"] | safe }} (exhaustion {{event["defender_exhaustion"]}}) in the\n'
        '                    {{ links[event["war"]] | safe }}.\n',
    ),
    "army_combat": EventDescriptor(
        Category.WARFARE, Tier.ROUTINE, "crosshair", POINT,
        '\n'
        '                    Invasion of planet {{ links[event["planet"]] | safe }} in the {{ links[event["system"]] | safe }} system:\n'
        '                    Attackers {{ event["attackers"] | safe }} ({{event["attacker_exhaustion"]}} exhaustion)\n'
        '                    {% if event["attacker_victory"] %} defeated {% else %} were defeated by {% endif %}\n'
        '                    {{ event["defenders"] | safe }} (exhaustion {{event["defender_exhaustion"]}}) in the\n'
        '                    {{ links[event["war"]] | safe }}.\n',
    ),
    # ---- Diplomacy ------------------------------------------------------
    "first_contact": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake", POINT,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} established first contact with the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "first_contact_stage": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "handshake", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }}\'s first contact with the {{ links[event["target_country"]] | safe }}: {{ event["description"] }}.\n',
    ),
    "first_contact_process": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }}{% if "leader" in event %}\'s {{ event["leader"].leader_class }} {{ links[event["leader"]] | safe }}{% endif %}\n'
        '                    {{ event["description"] }} the {{ links[event["target_country"]] | safe }}{% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %}.\n',
    ),
    "espionage_operation": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "eye", SPAN,
        '\n'
        '                    {% if "leader" in event %}{{ render_leader(event) }} of the {% else %}The {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} ran the "{{ event["description"] }}" operation against the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "completed_espionage_operation": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "key", POINT,
        '\n'
        '                    {% if "leader" in event %}{{ render_leader(event) }} of the {% else %}The {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} completed the "{{ event["description"] }}" operation against the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "formed_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", SPAN,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} and {{ links[event["target_country"]] | safe }} are now allied in the  "{{ event["description"] }}" federation.\n',
    ),
    "founded_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} founded the "{{ event["federation_name"] }}" federation'
        '{% if "federation_partners" in event %} together with the {{ event["federation_partners"] | safe }}{% endif %}.\n',
    ),
    "joined_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }}'
        '{% if event["changed_role"] %} became {{ "an associate" if event["is_associate"] else "a full" }} member of'
        '{% else %} joined{% endif %} the "{{ event["federation_name"] }}" federation'
        '{% if event["is_associate"] and not event["changed_role"] %} as an associate member{% endif %}'
        '{% if event["federation_member_count"] %} ({{ event["federation_member_count"] }} members){% endif %}.\n',
    ),
    "left_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} left the "{{ event["federation_name"] }}" federation.\n',
    ),
    "dissolved_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        '\n'
        '                    The "{{ event["federation_name"] }}" federation of the {{ links[event["country"]] | safe }} was dissolved.\n',
    ),
    "defensive_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "shield", SPAN,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} entered a defensive pact with the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "non_aggression_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} entered a non-aggression pact with the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "commercial_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "coins", SPAN,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} entered a commercial pact with the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "research_agreement": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} entered a research agreement with the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "migration_treaty": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} entered a migration treaty with the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "embassy": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "building", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} have established an embassy in the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "closed_borders": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "barrier", SPAN,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} revoked border access to the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "received_closed_borders": EventDescriptor(
        Category.DIPLOMACY, Tier.TRIVIA, "barrier", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} are denied access to the {{ links[event["target_country"]] | safe }} territories.\n',
    ),
    "sent_rivalry": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "rivalry", SPAN,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} declared rivalry against the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "received_rivalry": EventDescriptor(
        Category.DIPLOMACY, Tier.TRIVIA, "rivalry", SPAN,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} received a rivalry declaration from the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "became_subject": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "chain", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} became a {{ event["description"] }} of the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "federation_president": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "federation", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} held the presidency of the {{ event["federation_title"] }}.\n',
    ),
    "federation_level_up": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "federation", POINT,
        '\n'
        '                    Under the presidency of the {{ links[event["country"]] | safe }}, the {{ event["federation_title"] }}'
        ' reached level {{ event["description"] }}.\n',
    ),
    "federation_renamed": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "federation", POINT,
        '\n'
        '                    Under the presidency of the {{ links[event["country"]] | safe }}, the "{{ event["old_federation_name"] }}" federation'
        ' was renamed "{{ event["federation_name"] }}".\n',
    ),
    "envoy_community": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", ENVOY,
        '\n'
        '                    The {{ links[event["country"]] | safe }} sent\n'
        '                    {{ render_envoys(event) }}\n'
        '                    to increase their diplomatic weight in the galactic community{{ render_assignment_count(event) }}.\n',
    ),
    "envoy_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", ENVOY,
        '\n'
        '                    The {{ links[event["country"]] | safe }} sent\n'
        '                    {{ render_envoys(event) }}\n'
        '                    to improve the cohesion of the {{ event["description"] }}{{ render_assignment_count(event) }}.\n',
    ),
    "envoy_improving_relations": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} sent\n'
        '                    {{ render_envoys(event) }}\n'
        '                    to improve relations with the {{ links[event["target_country"]] | safe }}{{ render_assignment_count(event) }}.\n',
    ),
    "envoy_harming_relations": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} sent\n'
        '                    {{ render_envoys(event) }}\n'
        '                    to harm relations with the {{ links[event["target_country"]] | safe }}{{ render_assignment_count(event) }}.\n',
    ),
    # ---- Galaxy ---------------------------------------------------------
    "joined_galactic_community": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} joined the Galactic Community.\n',
    ),
    "joined_galactic_council": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} joined the Galactic Council.\n',
    ),
    "left_galactic_community": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} left the Galactic Community.\n',
    ),
    "left_galactic_council": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} left the Galactic Council.\n',
    ),
    "galactic_resolution_passed": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "ballot", POINT,
        '\n'
        '                    The Galactic Community passed the resolution {{ event["description"] }}, proposed by the {{ links[event["country"]] | safe }}'
        '{% if "target_country" in event %}, concerning the {{ links[event["target_country"]] | safe }}{% endif %}.\n',
    ),
    "galactic_custodian": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "crown", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} held the office of {{ event["description"] }}.\n',
    ),
    "country_destroyed": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "skull", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} ceased to exist{% if event["description"] %} {{ event["description"] }}{% endif %}'
        '{% if "target_country" in event %} {{ links[event["target_country"]] | safe }}{% endif %}.\n',
    ),
    "voted_for_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    "voted_against_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    "galactic_crisis": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "crisis", POINT,
        '\n'
        '                    {% set phrases = ' + json.dumps(GALACTIC_CRISIS_PHRASES) + ' %}\n'
        '                    The {{ links[event["country"]] | safe }} {{ phrases.get(event["description"], event["description"]) }}.\n',
    ),
    # ---- Expansion ------------------------------------------------------
    "megastructure_construction": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "megastructure", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} {% if event["end_date"] %}built{% else %}began building{% endif %}'
        ' the {{ event["description"] }}{% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %}.\n',
    ),
    "planet_destroyed": EventDescriptor(
        Category.EXPANSION, Tier.HEADLINE, "explosion", POINT,
        '\n'
        '                    The planet {{ links[event["planet"]] | safe }} in the {{ links[event["system"]] | safe }} system, owned by the\n'
        '                    {{ links[event["country"]] | safe }} was destroyed.\n'
        '                    {% if event["planet"].planet_class is not none %}\n'
        '                        It is now {{ render_a_an(event["planet"].planetclass) }} {{ event["planet"].planetclass }}.\n'
        '                    {% else %}\n'
        '                        No trace remains.\n'
        '                    {% endif %}\n',
    ),
    "habitat_ringworld_construction": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "ringworld", POINT,
        '\n'
        '                    Finished construction of {{ event["description"] }} in the {{ links[event["system"]] | safe }} system\n'
        '                    {% if "leader" in event %},\n'
        '                        under the governorship of {{ render_leader(event) }}\n'
        '                    {% endif %}.\n',
    ),
    "colonization": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "planet", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} colonized {{ render_planet_and_class(event) }}\n'
        '                    in the {{ links[event["system"]] | safe }} system\n'
        '                    {% if "leader" in event%} under the governorship of {{ render_leader(event) }}{% endif %}.\n',
    ),
    "capital_relocation": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "capital", POINT,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} relocated their capital to {{ render_planet_and_class(event) }}\n'
        '                    {% if "system" in event %}in the {{ links[event["system"]] | safe }} system{% endif %}.\n',
    ),
    "terraforming": EventDescriptor(
        Category.EXPANSION, Tier.ROUTINE, "planet", SPAN,
        '\n'
        '                    {% if "leader" in event %} under the governorship of {{ render_leader(event) }}, the {% endif %}{{ links[event["country"]] | safe }}\n'
        '                    terraformed the planet {{ links[event["planet"]] | safe }} in the {{ links[event["system"]] | safe }} system\n'
        '                    {{ event["description"] }}.\n',
    ),
    "expanded_to_system": EventDescriptor(
        Category.EXPANSION, Tier.ROUTINE, "outpost", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} built an outpost in the {{ links[event["system"]] | safe }} system, claiming it for their empire.\n',
    ),
    "sector_creation": EventDescriptor(
        Category.EXPANSION, Tier.TRIVIA, "sector", POINT,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} created the {{ event["description"] }} sector{% if "planet" in event %}\n'
        '                    with capital on the planet {{ links[event["planet"]] | safe }}{% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %}\n'
        '                    {% endif %}.\n',
    ),
    "planetary_unrest": EventDescriptor(Category.EXPANSION, Tier.ROUTINE, "unrest"),
    # ---- Science --------------------------------------------------------
    "discovered_new_system": EventDescriptor(
        Category.SCIENCE, Tier.TRIVIA, "telescope", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} discovered the previously unknown {{ links[event["system"]] | safe }} system.\n',
    ),
    "researched_technology": EventDescriptor(
        Category.SCIENCE, Tier.ROUTINE, "flask", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe}} researched the "{{event["description"]}}" technology.\n',
    ),
    "pre_ftl_age_change": EventDescriptor(
        Category.SCIENCE, Tier.MAJOR, "flask", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} {{ event["description"] }}.\n',
    ),
    # ---- Exploration ----------------------------------------------------
    "discovered_anomaly": EventDescriptor(
        Category.EXPLORATION, Tier.ROUTINE, "search", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} discovered the "{{ event["description"] }}" anomaly on\n'
        '                    {{ render_planet_and_class(event) }}{% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %}.\n',
    ),
    "researched_anomaly": EventDescriptor(
        Category.EXPLORATION, Tier.ROUTINE, "microscope", POINT,
        '\n'
        '                    {% if "leader" in event %}{{ render_leader(event) }} of the {% else %}The {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} finished researching the "{{ event["description"] }}" anomaly on\n'
        '                    {{ render_planet_and_class(event) }}{% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %}.\n',
    ),
    "excavated_dig_site": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "shovel", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} completed an excavation at the "{{ event["description"] }}" archaeological site on\n'
        '                    {{ render_planet_and_class(event) }}{% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %}.\n',
    ),
    "explored_astral_rift": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "compass", POINT,
        '\n'
        '                    {% if "leader" in event %}{{ render_leader(event) }} of the {% else %}The {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} explored an astral rift ({{ event["description"] }})\n'
        '                    {% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %}.\n',
    ),
    "acquired_relic": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "gem", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} acquired the "{{ event["description"] }}" relic.\n',
    ),
    "destroyed_leviathan": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "skull", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} destroyed a leviathan{% if "target_country" in event %} guarded by the {{ links[event["target_country"]] | safe }}{% endif %}.\n',
    ),
    # ---- Statecraft -----------------------------------------------------
    "crisis_level_up": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "star", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} became a {{ event["description"] }}.\n',
    ),
    "ascension_perk": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "star", POINT,
        '\n'
        '                    {% if "leader" in event %}\n'
        '                    {{ render_leader(event) }} ascended the {{ links[event["country"]] | safe }} with "{{event["description"]}}".\n'
        '                    {% else %}\n'
        '                    The  {{ links[event["country"]] | safe }} ascended with "{{event["description"]}}".\n'
        '                    {% endif %}\n',
    ),
    "government_reform": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "gavel", POINT,
        '\n'
        '                    {% if "leader" in event %}\n'
        '                    {{ render_leader(event) }} reformed the {{ links[event["country"]] | safe }} government:\n'
        '                    {% else %}\n'
        '                    Government reforms in the {{ links[event["country"]] | safe}}:\n'
        '                    {% endif %}\n'
        '                    {{event["description"]}}\n',
    ),
    "empire_renamed": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "gavel", POINT,
        '\n'
        '                    {% if "leader" in event %}\n'
        '                    {{ render_leader(event) }} renamed the {{ links[event["country"]] | safe }}\n'
        '                    {% else %}\n'
        '                    The {{ links[event["country"]] | safe }} was renamed\n'
        '                    {% endif %}\n'
        '                    {{event["description"]}}.\n',
    ),
    "species_rights_reform": EventDescriptor(Category.STATECRAFT, Tier.MAJOR, "gavel"),
    "new_faction": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "faction", POINT,
        '\n'
        '                    {% if event["faction_type"] == event["faction"].rendered_name %}\n'
        '                        {% if not event["faction_type"].startswith("The ") %}The{%endif %} "{{ event["faction_type"] }}" faction\n'
        '                    {% else %}\n'
        '                        The {{ event["faction_type"] }} "{{ event["faction"].rendered_name }}"\n'
        '                    {%endif %}\n'
        '                    emerged in the {{ links[event["country"]] | safe}}.\n',
    ),
    "new_policy": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "policy", POINT,
        '\n'
        '                    {% if "leader" in event %}Under the rule of {{ render_leader(event) }}, the {% else %}The {% endif %}\n'
        '                    {{ links[event["country"]] | safe}} enacted a new policy on {{event["description"]}}.\n',
    ),
    "changed_policy": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "policy", POINT,
        '\n'
        '                    {% if "leader" in event %}Under the rule of {{ render_leader(event) }}, the {% else %}The {% endif %}\n'
        '                    {{ links[event["country"]] | safe}} changed their policy on {{event["description"]}}.\n',
    ),
    "tradition": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "tradition", POINT,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} adopted the "{{ event["description"]}}" tradition.\n',
    ),
    "agenda_launch": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "agenda", POINT,
        '\n'
        '                    {% if "leader" in event %}Under the rule of {{ render_leader(event) }}, the {% else %}The {% endif %}\n'
        '                    {{ links[event["country"]] | safe}} launched the agenda "{{event["description"]}}".\n',
    ),
    "edict": EventDescriptor(
        Category.STATECRAFT, Tier.TRIVIA, "edict", SPAN,
        '\n'
        '                    {% if "leader" in event %}{{ render_leader(event) }}{% else %}The {{ links[event["country"]] | safe}}{% endif %}\n'
        '                    enacted the "{{event["description"]}}" edict.\n',
    ),
    "agenda_preparation": EventDescriptor(
        Category.STATECRAFT, Tier.TRIVIA, "agenda", SPAN,
        '\n'
        '                    {% if "leader" in event %}Under the rule of {{ render_leader(event) }}, the {% else %}The {% endif %}\n'
        '                    {{ links[event["country"]] | safe}} prepared a new agenda "{{event["description"]}}".\n',
    ),
    # ---- Leaders --------------------------------------------------------
    "ruled_empire": EventDescriptor(
        Category.LEADERS, Tier.MAJOR, "crown", SPAN,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }} ruled the {{ links[event["country"]] | safe}}\n'
        '                    {% if "planet" in event %} from the capital {{ links[event["planet"]] | safe }}{% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %} {% endif %}.\n',
    ),
    "leader_died": EventDescriptor(
        Category.LEADERS, Tier.MAJOR, "skull", POINT,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    died or retired.\n',
    ),
    "leader_recruited": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "person", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} hired\n'
        '                    {{ render_leader(event) }}.\n',
    ),
    "councilor": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "council", SPAN,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    served as {{event["description"]}} on the council of the {{ links[event["country"]] | safe}}.\n',
    ),
    "gained_trait": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "dna", POINT,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    gained the "{{ event["description"] }}" trait.\n',
    ),
    "lost_trait": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "dna", POINT,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    lost the "{{ event["description"] }}" trait.\n',
    ),
    "leader_left_country": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "person", POINT,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    left the service of the {{ links[event["country"]] | safe }}.\n',
    ),
    "faction_leader": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "faction", SPAN,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    was leader of the "{{ event["faction"].rendered_name}}" faction.\n',
    ),
    "governed_sector": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "sector", SPAN,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    governed the {{event["description"]}} sector\n'
        '                    from the planet {{ links[event["planet"]] | safe }}\n'
        '                    in the {{ links[event["system"]] | safe }} system.\n',
    ),
    "governed_planet": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "planet", SPAN,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    governed the planet {{ links[event["planet"]] | safe }}\n'
        '                    in the {{ links[event["system"]] | safe }} system.\n',
    ),
    "fleet_command": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "fleet", SPAN,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    {% if event["fleet"].is_civilian_fleet %} commanded {% else %} led {% endif %} the\n'
        '                    {% if event["fleet"].is_civilian_fleet %} science ship {% else %} fleet {% endif %}\n'
        '                    "{{ event["fleet"].rendered_name }}".\n',
    ),
    "level_up": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "chevron-up", POINT,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    was promoted to rank {{event["description"]}}.\n',
    ),
    "leader_changed_ethic": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "dna", POINT,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }}\n'
        '                    changed their ethic to {{ event["description"] }}.\n',
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
_STORYLINE_SYSTEM = '{% if "system" in event %} in the {{ links[event["system"]] | safe }} system{% endif %}'
_STORYLINE_COUNTRY = '{% if "country" in event %} by the {{ links[event["country"]] | safe }}{% endif %}'

# StorylineType name -> StorylineDescriptor
STORYLINE_REGISTRY: dict[str, StorylineDescriptor] = {
    "dig_site": StorylineDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "shovel", "chapter",
        'Excavation of the "{{ event["title"] }}" archaeological site'
        '{% if "planet" in event %} on {{ render_planet_and_class(event) }}{% endif %}'
        + _STORYLINE_SYSTEM + _STORYLINE_COUNTRY + _STORYLINE_LEADER,
        {"active": "excavating", "paused": "paused", "completed": "fully excavated"},
    ),
    "astral_rift": StorylineDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "compass", "step",
        'Exploration of the astral rift "{{ event["title"] }}"'
        + _STORYLINE_SYSTEM + _STORYLINE_COUNTRY + _STORYLINE_LEADER,
        {"active": "exploring", "paused": "paused", "completed": "fully explored"},
    ),
    "first_contact": StorylineDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake", "stage",
        'First contact between the {{ links[event["country"]] | safe }}'
        ' and the {{ links[event["target_country"]] | safe }}'
        + _STORYLINE_SYSTEM + _STORYLINE_LEADER,
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
    text = " ".join(template.render(event=event, links=links).split())
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
