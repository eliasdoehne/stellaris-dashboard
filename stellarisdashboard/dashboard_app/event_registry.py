"""Data-driven descriptor registry for historical events (ledger rework Chunk F).

Replaces the ~50-branch ``if/elif`` chain that used to live in
``history_page.html``. Each ``HistoricalEventType`` maps to an
:class:`EventDescriptor` carrying:

* ``category`` / ``tier`` — the two editorial axes from
  ``scratch/ledger-rework/02-event-type-mapping.md`` (new, data-driven metadata;
  recorded here but **not yet surfaced in the UI** — that lands in Chunk H), and
* ``icon`` — a name for a future glyph (also not rendered yet), plus
* the bits needed to reproduce the old per-branch markup exactly: an optional
  ``li_class`` (defaults to the event type), a ``dateblock`` style, and the
  verbatim Jinja ``sentence`` the old branch used.

:func:`render_event` dispatches on ``event_type`` and renders one ``<li>`` of
ledger markup, so ``history_page.html`` collapses to a single loop. Rendering
goes through Flask's own Jinja environment with the same event dict, so the
output is text-equivalent to the previous chain.

Event types that the parser doesn't emit yet (and that had no branch) carry
``sentence=None``; :func:`render_event` returns empty markup for them, matching
the old chain's fall-through (no output).
"""
import enum
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


class Tier(enum.IntEnum):
    HEADLINE = 4
    MAJOR = 3
    ROUTINE = 2
    TRIVIA = 1


# Dateblock variants, lifted verbatim from the old template so the rendered date
# column is byte-for-byte the same.
POINT = "point"   # {{start_date}}:
SPAN = "span"     # (A) marker + optional " - end_date"
ENVOY = "envoy"   # span, but the (A) marker is commented out (TODO upstream)

_DATEBLOCKS = {
    POINT: '{{event["start_date"]}}:',
    SPAN: '{% if event["is_active"] %}(A){% endif %} {{event["start_date"]}}'
          '{% if event["end_date"] != null %} - {{event["end_date"]}}{%endif%}:',
    ENVOY: '<!-- TODO fix is_active post-leader-rework {% if event["is_active"] %}(A){% endif %} -->'
           '{{event["start_date"]}}{% if event["end_date"] != null %} - {{event["end_date"]}}{%endif%}:',
}

# Helper macros, verbatim from history_page.html, made available to every
# sentence template via a shared prefix.
_MACROS = """
{% macro render_leader(event, fallback="an unknown leader") -%}
    {% if "leader" in event %}
        {{ event["leader"].leader_class.capitalize() }} {{ links[event["leader"]] | safe }}
    {% else %}
        {{ fallback }}
    {% endif %}
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
    {% if text.startswith(("a", "e", "i", "o", "u")) %} an {% else %} a {% endif %}
{%- endmacro %}
"""


@dataclass(frozen=True)
class EventDescriptor:
    category: Category
    tier: Tier
    icon: str
    dateblock: Optional[str] = None
    sentence: Optional[str] = None
    li_class: Optional[str] = None  # defaults to the event type


# event_type -> EventDescriptor. Sentences are copied verbatim from the old
# per-branch markup. category/tier follow 02-event-type-mapping.md.
REGISTRY: dict[str, EventDescriptor] = {
    # ---- Warfare --------------------------------------------------------
    "war": EventDescriptor(
        Category.WARFARE, Tier.HEADLINE, "swords", POINT,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} entered the {{ links[event["war"]] | safe }} war{{ event["description"] }}{% if event["target_country"] %} {{ links[event["target_country"]] | safe}}{% endif %}.\n',
    ),
    "peace": EventDescriptor(
        Category.WARFARE, Tier.HEADLINE, "dove", POINT,
        '\n\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} made peace in the {{ links[event["war"]] | safe }} conflict.\n',
    ),
    "conquered_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        '\n'
        '                    The {{ links[event["country"]] | safe }} conquered the {{ links[event["system"]] | safe }} system from the\n'
        '                    {{ links[event["target_country"]] | safe }}{% if event["war"] %} during the {{ links[event["war"]] | safe }} {% endif %}.\n',
        li_class="gained_system",
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
    "formed_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", SPAN,
        '\n'
        '                    {% if "leader" in event %} Under the rule of {{ render_leader(event) }}, the\n'
        '                    {% else %} The\n'
        '                    {% endif %}\n'
        '                    {{ links[event["country"]] | safe }} and {{ links[event["target_country"]] | safe }} are now allied in the  "{{ event["description"] }}" federation.\n',
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
        li_class="established_embassy",
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
    "envoy_community": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", ENVOY,
        '\n'
        '                    The {{ links[event["country"]] | safe }} sent\n'
        '                    {{ render_leader(event) }}\n'
        '                    to increase their diplomatic weight in the galactic community.\n',
    ),
    "envoy_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", ENVOY,
        '\n'
        '                    The {{ links[event["country"]] | safe }} sent\n'
        '                    {{ render_leader(event) }}\n'
        '                    to improve the cohesion of the {{ event["description"] }}.\n',
    ),
    "envoy_improving_relations": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} sent\n'
        '                    {{ render_leader(event) }}\n'
        '                    to improve relations with the {{ links[event["target_country"]] | safe }}.\n',
    ),
    "envoy_harming_relations": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", SPAN,
        '\n'
        '                    The {{ links[event["country"]] | safe }} sent\n'
        '                    {{ render_leader(event) }}\n'
        '                    to harm relations with the {{ links[event["target_country"]] | safe }}.\n',
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
    "voted_for_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    "voted_against_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    # ---- Expansion ------------------------------------------------------
    "megastructure_construction": EventDescriptor(Category.EXPANSION, Tier.HEADLINE, "megastructure"),
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
        li_class="colonization",
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
        '                    in the {{ links[event["system"]] | safe }} system.\n',
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
        '                    with capital on the planet {{ links[event["planet"]] | safe }} in the {{ links[event["system"]] | safe }} system\n'
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
    # ---- Statecraft -----------------------------------------------------
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
        li_class="policy",
    ),
    "changed_policy": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "policy", POINT,
        '\n'
        '                    {% if "leader" in event %}Under the rule of {{ render_leader(event) }}, the {% else %}The {% endif %}\n'
        '                    {{ links[event["country"]] | safe}} changed their policy on {{event["description"]}}.\n',
        li_class="policy",
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
        li_class="agenda",
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
        li_class="agenda",
    ),
    # ---- Leaders --------------------------------------------------------
    "ruled_empire": EventDescriptor(
        Category.LEADERS, Tier.MAJOR, "crown", SPAN,
        '\n'
        '                    {{ render_leader(event, "An unknown leader") }} ruled the {{ links[event["country"]] | safe}}\n'
        '                    {% if "planet" in event %} from the capital {{ links[event["planet"]] | safe }} in the {{ links[event["system"]] | safe }} system {% endif %}.\n',
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
        li_class="leader_died",
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
        li_class="leader_left_country",
    ),
}


_COMPILED: dict[str, object] = {}


def _compiled(event_type: str):
    """Lazily compile (and cache) the full ``<li>`` template for an event type
    using Flask's own Jinja environment, so rendering matches the page exactly."""
    if event_type in _COMPILED:
        return _COMPILED[event_type]
    descriptor = REGISTRY.get(event_type)
    if descriptor is None or descriptor.sentence is None:
        _COMPILED[event_type] = None
        return None
    from stellarisdashboard.dashboard_app import flask_app

    li_class = descriptor.li_class or event_type
    source = (
        _MACROS
        + '<li class="eventitem ' + li_class + '">\n'
        + '    <div class="eventdescription">\n'
        + '        <span class="dateblock">' + _DATEBLOCKS[descriptor.dateblock] + "</span>\n"
        + '        <span class="eventtext">' + descriptor.sentence + "</span>\n"
        + "    </div>\n"
        + "</li>"
    )
    template = flask_app.jinja_env.from_string(source)
    _COMPILED[event_type] = template
    return template


def render_event(event, links) -> Markup:
    """Render one event's ``<li>`` of ledger markup, dispatching on
    ``event_type``. Returns empty markup for types with no template (matching
    the old chain's fall-through)."""
    template = _compiled(event.get("event_type"))
    if template is None:
        return Markup("")
    return Markup(template.render(event=event, links=links))
