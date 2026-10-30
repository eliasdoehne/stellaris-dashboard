"""Data-driven descriptor registry for historical events (ledger rework Chunk F).

Replaces the ~50-branch ``if/elif`` chain that used to live in
``history_page.html``. Each ``HistoricalEventType`` maps to an
:class:`EventDescriptor` carrying:

* ``category`` / ``tier`` — the two editorial axes (topic group and
  importance) of an event type (data-driven metadata;
  recorded here but **not yet surfaced in the UI** — that lands in Chunk H), and
* ``icon`` — a name for a future glyph (also not rendered yet), plus
* the bits needed to reproduce the old per-branch markup exactly: an optional
  ``li_class`` (defaults to the event type), a ``dateblock`` style, and the
  Jinja ``sentence`` of the event.

:func:`render_event` dispatches on ``event_type`` and renders one ``<li>`` of
ledger markup, so ``history_page.html`` collapses to a single loop. Rendering
goes through Flask's own Jinja environment with the same event dict, so the
output is text-equivalent to the previous chain.

Multi-stage arcs (:class:`~stellarisdashboard.datamodel.Storyline`) have a
:class:`StorylineDescriptor` in :data:`STORYLINE_REGISTRY`; :func:`render_storyline`
renders one as a single ``<li>`` with a ``<details>`` card that expands to its
stages, and :func:`render_event` delegates to it for ``event_type == "storyline"``.

Event types that the parser doesn't emit yet (and that had no branch) carry
``sentence=None``; :func:`render_event` returns empty markup for them, matching
the old chain's fall-through (no output).

Translation: every sentence is a *complete* translatable message with named
placeholders (see docs/translating.md), built by the helpers below (:func:`_t`,
:func:`_if`, :func:`_ruled`, ...) into the Jinja source of the sentence. Variants
of a sentence (with and without a leader, with and without a system, ...) are
separate messages, so translators can reorder words and decline names freely.
:func:`jinja_sources` hands the generated sources to the message extractor.
"""
import enum
import functools
import re
from dataclasses import dataclass
from typing import Optional

from markupsafe import Markup, escape

from stellarisdashboard import datamodel
from stellarisdashboard.i18n import N_, gettext as _, ngettext, pgettext


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


# ---------------------------------------------------------------------------
# Building blocks used inside the sentences. They are called from the Jinja
# templates, bound to the page's ``links`` (see _render_context).
# ---------------------------------------------------------------------------


def leader_class_label(leader_class: str) -> str:
    """The localised name of a leader class ("scientist" -> "Scientist")."""
    labels = {
        "scientist": pgettext("leader class", "Scientist"),
        "commander": pgettext("leader class", "Commander"),
        "admiral": pgettext("leader class", "Admiral"),
        "general": pgettext("leader class", "General"),
        "governor": pgettext("leader class", "Governor"),
        "official": pgettext("leader class", "Official"),
        "ruler": pgettext("leader class", "Ruler"),
        "envoy": pgettext("leader class", "Envoy"),
    }
    return labels.get(leader_class) or (leader_class or "").capitalize()


def _leader_with_class(leader, links) -> Markup:
    return Markup(
        # Translators: a leader as shown in the ledger: their class, then their name as a link,
        # e.g. "Scientist Jane Doe". %(leader_class)s is the class, e.g. "Scientist".
        pgettext(
            "leader with class",
            "%(leader_class)s %(leader)s",
            leader_class=escape(leader_class_label(leader.leader_class)),
            leader=Markup(links[leader]),
        )
    )


def render_leader(event, subject=False, *, links) -> Markup:
    """The event's leader with their class, or a placeholder if the event has none.

    The placeholder starts with a capital letter if the leader is the subject of the sentence."""
    if "leader" in event:
        return _leader_with_class(event["leader"], links)
    # Translators: stands for a leader that the ledger has no record of, at the start of a sentence
    # ("An unknown leader died or retired.")
    # and inside a sentence ("The X hired an unknown leader.")
    return Markup(escape(_("An unknown leader") if subject else _("an unknown leader")))


def render_envoys(event, *, links) -> Markup:
    """The envoys of a (merged) envoy assignment, e.g. "A, B and 2 other envoys"."""
    if "leaders" not in event:
        return render_leader(event, links=links)
    envoys = [_leader_with_class(leader, links) for leader in event["leaders"]]
    others = event["other_envoy_count"]
    if others > 0:
        return Markup(
            # Translators: %(envoys)s is a list of leaders, e.g. "Scientist A, Official B, Official C".
            # %(num)s is the number of further envoys.
            ngettext(
                "%(envoys)s and %(num)s other envoy",
                "%(envoys)s and %(num)s other envoys",
                others,
                envoys=Markup(datamodel.join_comma(envoys)),
            )
        )
    return Markup(datamodel.join_and(envoys))


def render_planet_and_class(event, *, links) -> Markup:
    """The event's planet with its class if known, or a placeholder if the event has none."""
    planet = event.get("planet")
    if planet is None:
        # Translators: stands for a planet that the ledger has no record of
        return Markup(escape(_("an unknown planet")))
    if planet.planet_class is None:
        return Markup(links[planet])
    return Markup(
        # Translators: a planet and its class, inserted into sentences, e.g. "the Gaia World Terra".
        # %(planet_class)s is the planet class, e.g. "Gaia World", %(planet)s the name of the planet (a link).
        _(
            "the %(planet_class)s %(planet)s",
            planet_class=escape(planet.planetclass),
            planet=Markup(links[planet]),
        )
    )


def render_war(event, kind="war", *, links) -> Markup:
    """The war's name; wars without a name in the game are described by their sides."""
    war = event["war"]
    link = Markup(links[war])
    if war.ingame_name:
        return link
    if kind == "conflict":
        # Translators: a war without a name in the game, e.g. "Empire A vs Empire B conflict".
        # %(war)s is the link to the war, named after its sides.
        return Markup(_("%(war)s conflict", war=link))
    # Translators: a war without a name in the game, e.g. "Empire A vs Empire B war".
    # %(war)s is the link to the war, named after its sides.
    return Markup(_("%(war)s war", war=link))


def _render_context(event, links) -> dict:
    return dict(
        event=event,
        links=links,
        render_leader=functools.partial(render_leader, links=links),
        render_envoys=functools.partial(render_envoys, links=links),
        render_planet_and_class=functools.partial(render_planet_and_class, links=links),
        render_war=functools.partial(render_war, links=links),
        crisis_phrases=GALACTIC_CRISIS_PHRASES,
    )


# ---------------------------------------------------------------------------
# Helpers that build the Jinja source of the sentences.
# ---------------------------------------------------------------------------

# Jinja expressions for the placeholders that most messages use; {ev} is the name of the
# dict that describes the event (``stage`` for the stages of a storyline).
_DEFAULT_ARGS = {
    "country": '{links}[{ev}["country"]] | safe',
    "target": '{links}[{ev}["target_country"]] | safe',
    "leader": "render_leader({ev})",
    "system": '{links}[{ev}["system"]] | safe',
    "planet": '{links}[{ev}["planet"]] | safe',
    "war": '{links}[{ev}["war"]] | safe',
}

# What the default placeholders mean, added to the notes for translators of every message using them.
_PLACEHOLDER_NOTES = {
    "country": '%(country)s: name of an empire (a link), usually reads like "United Nations of Earth"',
    "target": "%(target)s: name of another empire (a link), like %(country)s",
    "leader": '%(leader)s: class and name of a leader (a link), e.g. "Scientist Jane Doe"',
    "system": "%(system)s: name of a star system (a link)",
    "planet": "%(planet)s: name of a planet (a link)",
    "war": "%(war)s: name of a war (a link)",
}

# Placeholders given as plain text of the event
D = '{ev}["description"]'
LEADER_AS_SUBJECT = "render_leader({ev}, true)"
PLANET_AND_CLASS = "render_planet_and_class({ev})"

_PLACEHOLDER_RE = re.compile(r"%\((\w+)\)s")


def _jinja_string(text: str) -> str:
    # a Python string literal is also a valid Jinja string literal
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _call_args(message, overrides, ev, extra_names=()) -> str:
    args = []
    for name in dict.fromkeys(_PLACEHOLDER_RE.findall(message)):
        if name in extra_names:
            continue
        expression = overrides.get(name, _DEFAULT_ARGS.get(name))
        if expression is None:
            raise ValueError(f"No value for %({name})s in {message!r}")
        args.append(f"{name}=" + expression.format(ev=ev, links="links"))
    unused = set(overrides) - set(_PLACEHOLDER_RE.findall(message))
    if unused:
        raise ValueError(f"Unused values {unused} for {message!r}")
    return ", ".join(args)


def _comment(message, note) -> str:
    notes = [_PLACEHOLDER_NOTES[n] for n in dict.fromkeys(_PLACEHOLDER_RE.findall(message))
             if n in _PLACEHOLDER_NOTES]
    if note:
        notes.insert(0, note)
    if not notes:
        return ""
    return "{# Translators: " + ". ".join(notes) + " #}\n"


def _t(message: str, note: Optional[str] = None, *, ev: str = "event", **overrides) -> str:
    """Jinja source of one translatable message, filled with the event's values.

    Each %(name)s placeholder of the message gets the Jinja expression from ``overrides``
    or, for the common ones (country, target, ...), from _DEFAULT_ARGS."""
    args = _call_args(message, overrides, ev)
    call = _jinja_string(message) + (", " + args if args else "")
    return _comment(message, note) + "{{ _(" + call + ") }}\n"


def _n(singular: str, plural: str, count: str, note: Optional[str] = None, *, ev: str = "event", **overrides) -> str:
    """Jinja source of a message with a plural form. ``count`` is a Jinja expression;
    the message can use it as %(num)s."""
    args = _call_args(singular, overrides, ev, extra_names=("num",))
    call = _jinja_string(singular) + ", " + _jinja_string(plural) + ", " + count + (", " + args if args else "")
    return _comment(singular, note) + "{{ ngettext(" + call + ") }}\n"


def _if(condition: str, then: str, otherwise: str = "") -> str:
    source = "{% if " + condition + " %}\n" + then
    if otherwise:
        source += "{% else %}\n" + otherwise
    return source + "{% endif %}\n"


def _cases(*cases, default: str = "") -> str:
    """if/elif/else chain over (condition, content) pairs."""
    source = ""
    for index, (condition, content) in enumerate(cases):
        source += ("{% if " if index == 0 else "{% elif ") + condition + " %}\n" + content
    if default:
        source += "{% else %}\n" + default
    return source + "{% endif %}\n"


HAS_LEADER = '"leader" in event'


def _ruled(with_leader: str, without_leader: str, note: Optional[str] = None, **overrides) -> str:
    """Two variants of a sentence: whether the event has a leader (``%(leader)s``) or not."""
    overrides_without = {k: v for k, v in overrides.items() if f"%({k})s" in without_leader}
    return _if(
        HAS_LEADER,
        _t(with_leader, note, **overrides),
        _t(without_leader, note, **overrides_without),
    )


def _optional(conditions, build) -> str:
    """Nest {% if %} over optional parts: build(parts) gives the content for the parts that are present.

    conditions is a list of (name, Jinja condition); parts is the tuple of the names whose condition holds."""

    def nest(remaining, present):
        if not remaining:
            return build(present)
        name, condition = remaining[0]
        return _if(condition, nest(remaining[1:], present + (name,)), nest(remaining[1:], present))

    return nest(list(conditions), ())


# Marks an event that is still going on, before its start date
_ACTIVE_MARKER = (
    '{% if event["is_active"] %}'
    "{# Translators: marks an event or period that is still going on, shown before its start date #}\n"
    '{{ pgettext("ongoing event marker", "(A)") }}'
    "{% endif %}"
)

# Dateblock variants, lifted verbatim from the old template so the rendered date
# column is byte-for-byte the same.
POINT = "point"   # {{start_date}}:
SPAN = "span"     # (A) marker + optional " - end_date"
ENVOY = "envoy"   # span, but the (A) marker is commented out (TODO upstream)

_DATEBLOCKS = {
    POINT: '{{event["start_date"]}}:',
    SPAN: _ACTIVE_MARKER + ' {{event["start_date"]}}'
          '{% if event["end_date"] != null %} - {{event["end_date"]}}{%endif%}:',
    ENVOY: '<!-- TODO fix is_active post-leader-rework {% if event["is_active"] %}(A){% endif %} -->'
           '{{event["start_date"]}}{% if event["end_date"] != null %} - {{event["end_date"]}}{%endif%}:',
}


# galactic_crisis event description (see parsing/galaxy_events.py) -> what happened to its country, as a
# complete sentence. Marked with N_ and translated where it is displayed.
GALACTIC_CRISIS_PHRASES = {
    # Translators: %(country)s is the name of the empire (a link), usually reads like "United Nations of Earth"
    "unbidden_arrived": N_("The %(country)s came through a dimensional portal and invaded the galaxy, starting the end-game crisis."),
    "aberrant_arrived": N_("The %(country)s came through a second dimensional portal."),
    "vehement_arrived": N_("The %(country)s came through a third dimensional portal."),
    "unbidden_portal_destroyed": N_("The %(country)s lost their dimensional portal."),
    "aberrant_portal_destroyed": N_("The %(country)s lost their dimensional portal."),
    "vehement_portal_destroyed": N_("The %(country)s lost their dimensional portal."),
    "extradimensional_defeated": N_("The %(country)s were defeated, ending the extradimensional invasion."),
    "prethoryn_arrived": N_("The %(country)s arrived from beyond the galaxy, starting the end-game crisis."),
    "prethoryn_defeated": N_("The %(country)s were defeated, ending the Prethoryn invasion."),
    "contingency_activated": N_("The %(country)s awoke, starting the end-game crisis."),
    "contingency_defeated": N_("The %(country)s were defeated when their last machine world fell."),
    "synth_queen_arrived": N_("The %(country)s appeared, starting the end-game crisis."),
    "synth_queen_war": N_("The %(country)s declared war on the galaxy."),
    "synth_queen_victorious": N_("The %(country)s prevailed over the galaxy."),
    "synth_queen_defeated": N_("The %(country)s were defeated, ending the crisis."),
    "gray_tempest_arrived": N_("The %(country)s emerged as the Gray Tempest."),
    "gray_tempest_defeated": N_("The %(country)s were defeated, ending the Gray Tempest."),
    "voidworm_plague_started": N_("The %(country)s spread across the galaxy, starting the Voidworm Plague."),
    "voidworm_plague_ended": N_("The %(country)s receded, ending the Voidworm Plague."),
    "great_khan_rose": N_("The %(country)s united under the Great Khan and set out to conquer the galaxy."),
    "great_khan_defeated": N_("The %(country)s lost the Great Khan's armada in battle; the Khan fled to raise a new one."),
    "great_khan_died": N_("The %(country)s lost the Great Khan, who died, leaving the horde without its leader."),
    "war_in_heaven_started": N_("The %(country)s entered the War in Heaven."),
    "war_in_heaven_ended": N_("The %(country)s saw the War in Heaven end."),
    "crisis_stage_2": N_("The %(country)s spread, escalating the crisis to stage 2 of 4."),
    "crisis_stage_3": N_("The %(country)s spread further, escalating the crisis to stage 3 of 4."),
    "crisis_stage_4": N_("The %(country)s overran the galaxy, escalating the crisis to its final stage."),
    "declared_crisis": N_("The %(country)s were declared a crisis to the galaxy."),
    "fallen_empire_awakened": N_("The %(country)s awoke from their long slumber."),
    "fallen_empire_awakened_upstart": N_("The %(country)s awoke from their long slumber, alarmed by the rise of the younger empires."),
    "fallen_empire_awakened_guardian": N_("The %(country)s awoke from their long slumber to guard the galaxy against the end-game crisis."),
    "war_in_heaven_ruled_out": N_("The %(country)s awoke without an ancient rival, so no War in Heaven will follow."),
    "war_in_heaven_rival_chosen": N_("The %(country)s were chosen as the ancient rival of the awakened empire, fated to wake and contest it."),
    "ancient_rival_woke": N_("The %(country)s awoke as the ancient rival of the awakened empire, setting the War in Heaven in motion."),
    "nonaligned_league_formed": N_("The %(country)s founded the League of Non-Aligned Powers, to stay out of the War in Heaven."),
    "nonaligned_league_war": N_("The %(country)s led the League of Non-Aligned Powers into war against the awakened empires."),
    "fallen_empire_lost_war": N_("The %(country)s lost a war, shattering the myth of Fallen Empire invincibility."),
}


@dataclass(frozen=True)
class EventDescriptor:
    category: Category
    tier: Tier
    icon: str
    dateblock: Optional[str] = None
    sentence: Optional[str] = None
    li_class: Optional[str] = None  # defaults to the event type


# ---- Sentences with several variants ---------------------------------------------------

def _war_sentence() -> str:
    """The country entered a war; the sentence depends on how it was drawn into it."""
    note = "A war without a name in the game is named after its sides"

    def pair(with_leader, without_leader, **kwargs):
        return _ruled(with_leader, without_leader, note, war="render_war(event)", **kwargs)

    return (
        '{% set call_type = event.get("description_key") or "" %}\n'
        + _cases(
            ('call_type == "primary"', pair(
                "Under the rule of %(leader)s, the %(country)s entered the %(war)s as a primary party.",
                "The %(country)s entered the %(war)s as a primary party.",
            )),
            ('call_type == "overlord" and "target_country" in event', pair(
                "Under the rule of %(leader)s, the %(country)s entered the %(war)s, called by their overlord %(target)s.",
                "The %(country)s entered the %(war)s, called by their overlord %(target)s.",
            )),
            ('call_type == "offensive" and "target_country" in event', pair(
                "Under the rule of %(leader)s, the %(country)s entered the %(war)s attacking on the side of the %(target)s.",
                "The %(country)s entered the %(war)s attacking on the side of the %(target)s.",
            )),
            ('call_type == "defensive" and "target_country" in event', pair(
                "Under the rule of %(leader)s, the %(country)s entered the %(war)s due to their defensive pact with the %(target)s.",
                "The %(country)s entered the %(war)s due to their defensive pact with the %(target)s.",
            )),
            ('call_type == "alliance" and "target_country" in event', pair(
                "Under the rule of %(leader)s, the %(country)s entered the %(war)s due to their alliance with the %(target)s.",
                "The %(country)s entered the %(war)s due to their alliance with the %(target)s.",
            )),
            ('call_type not in ["", "overlord", "offensive", "defensive", "alliance"]', pair(
                "Under the rule of %(leader)s, the %(country)s entered the %(war)s (called as %(call_type)s).",
                "The %(country)s entered the %(war)s (called as %(call_type)s).",
                call_type="call_type",
            )),
            default=pair(
                "Under the rule of %(leader)s, the %(country)s entered the %(war)s.",
                "The %(country)s entered the %(war)s.",
            ),
        )
    )


def _combat_sentence(invasion: bool) -> str:
    """A space battle or an invasion: attackers won or lost against the defenders."""
    args = dict(
        attackers='event["attackers"] | safe',
        defenders='event["defenders"] | safe',
        attacker_exhaustion='event["attacker_exhaustion"]',
        defender_exhaustion='event["defender_exhaustion"]',
    )
    note = (
        "%(attackers)s and %(defenders)s are lists of empires (links). "
        "The exhaustion values are the war exhaustion of each side in percent, e.g. 12.5%"
    )
    if invasion:
        victory = (
            "Invasion of planet %(planet)s in the %(system)s system: "
            "Attackers %(attackers)s (%(attacker_exhaustion)s exhaustion) defeated "
            "%(defenders)s (exhaustion %(defender_exhaustion)s) in the %(war)s."
        )
        defeat = victory.replace(" defeated ", " were defeated by ")
    else:
        victory = (
            "Space battle in the %(system)s system: "
            "Attackers %(attackers)s (%(attacker_exhaustion)s exhaustion) defeated "
            "%(defenders)s (exhaustion %(defender_exhaustion)s) in the %(war)s."
        )
        defeat = victory.replace(" defeated ", " were defeated by ")
    return _if('event["attacker_victory"]', _t(victory, note, **args), _t(defeat, note, **args))


def _first_contact_process_sentence() -> str:
    """Whether a first contact began, was made or was abandoned, with or without leader and system."""
    def build(status_phrase):
        def build_parts(parts):
            with_system = "system" in parts
            tail = " in the %(system)s system." if with_system else "."
            return _ruled(
                "The %(country)s's %(leader)s " + status_phrase + " the %(target)s" + tail,
                "The %(country)s " + status_phrase + " the %(target)s" + tail,
            )

        return _optional([("system", '"system" in event')], build_parts)

    return _cases(
        ('event.get("contact_status") == "in_progress"', build("began first contact with")),
        ('event.get("contact_status") == "aborted"', build("abandoned first contact with")),
        default=build("made first contact with"),
    )


def _joined_federation_sentence() -> str:
    """Joining a federation, or changing the role in it, with the number of members if known."""
    federation = dict(federation='event["federation_name"]')

    def with_count(message_without_end):
        singular = message_without_end + " (%(num)s member)."
        plural = message_without_end + " (%(num)s members)."
        return _if(
            'event["federation_member_count"]',
            _n(singular, plural, 'event["federation_member_count"]', **federation),
            _t(message_without_end + ".", **federation),
        )

    return _cases(
        ('event["changed_role"] and event["is_associate"]',
         with_count('The %(country)s became an associate member of the "%(federation)s" federation')),
        ('event["changed_role"]',
         with_count('The %(country)s became a full member of the "%(federation)s" federation')),
        ('event["is_associate"]',
         with_count('The %(country)s joined the "%(federation)s" federation as an associate member')),
        default=with_count('The %(country)s joined the "%(federation)s" federation'),
    )


def _envoy_sentence(message: str, **overrides) -> str:
    """An envoy sentence, which also counts the assignments of a merged entry.

    ``message`` is the sentence ending in a period; it uses %(envoys)s for the envoys."""
    overrides = dict(envoys="render_envoys(event)", **overrides)
    note = (
        "%(envoys)s: the leaders sent, e.g. \"Official A, Official B and 2 other envoys\" "
        "(a list of links). %(num)s: how many times the leaders were assigned to this"
    )
    base = message[: -len(".")]
    return _if(
        '"assignment_count" in event',
        _n(base + " (%(num)s assignment).", base + " (%(num)s assignments).", 'event["assignment_count"]', note, **overrides),
        _t(message, note, **overrides),
    )


def _megastructure_sentence() -> str:
    mega = dict(megastructure=D)

    def build(parts):
        verb_built = "The %(country)s built the %(megastructure)s"
        verb_began = "The %(country)s began building the %(megastructure)s"
        tail = " in the %(system)s system." if "system" in parts else "."
        return _if(
            'event["end_date"]',
            _t(verb_built + tail, **mega),
            _t(verb_began + tail, **mega),
        )

    return _optional([("system", '"system" in event')], build)


def _planet_destroyed_sentence() -> str:
    return (
        _t("The planet %(planet)s in the %(system)s system, owned by the %(country)s was destroyed.")
        + _if(
            'event["planet"].planet_class is not none',
            _t(
                "It is now classified as %(planet_class)s.",
                planet_class='event["planet"].planetclass',
            ),
            _t("No trace remains."),
        )
    )


def _capital_relocation_sentence() -> str:
    def build(parts):
        tail = " in the %(system)s system." if "system" in parts else "."
        return _ruled(
            "Under the rule of %(leader)s, the %(country)s relocated their capital to %(planet)s" + tail,
            "The %(country)s relocated their capital to %(planet)s" + tail,
            planet=PLANET_AND_CLASS,
        )

    return _optional([("system", '"system" in event')], build)


def _sector_creation_sentence() -> str:
    sector = dict(sector=D)

    def build(parts):
        if "planet" not in parts:
            tail = "."
        elif "system" not in parts:
            tail = " with capital on the planet %(planet)s."
        else:
            tail = " with capital on the planet %(planet)s in the %(system)s system."
        return _ruled(
            "Under the rule of %(leader)s, the %(country)s created the %(sector)s sector" + tail,
            "The %(country)s created the %(sector)s sector" + tail,
            **sector,
        )

    return _optional([("planet", '"planet" in event'), ("system", '"system" in event')], build)


def _exploration_on_planet_sentence(with_leader: Optional[str], without_leader: str, **overrides) -> str:
    """A sentence ending in "on <planet>", followed by " in the <system> system" if there is a system.

    The messages given are the sentences without that ending."""

    def build(parts):
        tail = " in the %(system)s system." if "system" in parts else "."
        if with_leader is None:
            return _t(without_leader + tail, planet=PLANET_AND_CLASS, **overrides)
        return _ruled(
            with_leader + tail, without_leader + tail, planet=PLANET_AND_CLASS, **overrides
        )

    return _optional([("system", '"system" in event')], build)


def _new_faction_sentence() -> str:
    faction = dict(faction='event["faction"].rendered_name')
    note = '%(faction)s: name of the faction. %(faction_type)s: the kind of faction, e.g. "Supremacist"'
    return _cases(
        ('event["faction_type"] == event["faction"].rendered_name and not event["faction_type"].startswith("The ")',
         _t('The "%(faction)s" faction emerged in the %(country)s.', **faction)),
        ('event["faction_type"] == event["faction"].rendered_name',
         _t('"%(faction)s" faction emerged in the %(country)s.', **faction)),
        default=_t(
            'The %(faction_type)s "%(faction)s" emerged in the %(country)s.',
            note, faction_type='event["faction_type"]', **faction,
        ),
    )


def _ruled_empire_sentence() -> str:
    leader = dict(leader=LEADER_AS_SUBJECT)
    return _cases(
        ('"planet" in event and "system" in event',
         _t("%(leader)s ruled the %(country)s from the capital %(planet)s in the %(system)s system.", **leader)),
        ('"planet" in event',
         _t("%(leader)s ruled the %(country)s from the capital %(planet)s.", **leader)),
        default=_t("%(leader)s ruled the %(country)s.", **leader),
    )


def _country_destroyed_sentence() -> str:
    return _cases(
        ('event.get("cause") == "war" and "target_country" in event',
         _t("The %(country)s ceased to exist after losing a war against the %(target)s.")),
        ('event.get("cause") == "subject" and "target_country" in event',
         _t("The %(country)s ceased to exist after being integrated by the %(target)s.")),
        default=_t("The %(country)s ceased to exist."),
    )


def _galactic_crisis_sentence() -> str:
    return _if(
        'event.get("crisis_key") in crisis_phrases',
        # the message is picked from GALACTIC_CRISIS_PHRASES, where it is marked for translation
        "{{ _(crisis_phrases[event[\"crisis_key\"]], country=links[event[\"country\"]] | safe) }}\n",
        _t("The %(country)s %(description)s.", description=D),
    )


# event_type -> EventDescriptor. category is the topic group, tier the importance.
# The sentences are built with the helpers above: each message is a whole sentence.
REGISTRY: dict[str, EventDescriptor] = {
    # ---- Warfare --------------------------------------------------------
    "war": EventDescriptor(
        Category.WARFARE, Tier.HEADLINE, "swords", POINT,
        _war_sentence(),
    ),
    "peace": EventDescriptor(
        Category.WARFARE, Tier.HEADLINE, "dove", POINT,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s made peace in the %(war)s.",
            "The %(country)s made peace in the %(war)s.",
            war='render_war(event, "conflict")',
        ),
    ),
    "conquered_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        _if(
            'event["war"]',
            _t("The %(country)s conquered the %(system)s system from the %(target)s during the %(war)s."),
            _t("The %(country)s conquered the %(system)s system from the %(target)s."),
        ),
        li_class="gained_system",
    ),
    "founded_with_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        _t("The %(country)s was founded with the %(system)s system, formerly held by the %(target)s."),
        li_class="gained_system",
    ),
    "lost_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        _if(
            'event["target_country"]',
            _t("The %(country)s lost control of the %(system)s system to the %(target)s."),
            _t("The %(country)s lost control of the %(system)s system, leaving it unclaimed."),
        ),
    ),
    "fleet_combat": EventDescriptor(
        Category.WARFARE, Tier.ROUTINE, "crosshair", POINT,
        _combat_sentence(invasion=False),
    ),
    "army_combat": EventDescriptor(
        Category.WARFARE, Tier.ROUTINE, "crosshair", POINT,
        _combat_sentence(invasion=True),
    ),
    # ---- Diplomacy ------------------------------------------------------
    "first_contact": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake", POINT,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s established first contact with the %(target)s.",
            "The %(country)s established first contact with the %(target)s.",
        ),
    ),
    "first_contact_stage": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "handshake", POINT,
        _t(
            "The %(country)s's first contact with the %(target)s: %(stage)s.",
            "%(stage)s: what happened in the first contact, e.g. \"communications established (cautious)\"",
            stage=D,
        ),
    ),
    "first_contact_process": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake", SPAN,
        _first_contact_process_sentence(),
    ),
    "espionage_operation": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "eye", SPAN,
        _ruled(
            '%(leader)s of the %(country)s ran the "%(operation)s" operation against the %(target)s.',
            'The %(country)s ran the "%(operation)s" operation against the %(target)s.',
            operation=D,
        ),
    ),
    "completed_espionage_operation": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "key", POINT,
        _ruled(
            '%(leader)s of the %(country)s completed the "%(operation)s" operation against the %(target)s.',
            'The %(country)s completed the "%(operation)s" operation against the %(target)s.',
            operation=D,
        ),
    ),
    "formed_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", SPAN,
        _ruled(
            'Under the rule of %(leader)s, the %(country)s and %(target)s are now allied in the "%(federation)s" federation.',
            'The %(country)s and %(target)s are now allied in the "%(federation)s" federation.',
            federation=D,
        ),
    ),
    "founded_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        _if(
            '"federation_partners" in event',
            _t(
                'The %(country)s founded the "%(federation)s" federation together with the %(partners)s.',
                "%(partners)s: the other founding members, e.g. \"A, B and C\" (links)",
                federation='event["federation_name"]',
                partners='event["federation_partners"] | safe',
            ),
            _t('The %(country)s founded the "%(federation)s" federation.', federation='event["federation_name"]'),
        ),
    ),
    "joined_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        _joined_federation_sentence(),
    ),
    "left_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        _t('The %(country)s left the "%(federation)s" federation.', federation='event["federation_name"]'),
    ),
    "dissolved_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        _t('The "%(federation)s" federation of the %(country)s was dissolved.', federation='event["federation_name"]'),
    ),
    "defensive_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "shield", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s entered a defensive pact with the %(target)s.",
            "The %(country)s entered a defensive pact with the %(target)s.",
        ),
    ),
    "non_aggression_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s entered a non-aggression pact with the %(target)s.",
            "The %(country)s entered a non-aggression pact with the %(target)s.",
        ),
    ),
    "commercial_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "coins", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s entered a commercial pact with the %(target)s.",
            "The %(country)s entered a commercial pact with the %(target)s.",
        ),
    ),
    "research_agreement": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s entered a research agreement with the %(target)s.",
            "The %(country)s entered a research agreement with the %(target)s.",
        ),
    ),
    "migration_treaty": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s entered a migration treaty with the %(target)s.",
            "The %(country)s entered a migration treaty with the %(target)s.",
        ),
    ),
    "embassy": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "building", SPAN,
        _t("The %(country)s have established an embassy in the %(target)s."),
        li_class="established_embassy",
    ),
    "closed_borders": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "barrier", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s revoked border access to the %(target)s.",
            "The %(country)s revoked border access to the %(target)s.",
        ),
    ),
    "received_closed_borders": EventDescriptor(
        Category.DIPLOMACY, Tier.TRIVIA, "barrier", SPAN,
        _t("The %(country)s are denied access to the %(target)s territories."),
    ),
    "sent_rivalry": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "rivalry", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s declared rivalry against the %(target)s.",
            "The %(country)s declared rivalry against the %(target)s.",
        ),
    ),
    "received_rivalry": EventDescriptor(
        Category.DIPLOMACY, Tier.TRIVIA, "rivalry", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s received a rivalry declaration from the %(target)s.",
            "The %(country)s received a rivalry declaration from the %(target)s.",
        ),
    ),
    "became_subject": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "chain", SPAN,
        _t(
            "The %(country)s became a %(subject_type)s of the %(target)s.",
            "%(subject_type)s: the kind of subject, e.g. \"Vassal\"",
            subject_type=D,
        ),
    ),
    "federation_president": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "federation", SPAN,
        _t(
            "The %(country)s held the presidency of the %(federation)s.",
            "%(federation)s: the type and name of the federation, e.g. Martial Alliance \"Star Security Organization\"",
            federation='event["federation_title"]',
        ),
    ),
    "federation_level_up": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "federation", POINT,
        _t(
            "Under the presidency of the %(country)s, the %(federation)s reached level %(level)s.",
            "%(federation)s: the type and name of the federation, e.g. Martial Alliance \"Star Security Organization\"",
            federation='event["federation_title"]',
            level=D,
        ),
    ),
    "federation_renamed": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "federation", POINT,
        _t(
            'Under the presidency of the %(country)s, the "%(old_name)s" federation was renamed "%(new_name)s".',
            old_name='event["old_federation_name"]',
            new_name='event["federation_name"]',
        ),
    ),
    "envoy_community": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", ENVOY,
        _envoy_sentence("The %(country)s sent %(envoys)s to increase their diplomatic weight in the galactic community."),
    ),
    "envoy_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", ENVOY,
        _envoy_sentence("The %(country)s sent %(envoys)s to improve the cohesion of the %(federation)s.", federation=D),
    ),
    "envoy_improving_relations": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", SPAN,
        _envoy_sentence("The %(country)s sent %(envoys)s to improve relations with the %(target)s."),
    ),
    "envoy_harming_relations": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "envoy", SPAN,
        _envoy_sentence("The %(country)s sent %(envoys)s to harm relations with the %(target)s."),
    ),
    # ---- Galaxy ---------------------------------------------------------
    "joined_galactic_community": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s joined the Galactic Community.",
            "The %(country)s joined the Galactic Community.",
        ),
    ),
    "joined_galactic_council": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s joined the Galactic Council.",
            "The %(country)s joined the Galactic Council.",
        ),
    ),
    "left_galactic_community": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s left the Galactic Community.",
            "The %(country)s left the Galactic Community.",
        ),
    ),
    "left_galactic_council": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s left the Galactic Council.",
            "The %(country)s left the Galactic Council.",
        ),
    ),
    "galactic_resolution_passed": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "ballot", POINT,
        _if(
            '"target_country" in event',
            _t(
                "The Galactic Community passed the resolution %(resolution)s, proposed by the %(country)s, concerning the %(target)s.",
                "%(resolution)s: the name of the resolution, in quotation marks",
                resolution=D,
            ),
            _t(
                "The Galactic Community passed the resolution %(resolution)s, proposed by the %(country)s.",
                "%(resolution)s: the name of the resolution, in quotation marks",
                resolution=D,
            ),
        ),
    ),
    "galactic_custodian": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "crown", SPAN,
        _t(
            "The %(country)s held the office of %(office)s.",
            "%(office)s: the office in the Galactic Community, e.g. \"Galactic Custodian\"",
            office=D,
        ),
    ),
    "country_destroyed": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "skull", POINT,
        _country_destroyed_sentence(),
    ),
    "voted_for_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    "voted_against_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    "galactic_crisis": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "crisis", POINT,
        _galactic_crisis_sentence(),
    ),
    # ---- Expansion ------------------------------------------------------
    "megastructure_construction": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "megastructure", SPAN,
        _megastructure_sentence(),
    ),
    "planet_destroyed": EventDescriptor(
        Category.EXPANSION, Tier.HEADLINE, "explosion", POINT,
        _planet_destroyed_sentence(),
        li_class="colonization",
    ),
    "habitat_ringworld_construction": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "ringworld", POINT,
        _ruled(
            "Finished construction of %(structure)s in the %(system)s system under the governorship of %(leader)s.",
            "Finished construction of %(structure)s in the %(system)s system.",
            structure=D,
        ),
    ),
    "colonization": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "planet", SPAN,
        _ruled(
            "The %(country)s colonized %(planet)s in the %(system)s system under the governorship of %(leader)s.",
            "The %(country)s colonized %(planet)s in the %(system)s system.",
            planet=PLANET_AND_CLASS,
        ),
    ),
    "capital_relocation": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "capital", POINT,
        _capital_relocation_sentence(),
    ),
    "terraforming": EventDescriptor(
        Category.EXPANSION, Tier.ROUTINE, "planet", SPAN,
        _ruled(
            "Under the governorship of %(leader)s, the %(country)s terraformed the planet %(planet)s in the %(system)s system from %(old_class)s to %(new_class)s.",
            "The %(country)s terraformed the planet %(planet)s in the %(system)s system from %(old_class)s to %(new_class)s.",
            "%(old_class)s and %(new_class)s are planet classes, e.g. \"Barren World\"",
            old_class='event["old_class"]',
            new_class='event["new_class"]',
        ),
    ),
    "expanded_to_system": EventDescriptor(
        Category.EXPANSION, Tier.ROUTINE, "outpost", POINT,
        _t("The %(country)s built an outpost in the %(system)s system, claiming it for their empire."),
    ),
    "sector_creation": EventDescriptor(
        Category.EXPANSION, Tier.TRIVIA, "sector", POINT,
        _sector_creation_sentence(),
    ),
    "planetary_unrest": EventDescriptor(Category.EXPANSION, Tier.ROUTINE, "unrest"),
    # ---- Science --------------------------------------------------------
    "discovered_new_system": EventDescriptor(
        Category.SCIENCE, Tier.TRIVIA, "telescope", POINT,
        _t("The %(country)s discovered the previously unknown %(system)s system."),
    ),
    "researched_technology": EventDescriptor(
        Category.SCIENCE, Tier.ROUTINE, "flask", SPAN,
        _t('The %(country)s researched the "%(technology)s" technology.', technology=D),
    ),
    "pre_ftl_age_change": EventDescriptor(
        Category.SCIENCE, Tier.MAJOR, "flask", POINT,
        _if(
            'event["regressed"]',
            _t(
                "The %(country)s fell back from the %(old_age)s to the %(new_age)s.",
                "%(old_age)s and %(new_age)s are technology ages of a primitive civilisation, e.g. \"Steam Age\"",
                old_age='event["old_age"]', new_age='event["new_age"]',
            ),
            _t(
                "The %(country)s advanced from the %(old_age)s to the %(new_age)s.",
                "%(old_age)s and %(new_age)s are technology ages of a primitive civilisation, e.g. \"Steam Age\"",
                old_age='event["old_age"]', new_age='event["new_age"]',
            ),
        ),
    ),
    # ---- Exploration ----------------------------------------------------
    "discovered_anomaly": EventDescriptor(
        Category.EXPLORATION, Tier.ROUTINE, "search", POINT,
        _exploration_on_planet_sentence(
            None, 'The %(country)s discovered the "%(anomaly)s" anomaly on %(planet)s', anomaly=D
        ),
    ),
    "researched_anomaly": EventDescriptor(
        Category.EXPLORATION, Tier.ROUTINE, "microscope", POINT,
        _exploration_on_planet_sentence(
            '%(leader)s of the %(country)s finished researching the "%(anomaly)s" anomaly on %(planet)s',
            'The %(country)s finished researching the "%(anomaly)s" anomaly on %(planet)s',
            anomaly=D,
        ),
    ),
    "excavated_dig_site": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "shovel", POINT,
        _exploration_on_planet_sentence(
            None, 'The %(country)s completed an excavation at the "%(site)s" archaeological site on %(planet)s', site=D
        ),
    ),
    "explored_astral_rift": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "compass", POINT,
        _optional(
            [("system", '"system" in event')],
            lambda parts: _ruled(
                "%(leader)s of the %(country)s explored an astral rift (%(rift)s)"
                + (" in the %(system)s system." if parts else "."),
                "The %(country)s explored an astral rift (%(rift)s)"
                + (" in the %(system)s system." if parts else "."),
                rift=D,
            ),
        ),
    ),
    "acquired_relic": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "gem", POINT,
        _t('The %(country)s acquired the "%(relic)s" relic.', relic=D),
    ),
    "destroyed_leviathan": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "skull", POINT,
        _if(
            '"target_country" in event',
            _t("The %(country)s destroyed a leviathan guarded by the %(target)s."),
            _t("The %(country)s destroyed a leviathan."),
        ),
    ),
    # ---- Statecraft -----------------------------------------------------
    "crisis_level_up": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "star", POINT,
        _t(
            "The %(country)s became a %(threat)s.",
            "%(threat)s: the crisis level reached, a phrase like \"Risk\"-level threat to the galaxy",
            threat=D,
        ),
    ),
    "ascension_perk": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "star", POINT,
        _ruled(
            '%(leader)s ascended the %(country)s with "%(perk)s".',
            'The %(country)s ascended with "%(perk)s".',
            perk=D,
        ),
    ),
    "government_reform": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "gavel", POINT,
        _ruled(
            "%(leader)s reformed the %(country)s government: %(reforms)s",
            "Government reforms in the %(country)s: %(reforms)s",
            "%(reforms)s: sentences describing the changes of the government, "
            "e.g. \"Adopted civics A, B. Embraced ethics C.\"",
            reforms=D,
        ),
    ),
    "empire_renamed": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "gavel", POINT,
        _ruled(
            "%(leader)s renamed the %(country)s from %(old_name)s to %(new_name)s.",
            "The %(country)s was renamed from %(old_name)s to %(new_name)s.",
            "%(old_name)s and %(new_name)s are the names of the empire",
            old_name='event["old_name"]',
            new_name='event["new_name"]',
        ),
    ),
    "species_rights_reform": EventDescriptor(Category.STATECRAFT, Tier.MAJOR, "gavel"),
    "new_faction": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "faction", POINT,
        _new_faction_sentence(),
    ),
    "new_policy": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "policy", POINT,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s enacted a new policy on '%(policy)s': '%(selected)s'.",
            "The %(country)s enacted a new policy on '%(policy)s': '%(selected)s'.",
            "%(policy)s: the area of the policy, %(selected)s: the option chosen",
            policy='event["policy"]', selected='event["selected"]',
        ),
        li_class="policy",
    ),
    "changed_policy": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "policy", POINT,
        _ruled(
            "Under the rule of %(leader)s, the %(country)s changed their policy on '%(policy)s' from '%(old_choice)s' to '%(new_choice)s'.",
            "The %(country)s changed their policy on '%(policy)s' from '%(old_choice)s' to '%(new_choice)s'.",
            "%(policy)s: the area of the policy, %(old_choice)s and %(new_choice)s: the options",
            policy='event["policy"]', old_choice='event["old_choice"]', new_choice='event["new_choice"]',
        ),
        li_class="policy",
    ),
    "tradition": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "tradition", POINT,
        _ruled(
            'Under the rule of %(leader)s, the %(country)s adopted the "%(tradition)s" tradition.',
            'The %(country)s adopted the "%(tradition)s" tradition.',
            tradition=D,
        ),
    ),
    "agenda_launch": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "agenda", POINT,
        _ruled(
            'Under the rule of %(leader)s, the %(country)s launched the agenda "%(agenda)s".',
            'The %(country)s launched the agenda "%(agenda)s".',
            agenda=D,
        ),
        li_class="agenda",
    ),
    "edict": EventDescriptor(
        Category.STATECRAFT, Tier.TRIVIA, "edict", SPAN,
        _ruled(
            '%(leader)s enacted the "%(edict)s" edict.',
            'The %(country)s enacted the "%(edict)s" edict.',
            edict=D,
        ),
    ),
    "agenda_preparation": EventDescriptor(
        Category.STATECRAFT, Tier.TRIVIA, "agenda", SPAN,
        _ruled(
            'Under the rule of %(leader)s, the %(country)s prepared a new agenda "%(agenda)s".',
            'The %(country)s prepared a new agenda "%(agenda)s".',
            agenda=D,
        ),
        li_class="agenda",
    ),
    # ---- Leaders --------------------------------------------------------
    "ruled_empire": EventDescriptor(
        Category.LEADERS, Tier.MAJOR, "crown", SPAN,
        _ruled_empire_sentence(),
    ),
    "leader_died": EventDescriptor(
        Category.LEADERS, Tier.MAJOR, "skull", POINT,
        _t("%(leader)s died or retired.", leader=LEADER_AS_SUBJECT),
    ),
    "leader_recruited": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "person", POINT,
        _t("The %(country)s hired %(leader)s."),
    ),
    "councilor": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "council", SPAN,
        _t(
            "%(leader)s served as %(position)s on the council of the %(country)s.",
            "%(position)s: the council position, e.g. \"Chancellor\"",
            leader=LEADER_AS_SUBJECT, position=D,
        ),
    ),
    "gained_trait": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "dna", POINT,
        _t('%(leader)s gained the "%(trait)s" trait.', leader=LEADER_AS_SUBJECT, trait=D),
    ),
    "lost_trait": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "dna", POINT,
        _t('%(leader)s lost the "%(trait)s" trait.', leader=LEADER_AS_SUBJECT, trait=D),
    ),
    "leader_left_country": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "person", POINT,
        _t("%(leader)s left the service of the %(country)s.", leader=LEADER_AS_SUBJECT),
    ),
    "faction_leader": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "faction", SPAN,
        _t(
            '%(leader)s was leader of the "%(faction)s" faction.',
            leader=LEADER_AS_SUBJECT, faction='event["faction"].rendered_name',
        ),
    ),
    "governed_sector": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "sector", SPAN,
        _t(
            "%(leader)s governed the %(sector)s sector from the planet %(planet)s in the %(system)s system.",
            "%(sector)s: the name of the sector",
            leader=LEADER_AS_SUBJECT, sector=D,
        ),
    ),
    "governed_planet": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "planet", SPAN,
        _t(
            "%(leader)s governed the planet %(planet)s in the %(system)s system.",
            leader=LEADER_AS_SUBJECT,
        ),
    ),
    "fleet_command": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "fleet", SPAN,
        _if(
            'event["fleet"].is_civilian_fleet',
            _t('%(leader)s commanded the science ship "%(fleet)s".',
               leader=LEADER_AS_SUBJECT, fleet='event["fleet"].rendered_name'),
            _t('%(leader)s led the fleet "%(fleet)s".',
               leader=LEADER_AS_SUBJECT, fleet='event["fleet"].rendered_name'),
        ),
        li_class="leader_died",
    ),
    "level_up": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "chevron-up", POINT,
        _t("%(leader)s was promoted to rank %(rank)s.", leader=LEADER_AS_SUBJECT, rank=D),
    ),
    "leader_changed_ethic": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "dna", POINT,
        _t("%(leader)s changed their ethic to %(ethic)s.", leader=LEADER_AS_SUBJECT, ethic=D),
        li_class="leader_left_country",
    ),
}


@dataclass(frozen=True)
class StorylineDescriptor:
    category: Category
    tier: Tier
    icon: str
    sentence: str  # Jinja: the arc's title; the storyline item dict is named ``event``
    stage_row: str  # Jinja: one stage of the arc; the stage dict is named ``stage``
    stage_count: str  # Jinja: how many stages the arc has reached, ``event["stage_count"]``
    state_labels: dict[str, str]  # marked with N_, translated when rendered


_STORYLINE_LEADER = '{% if "leader" in event %} ({{ render_leader(event) | trim }}){% endif %}'
_STAGE_LEADER = '{% if "leader" in stage %} ({{ render_leader(stage) | trim }}){% endif %}'


def _storyline_title(base: str, optional, **overrides) -> str:
    """The title of an arc: ``base`` followed by the optional parts that its item has.

    ``optional`` is a list of (name, Jinja condition, English text, placeholder values)."""

    def build(parts):
        message = base
        values = dict(overrides)
        for name, _condition, text, extra in optional:
            if name in parts:
                message += text
                values.update(extra)
        return _t(message, **{k: v for k, v in values.items() if f"%({k})s" in message})

    return _optional([(name, condition) for name, condition, _text, _extra in optional], build) + _STORYLINE_LEADER


_IN_SYSTEM = ("system", '"system" in event', " in the %(system)s system", {})
_BY_COUNTRY = ("country", '"country" in event', " by the %(country)s", {})


def _stage_row(label: str, label_with_detail: str, label_by_country: str, label_with_detail_by_country: str) -> str:
    """A stage of an arc: its number, what happened, and the country if it differs from the arc's."""
    messages = {
        (): label,
        ("detail",): label_with_detail,
        ("country",): label_by_country,
        ("detail", "country"): label_with_detail_by_country,
    }

    def build(parts):
        message = messages[parts]
        return _t(
            message,
            ev="stage",
            **{k: v for k, v in dict(index='stage["index"]', detail='stage["detail"]').items() if f"%({k})s" in message},
        )

    return _optional([("detail", '"detail" in stage'), ("country", '"country" in stage')], build) + _STAGE_LEADER


# StorylineType name -> StorylineDescriptor
STORYLINE_REGISTRY: dict[str, StorylineDescriptor] = {
    "dig_site": StorylineDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "shovel",
        _storyline_title(
            'Excavation of the "%(site)s" archaeological site',
            [("planet", '"planet" in event', " on %(planet)s", dict(planet=PLANET_AND_CLASS)), _IN_SYSTEM, _BY_COUNTRY],
            site='event["title"]',
        ),
        _stage_row(
            "Chapter %(index)s",
            "Chapter %(index)s: %(detail)s",
            "Chapter %(index)s by the %(country)s",
            "Chapter %(index)s: %(detail)s by the %(country)s",
        ),
        _n("%(num)s chapter", "%(num)s chapters", 'event["stage_count"]'),
        {"active": N_("excavating"), "paused": N_("paused"), "completed": N_("fully excavated")},
    ),
    "astral_rift": StorylineDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "compass",
        _storyline_title(
            'Exploration of the astral rift "%(rift)s"',
            [_IN_SYSTEM, _BY_COUNTRY],
            rift='event["title"]',
        ),
        _stage_row(
            "Step %(index)s",
            "Step %(index)s: %(detail)s",
            "Step %(index)s by the %(country)s",
            "Step %(index)s: %(detail)s by the %(country)s",
        ),
        _n("%(num)s step", "%(num)s steps", 'event["stage_count"]'),
        {"active": N_("exploring"), "paused": N_("paused"), "completed": N_("fully explored")},
    ),
    "first_contact": StorylineDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake",
        _storyline_title(
            "First contact between the %(country)s and the %(target)s",
            [_IN_SYSTEM],
        ),
        _stage_row(
            "Stage %(index)s",
            "Stage %(index)s: %(detail)s",
            "Stage %(index)s by the %(country)s",
            "Stage %(index)s: %(detail)s by the %(country)s",
        ),
        _n("%(num)s stage", "%(num)s stages", 'event["stage_count"]'),
        {"active": N_("in progress"), "completed": N_("contact made"), "abandoned": N_("abandoned")},
    ),
}

_COMPILED: dict[str, object] = {}
_COMPILED_STORYLINES: dict[str, object] = {}


def _event_source(event_type: str, descriptor: EventDescriptor) -> str:
    li_class = descriptor.li_class or event_type
    return (
        '<li class="eventitem ' + li_class + '">\n'
        + '    <div class="eventdescription">\n'
        + '        <span class="dateblock">' + _DATEBLOCKS[descriptor.dateblock] + "</span>\n"
        + '        <span class="eventtext">' + descriptor.sentence + "</span>\n"
        + "    </div>\n"
        + "</li>"
    )


def _storyline_source(descriptor: StorylineDescriptor) -> str:
    head = (
        '<span class="dateblock">' + _DATEBLOCKS[SPAN] + "</span>\n"
        + '<span class="eventtext">' + descriptor.sentence
        + '<span class="storyline_meta"> <span class="storyline_state state_{{ event["state"] }}">{{ state_label }}</span>'
        + '{% if event["stage_count"] %} ' + descriptor.stage_count + "{% endif %}"
        + "</span></span>\n"
    )
    return (
        '<li class="eventitem storyline {{ event["storyline_type"] }}_storyline">\n'
        + '{% if event["stages"] %}'
        + '<details class="storyline_card"><summary class="storyline_summary eventdescription">\n'
        + head
        + "</summary>\n"
        + '<ul class="event_list storyline_stages">'
        + '{% for stage in event["stages"] %}'
        + '<li class="eventitem storyline_stage"><span class="dateblock">{{ stage["start_date"] }}:</span> '
        + '<span class="eventtext">' + descriptor.stage_row + "</span></li>"
        + "{% endfor %}"
        + "</ul></details>\n"
        + "{% else %}"
        + '<div class="eventdescription">\n' + head + "</div>\n"
        + "{% endif %}"
        + "</li>"
    )


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

    template = flask_app.jinja_env.from_string(_event_source(event_type, descriptor))
    _COMPILED[event_type] = template
    return template


def _compiled_storyline(storyline_type: str):
    """Lazily compile (and cache) the card template for a storyline type."""
    if storyline_type in _COMPILED_STORYLINES:
        return _COMPILED_STORYLINES[storyline_type]
    descriptor = STORYLINE_REGISTRY.get(storyline_type)
    if descriptor is None:
        _COMPILED_STORYLINES[storyline_type] = None
        return None
    from stellarisdashboard.dashboard_app import flask_app

    template = flask_app.jinja_env.from_string(_storyline_source(descriptor))
    _COMPILED_STORYLINES[storyline_type] = template
    return template


def jinja_sources():
    """(name, source) of every Jinja template kept in this module, for extracting
    their translatable messages (see stellarisdashboard/i18n/extract.py)."""
    for name, source in _DATEBLOCKS.items():
        yield f"_DATEBLOCKS[{name}]", source
    for event_type, descriptor in REGISTRY.items():
        if descriptor.sentence is not None:
            yield f"REGISTRY[{event_type}]", _event_source(event_type, descriptor)
    for storyline_type, descriptor in STORYLINE_REGISTRY.items():
        yield f"STORYLINE_REGISTRY[{storyline_type}]", _storyline_source(descriptor)


def render_storyline(event, links) -> Markup:
    """Render one storyline item as a ``<li>`` card that expands to its stages."""
    storyline_type = event.get("storyline_type")
    template = _compiled_storyline(storyline_type)
    if template is None:
        return Markup("")
    descriptor = STORYLINE_REGISTRY[storyline_type]
    state = event.get("state")
    label = descriptor.state_labels.get(state)
    return Markup(
        template.render(
            state_label=_(label) if label else state,
            **_render_context(event, links),
        )
    )


def render_event(event, links) -> Markup:
    """Render one event's ``<li>`` of ledger markup, dispatching on
    ``event_type``. Returns empty markup for types with no template (matching
    the old chain's fall-through)."""
    if event.get("event_type") == "storyline":
        return render_storyline(event, links)
    template = _compiled(event.get("event_type"))
    if template is None:
        return Markup("")
    return Markup(template.render(**_render_context(event, links)))
