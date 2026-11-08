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

Translation: every sentence is a *complete* translatable message with named
placeholders (see docs/translating.md), built by the helpers below (:func:`_t`,
:func:`_sentence`, :func:`_ruled`, ...) into the Jinja source of the sentence.
Variants of a sentence (with and without a leader, with and without a system, ...)
are separate messages, so translators can reorder words and decline names freely.
:func:`jinja_sources` hands the generated sources to the message extractor.

English sentences write "the" before the names of empires, wars, ...; names that
bring their own article ("The Chosen") drop it, see :func:`elide_articles`.
"""
import enum
import functools
import re
from dataclasses import dataclass
from typing import Optional

from markupsafe import Markup, escape

from stellarisdashboard import datamodel, i18n
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

    @property
    def label(self) -> str:
        """The name of the category in the dashboard language."""
        return {
            "WARFARE": pgettext("event category", "Warfare"),
            "DIPLOMACY": pgettext("event category", "Diplomacy"),
            "GALAXY": pgettext("event category", "Galaxy"),
            "EXPANSION": pgettext("event category", "Expansion"),
            "SCIENCE": pgettext("event category", "Science"),
            "STATECRAFT": pgettext("event category", "Statecraft"),
            "LEADERS": pgettext("event category", "Leaders"),
            "EXPLORATION": pgettext("event category", "Exploration"),
        }[self.name]


class Tier(enum.IntEnum):
    HEADLINE = 4
    MAJOR = 3
    ROUTINE = 2
    TRIVIA = 1


# How an event is dated: a point in time, or a span with an optional end date.
POINT = "point"
SPAN = "span"
ENVOY = "envoy"   # a span that is merged per envoy group (see event_grouping)


# ---------------------------------------------------------------------------
# Articles. English sentences say "the Tzynn Empire", but "The Chosen" instead of
# "the The Chosen". The messages keep the "the" (so translators see whole sentences),
# and it is dropped before names that bring their own, as long as the English text
# is shown. Translations write whatever their language needs.
# ---------------------------------------------------------------------------

# placeholders whose names get "the" in English
_ARTICLE_PLACEHOLDERS = frozenset({"country", "target", "war", "sector", "federation", "faction_type", "name"})
_ARTICLE_RE = re.compile(r"\b[Tt]he %\((\w+)\)s")


def has_own_article(name: str) -> bool:
    """Whether a name starts with its own article, like "The Chosen" or "The War in Heaven"."""
    return name.lower().startswith("the ")


def _plain_text(value) -> str:
    return Markup(value).striptags() if isinstance(value, Markup) else str(value)


def elide_articles(message: str, variables: dict) -> str:
    """The English message without "the" before names that start with their own article."""

    def replace(match):
        name = match.group(1)
        value = variables.get(name)
        if name in _ARTICLE_PLACEHOLDERS and value is not None and has_own_article(_plain_text(value)):
            return f"%({name})s"
        return match.group(0)

    return _ARTICLE_RE.sub(replace, message)


def _markup_message(translated: str, english: str, variables: dict) -> Markup:
    """A translated message filled with (escaped) values, as markup. The English text drops
    doubled articles, see elide_articles."""
    if translated == english:
        translated = english = elide_articles(english, variables)
    return Markup(i18n.format_message(Markup(translated), variables, fallback=Markup(english)))


def sentence_gettext(message: str, **variables) -> Markup:
    """gettext for the ledger sentences, see _markup_message."""
    return _markup_message(i18n.translate(message), message, variables)


def sentence_ngettext(singular: str, plural: str, n: int, **variables) -> Markup:
    variables = {"num": n, **variables}
    english = singular if n == 1 else plural
    translated = i18n.ntranslate(singular, plural, n)
    return _markup_message(translated if translated not in (singular, plural) else english, english, variables)


def sentence_pgettext(context: str, message: str, **variables) -> Markup:
    return _markup_message(i18n.ptranslate(context, message), message, variables)


def with_article(entity, links=None, capitalize=False) -> Markup:
    """An entity's link (or a plain name) with its article: "the Tzynn Empire", but "The Chosen"."""
    if isinstance(entity, str):
        shown = escape(entity)
    else:
        shown = Markup(links[entity])
    if capitalize:
        # Translators: the name of an empire (a link) at the start of a sentence, in a list of empires
        # ("The A, the B and the C defeated ..."). Leave out the article if your language has none: "%(name)s"
        return sentence_pgettext("name with article", "The %(name)s", name=shown)
    # Translators: the name of an empire (a link) in a list of empires ("... together with the A, the B and
    # the C"). Leave out the article if your language has none: "%(name)s"
    return sentence_pgettext("name with article", "the %(name)s", name=shown)


def with_articles(entities, links=None, capitalize=False) -> Markup:
    """Several entities, each with its article: "the A, the B and the C"."""
    parts = [with_article(e, links, capitalize and i == 0) for i, e in enumerate(entities)]
    return Markup(datamodel.join_and(parts))


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


def render_war(event, *, links) -> Markup:
    """The war's name; wars without a name in the game are described by their sides."""
    war = event["war"]
    link = Markup(links[war])
    if war.ingame_name:
        return link
    # Translators: a war without a name in the game, e.g. "Empire A vs Empire B war".
    # %(war)s is the link to the war, named after its sides.
    return Markup(_("%(war)s war", war=link))


def starts_with_vowel(text) -> bool:
    """Whether English needs "an" rather than "a" before the text (quotes aside)."""
    return str(text).lstrip('"').lower().startswith(("a", "e", "i", "o", "u"))


def _render_context(event, links) -> dict:
    return dict(
        event=event,
        links=links,
        _=sentence_gettext,
        gettext=sentence_gettext,
        ngettext=sentence_ngettext,
        pgettext=sentence_pgettext,
        render_leader=functools.partial(render_leader, links=links),
        render_envoys=functools.partial(render_envoys, links=links),
        render_planet_and_class=functools.partial(render_planet_and_class, links=links),
        render_war=functools.partial(render_war, links=links),
        the_all=lambda entities: with_articles(entities, links),
        The_all=lambda entities: with_articles(entities, links, capitalize=True),
        has_own_article=has_own_article,
        starts_with_vowel=starts_with_vowel,
        crisis_phrases=GALACTIC_CRISIS_PHRASES,
    )


# ---------------------------------------------------------------------------
# Helpers that build the Jinja source of the sentences.
# ---------------------------------------------------------------------------

# Jinja expressions for the placeholders that most messages use; {ev} is the name of the
# dict that describes the event.
_DEFAULT_ARGS = {
    "country": '{links}[{ev}["country"]] | safe',
    "target": '{links}[{ev}["target_country"]] | safe',
    "leader": "render_leader({ev})",
    "system": '{links}[{ev}["system"]] | safe',
    "planet": '{links}[{ev}["planet"]] | safe',
    "war": "render_war({ev})",
}

# What the default placeholders mean, added to the notes for translators of every message using them.
_PLACEHOLDER_NOTES = {
    "country": '%(country)s: name of an empire (a link), usually reads like "United Nations of Earth"',
    "target": "%(target)s: name of another empire (a link), like %(country)s",
    "leader": '%(leader)s: class and name of a leader (a link), e.g. "Scientist Jane Doe"',
    "system": "%(system)s: name of a star system (a link)",
    "planet": "%(planet)s: name of a planet (a link)",
    "war": '%(war)s: name of a war (a link); a war without a name in the game reads "Empire A vs Empire B war"',
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


def _used(message, overrides) -> dict:
    return {k: v for k, v in overrides.items() if f"%({k})s" in message}


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


def _optional(conditions, build) -> str:
    """Nest {% if %} over optional parts: build(parts) gives the content for the parts that are present.

    conditions is a list of (name, Jinja condition); parts is the tuple of the names whose condition holds."""

    def nest(remaining, present):
        if not remaining:
            return build(present)
        name, condition = remaining[0]
        return _if(condition, nest(remaining[1:], present + (name,)), nest(remaining[1:], present))

    return nest(list(conditions), ())


@dataclass(frozen=True)
class Part:
    """An optional part of a sentence: the {slot} of the sentence is filled with ``text``
    if ``condition`` holds for the event, else left empty. A part that ``requires`` another
    one only occurs with it (its condition must imply the other's)."""
    slot: str
    condition: str
    text: str
    requires: Optional[str] = None


IN_SYSTEM = Part("system", '"system" in event', " in the %(system)s system")
DURING_WAR = Part("war", '"war" in event', " during the %(war)s")
UNDER_GOVERNOR = Part("governor", HAS_LEADER, " under the governorship of %(leader)s")


def _fill(template: str, parts, present) -> str:
    return template.format(**{p.slot: (p.text if p.slot in present else "") for p in parts})


def _sentence(template: str, *parts: Part, note: Optional[str] = None, **overrides) -> str:
    """All variants of a sentence with optional parts: ``template`` has a {slot} for each part."""

    def build(present):
        if any(p.requires and p.slot in present and p.requires not in present for p in parts):
            return ""  # cannot happen
        message = _fill(template, parts, present)
        return _t(message, note, **_used(message, overrides))

    return _optional([(p.slot, p.condition) for p in parts], build)


def _ruled(template: str, *parts: Part, note: Optional[str] = None, **overrides) -> str:
    """A sentence about the event's country, starting with "The %(country)s", in two variants:
    "Under %(leader)s, the %(country)s ..." if the event has a leader (its country's ruler), else as given."""
    assert template.startswith("The %(country)s ")
    with_leader = "Under %(leader)s, the %(country)s " + template[len("The %(country)s "):]
    return _if(HAS_LEADER, _sentence(with_leader, *parts, note=note, **overrides),
               _sentence(template, *parts, note=note, **overrides))


def _leader_of_country(template: str, *parts: Part, note: Optional[str] = None, **overrides) -> str:
    """A sentence about the event's country, starting with "The %(country)s", in two variants:
    "%(leader)s of the %(country)s ..." if the event has a leader, else as given."""
    assert template.startswith("The %(country)s ")
    with_leader = "%(leader)s of the %(country)s " + template[len("The %(country)s "):]
    return _if(HAS_LEADER, _sentence(with_leader, *parts, note=note, **overrides),
               _sentence(template, *parts, note=note, **overrides))


def _a_an(condition_text: str, template: str, note: Optional[str] = None, **overrides) -> str:
    """Two variants of a sentence with "{a}" before a name: "an" or "a" in English."""
    note = ((note + ". ") if note else "") + 'The English text has the variants "a" and "an" of this sentence'
    return _if(
        "starts_with_vowel(" + condition_text + ")",
        _t(template.format(a="an"), note, **overrides),
        _t(template.format(a="a"), note, **overrides),
    )


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


# ---- Sentences with several variants ---------------------------------------------------

# war participant call type (see datamodel.WAR_CALL_TYPES) -> how they joined, followed by the empire that called them
_WAR_CALL_SENTENCES = {
    "overlord": "The %(country)s entered the %(war)s, called in by their overlord the %(target)s.",
    "subject": "The %(country)s entered the %(war)s, called in by their subject the %(target)s.",
    "secret_fealty": "The %(country)s entered the %(war)s, called in by their secret overlord the %(target)s.",
    "offensive": "The %(country)s entered the %(war)s, attacking alongside the %(target)s.",
    "defensive": "The %(country)s entered the %(war)s, honoring their defensive pact with the %(target)s.",
    "alliance": "The %(country)s entered the %(war)s, honoring their alliance with the %(target)s.",
    "guarantee": "The %(country)s entered the %(war)s, honoring their guarantee of independence for the %(target)s.",
    "trade_deal": "The %(country)s entered the %(war)s, honoring a deal with the %(target)s.",
}


def _war_sentence() -> str:
    """The country entered a war; the sentence depends on how it was drawn into it and by whom."""
    call_type = 'event.get("call_type")'
    return _cases(
        (call_type + ' == "primary"', _ruled("The %(country)s entered the %(war)s as a primary party.")),
        *[
            (f'{call_type} == "{key}" and "target_country" in event', _ruled(message))
            for key, message in _WAR_CALL_SENTENCES.items()
        ],
        # call types the dashboard does not know: still name who called the country in
        ('"target_country" in event', _ruled("The %(country)s entered the %(war)s, called in by the %(target)s.")),
        default=_ruled("The %(country)s entered the %(war)s."),
    )


def _combat_sentence(invasion: bool) -> str:
    """A space battle or an invasion: the attackers won or lost against the defenders."""
    args = dict(
        attackers_first='The_all(event["attackers"])',
        attackers='the_all(event["attackers"])',
        defenders_first='The_all(event["defenders"])',
        defenders='the_all(event["defenders"])',
        attacker_exhaustion='event["attacker_exhaustion"]',
        defender_exhaustion='event["defender_exhaustion"]',
    )
    note = (
        "%(attackers)s and %(defenders)s are lists of empires (links) with their articles, "
        'e.g. "the A and the B" (see the messages "the %%(name)s" and "The %%(name)s"). '
        "The exhaustion values are the war exhaustion of each side in percent, e.g. 12.5%%"
    )
    exhaustion = " (exhaustion: attackers %(attacker_exhaustion)s, defenders %(defender_exhaustion)s)."
    if invasion:
        def build_for_planet(victory, planet):
            if victory:
                template = "%(attackers_first)s invaded " + planet + "{system}, defeating %(defenders)s{war}" + exhaustion
            else:
                template = (
                    "%(defenders_first)s fought off an invasion of " + planet + "{system} by %(attackers)s{war}"
                    + exhaustion
                )
            return _sentence(template, IN_SYSTEM, DURING_WAR, note=note, **args)

        def build(victory):
            return _if('"planet" in event', build_for_planet(victory, "the planet %(planet)s"),
                       build_for_planet(victory, "a planet"))
    else:
        def build(victory):
            if victory:
                template = "%(attackers_first)s defeated %(defenders)s in a space battle{system}{war}" + exhaustion
            else:
                template = "%(defenders_first)s repelled an attack by %(attackers)s in a space battle{system}{war}" + exhaustion
            return _sentence(template, IN_SYSTEM, DURING_WAR, note=note, **args)

    return _if('event["attacker_victory"]', build(True), build(False))


def _first_contact_process_sentence() -> str:
    """Whether a first contact began, was made or was abandoned, with or without leader and system."""

    def build(status_phrase):
        return _if(
            HAS_LEADER,
            _sentence("The %(country)s's %(leader)s " + status_phrase + " the %(target)s{system}.", IN_SYSTEM),
            _sentence("The %(country)s " + status_phrase + " the %(target)s{system}.", IN_SYSTEM),
        )

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


def _planet_destroyed_sentence() -> str:
    owner = Part("owner", '"country" in event', ", owned by the %(country)s,")
    return _sentence("The planet %(planet)s{system}{owner} was destroyed.", IN_SYSTEM, owner) + _if(
        'event["planet"].planet_class is not none',
        _a_an(
            'event["planet"].planetclass',
            "It is now {a} %(planet_class)s.",
            "%(planet_class)s: the class of the planet, e.g. \"Barren World\"",
            planet_class='event["planet"].planetclass',
        ),
        _t("No trace remains."),
    )


def _crisis_level_sentence() -> str:
    level = dict(level='event["crisis_level"]')
    note = "%(level)s: the name of a crisis level, e.g. \"Risk\""
    return _if(
        'event.get("crisis_defender")',
        _a_an('event["crisis_level"]', 'The %(country)s became {a} "%(level)s"-rank Defender of the Galaxy.', note, **level),
        _a_an('event["crisis_level"]', 'The %(country)s became {a} "%(level)s"-level threat to the galaxy.', note, **level),
    )


def _new_faction_sentence() -> str:
    faction = dict(faction='event["faction"].rendered_name')
    note = '%(faction)s: name of the faction. %(faction_type)s: the kind of faction, e.g. "Supremacist"'
    return _cases(
        ('event["faction_type"] == event["faction"].rendered_name and not has_own_article(event["faction_type"])',
         _t('The "%(faction)s" faction emerged in the %(country)s.', **faction)),
        ('event["faction_type"] == event["faction"].rendered_name',
         _t('"%(faction)s" faction emerged in the %(country)s.', **faction)),
        default=_t(
            'The %(faction_type)s "%(faction)s" emerged in the %(country)s.',
            note, faction_type='event["faction_type"]', **faction,
        ),
    )


def _tradition_sentence() -> str:
    tradition = dict(tradition='event["tradition"]')
    return _cases(
        ('event.get("tradition_kind") == "adopt"', _ruled(
            "The %(country)s adopted the %(tradition)s.",
            note='%(tradition)s: a tradition tree, e.g. "Discovery Traditions"', **tradition,
        )),
        ('event.get("tradition_kind") == "finish"', _ruled(
            "The %(country)s completed the %(tradition)s.",
            note='%(tradition)s: a tradition tree, e.g. "Discovery Traditions"', **tradition,
        )),
        default=_ruled('The %(country)s adopted the "%(tradition)s" tradition.', **tradition),
    )


_FROM_COUNTRY = Part("from", '"target_country" in event', " from the %(target)s")
_ON_PLANET = Part("planet", '"planet" in event', " on %(planet)s")
_CONCERNING_TARGET = Part("target", '"target_country" in event', " concerning the %(target)s")
_SECTOR_CAPITAL = Part("capital", '"planet" in event', " with its capital on %(planet)s")
_SYSTEM_OF_PLANET = Part("system", '"planet" in event and "system" in event', " in the %(system)s system", "capital")


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
        _ruled("The %(country)s made peace in the %(war)s."),
    ),
    "conquered_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        _sentence("The %(country)s conquered the %(system)s system{from}{war}.", _FROM_COUNTRY, DURING_WAR),
    ),
    "founded_with_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        _sentence(
            "The %(country)s was founded with the %(system)s system{held}.",
            Part("held", '"target_country" in event', ", formerly held by the %(target)s"),
        ),
    ),
    "lost_system": EventDescriptor(
        Category.WARFARE, Tier.MAJOR, "flag", POINT,
        _if(
            '"target_country" in event',
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
        _ruled("The %(country)s established first contact with the %(target)s."),
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
        _leader_of_country('The %(country)s ran the "%(operation)s" operation against the %(target)s.', operation=D),
    ),
    "completed_espionage_operation": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "key", POINT,
        _leader_of_country('The %(country)s completed the "%(operation)s" operation against the %(target)s.', operation=D),
    ),
    "formed_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", SPAN,
        _ruled('The %(country)s allied with the %(target)s in the "%(federation)s" federation.', federation=D),
    ),
    "founded_federation": EventDescriptor(
        Category.DIPLOMACY, Tier.HEADLINE, "federation", POINT,
        _if(
            '"federation_partners" in event',
            _t(
                'The %(country)s founded the "%(federation)s" federation together with %(partners)s.',
                "%(partners)s: the other founding members with their articles, e.g. \"the A, the B and the C\" (links)",
                federation='event["federation_name"]',
                partners='the_all(event["federation_partners"])',
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
        _ruled("The %(country)s entered a defensive pact with the %(target)s."),
    ),
    "non_aggression_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        _ruled("The %(country)s entered a non-aggression pact with the %(target)s."),
    ),
    "commercial_pact": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "coins", SPAN,
        _ruled("The %(country)s entered a commercial pact with the %(target)s."),
    ),
    "research_agreement": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        _ruled("The %(country)s entered a research agreement with the %(target)s."),
    ),
    "migration_treaty": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "scroll", SPAN,
        _ruled("The %(country)s entered a migration treaty with the %(target)s."),
    ),
    "embassy": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "building", SPAN,
        _t("The %(country)s established an embassy with the %(target)s."),
    ),
    "closed_borders": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "barrier", SPAN,
        _ruled("The %(country)s closed their borders to the %(target)s."),
    ),
    "received_closed_borders": EventDescriptor(
        Category.DIPLOMACY, Tier.TRIVIA, "barrier", SPAN,
        _t("The %(country)s lost border access to the %(target)s."),
    ),
    "sent_rivalry": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "rivalry", SPAN,
        _ruled("The %(country)s declared the %(target)s a rival."),
    ),
    "received_rivalry": EventDescriptor(
        Category.DIPLOMACY, Tier.TRIVIA, "rivalry", SPAN,
        _ruled("The %(country)s was declared a rival by the %(target)s."),
    ),
    "became_subject": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "chain", SPAN,
        _a_an(
            'event["description"]',
            "The %(country)s became {a} %(subject_type)s of the %(target)s.",
            "%(subject_type)s: the kind of subject, e.g. \"Vassal\"",
            subject_type=D,
        ),
    ),
    "federation_president": EventDescriptor(
        Category.DIPLOMACY, Tier.ROUTINE, "federation", SPAN,
        _t(
            "The %(country)s held the presidency of the %(federation_title)s.",
            "%(federation_title)s: the type and name of the federation, e.g. Martial Alliance \"Star Security Organization\"",
            federation_title='event["federation_title"]',
        ),
    ),
    "federation_level_up": EventDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "federation", POINT,
        _t(
            "Under the presidency of the %(country)s, the %(federation_title)s reached level %(level)s.",
            "%(federation_title)s: the type and name of the federation, e.g. Martial Alliance \"Star Security Organization\"",
            federation_title='event["federation_title"]',
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
        _envoy_sentence("The %(country)s sent %(envoys)s to increase their diplomatic weight in the Galactic Community."),
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
        _ruled("The %(country)s joined the Galactic Community."),
    ),
    "joined_galactic_council": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        _ruled("The %(country)s joined the Galactic Council."),
    ),
    "left_galactic_community": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        _ruled("The %(country)s left the Galactic Community."),
    ),
    "left_galactic_council": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "globe", SPAN,
        _ruled("The %(country)s left the Galactic Council."),
    ),
    "galactic_resolution_passed": EventDescriptor(
        Category.GALAXY, Tier.MAJOR, "ballot", POINT,
        _sentence(
            "The Galactic Community passed the resolution %(resolution)s{proposed}{concerning}.",
            Part("proposed", '"country" in event', ", proposed by the %(country)s"),
            Part("concerning", '"target_country" in event', ", concerning the %(target)s"),
            note="%(resolution)s: the name of the resolution, in quotation marks",
            resolution=D,
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
        _cases(
            ('event.get("cause") == "war" and "target_country" in event',
             _t("The %(country)s ceased to exist after losing a war against the %(target)s.")),
            ('event.get("cause") == "subject" and "target_country" in event',
             _t("The %(country)s ceased to exist after being integrated by the %(target)s.")),
            default=_t("The %(country)s ceased to exist."),
        ),
    ),
    "voted_for_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    "voted_against_resolution": EventDescriptor(Category.GALAXY, Tier.ROUTINE, "ballot"),
    "galactic_crisis": EventDescriptor(
        Category.GALAXY, Tier.HEADLINE, "crisis", POINT,
        _if(
            'event.get("crisis_key") in crisis_phrases',
            # the message is picked from GALACTIC_CRISIS_PHRASES, where it is marked for translation
            "{{ _(crisis_phrases[event[\"crisis_key\"]], country=links[event[\"country\"]] | safe) }}\n",
            _t("The %(country)s %(description)s.", description=D),
        ),
    ),
    # ---- Expansion ------------------------------------------------------
    "megastructure_construction": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "megastructure", SPAN,
        _if(
            'event["end_date"]',
            _sentence("The %(country)s built the %(megastructure)s{system}.", IN_SYSTEM, megastructure=D),
            _sentence("The %(country)s began building the %(megastructure)s{system}.", IN_SYSTEM, megastructure=D),
        ),
    ),
    "planet_destroyed": EventDescriptor(
        Category.EXPANSION, Tier.HEADLINE, "explosion", POINT,
        _planet_destroyed_sentence(),
    ),
    "habitat_ringworld_construction": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "ringworld", POINT,
        _if(
            '"country" in event',
            _sentence("The %(country)s finished building %(structure)s{system}{governor}.",
                      IN_SYSTEM, UNDER_GOVERNOR, structure=D),
            _sentence("Construction finished on %(structure)s{system}{governor}.",
                      IN_SYSTEM, UNDER_GOVERNOR, structure=D),
        ),
    ),
    "colonization": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "planet", SPAN,
        _sentence("The %(country)s colonized %(planet)s{system}{governor}.", IN_SYSTEM, UNDER_GOVERNOR,
                  planet=PLANET_AND_CLASS),
    ),
    "capital_relocation": EventDescriptor(
        Category.EXPANSION, Tier.MAJOR, "capital", POINT,
        _ruled("The %(country)s relocated their capital to %(planet)s{system}.", IN_SYSTEM, planet=PLANET_AND_CLASS),
    ),
    "terraforming": EventDescriptor(
        Category.EXPANSION, Tier.ROUTINE, "planet", SPAN,
        _sentence(
            "The %(country)s terraformed the planet %(planet)s{system} from %(old_class)s to %(new_class)s{governor}.",
            IN_SYSTEM, UNDER_GOVERNOR,
            note="%(old_class)s and %(new_class)s are planet classes, e.g. \"Barren World\"",
            old_class='event["old_class"]',
            new_class='event["new_class"]',
        ),
    ),
    "expanded_to_system": EventDescriptor(
        Category.EXPANSION, Tier.ROUTINE, "outpost", POINT,
        _t("The %(country)s built an outpost in the %(system)s system."),
    ),
    "sector_creation": EventDescriptor(
        Category.EXPANSION, Tier.TRIVIA, "sector", POINT,
        _ruled(
            "The %(country)s created the %(sector)s sector{capital}{system}.",
            _SECTOR_CAPITAL, _SYSTEM_OF_PLANET,
            note="%(sector)s: the name of the sector",
            sector=D,
        ),
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
        _sentence('The %(country)s discovered the "%(anomaly)s" anomaly on %(planet)s{system}.', IN_SYSTEM,
                  anomaly=D, planet=PLANET_AND_CLASS),
    ),
    "researched_anomaly": EventDescriptor(
        Category.EXPLORATION, Tier.ROUTINE, "microscope", POINT,
        _leader_of_country('The %(country)s finished researching the "%(anomaly)s" anomaly on %(planet)s{system}.',
                           IN_SYSTEM, anomaly=D, planet=PLANET_AND_CLASS),
    ),
    "excavated_dig_site": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "shovel", POINT,
        _sentence(
            'The %(country)s completed an excavation at the "%(site)s" archaeological site on %(planet)s{system}.',
            IN_SYSTEM, site=D, planet=PLANET_AND_CLASS,
        ),
    ),
    "explored_astral_rift": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "compass", POINT,
        _leader_of_country('The %(country)s explored the astral rift "%(rift)s"{system}.', IN_SYSTEM, rift=D),
    ),
    "acquired_relic": EventDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "gem", POINT,
        _t('The %(country)s acquired the "%(relic)s" relic.', relic=D),
    ),
    "special_project": EventDescriptor(
        Category.EXPLORATION, Tier.ROUTINE, "flask", SPAN,
        _cases(
            ('event["project_outcome"] == "completed"',
             _t('The %(country)s completed the special project "%(project)s".', project=D)),
            ('event["project_outcome"]',
             _t('The special project "%(project)s" of the %(country)s ended unfinished.', project=D)),
            default=_t('The %(country)s began the special project "%(project)s".', project=D),
        ),
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
    "situation": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "alert", SPAN,
        _sentence('The "%(situation)s" situation began for the %(country)s{planet}{target}.',
                  _ON_PLANET, _CONCERNING_TARGET, situation=D, planet=PLANET_AND_CLASS),
    ),
    # as major as the situation itself, so that its card lists every stage
    "situation_stage": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "alert", POINT,
        _if(
            'event["stage_name"]',
            _t('The "%(situation)s" situation of the %(country)s reached stage %(stage)s, "%(stage_name)s".',
               situation=D, stage='event["stage_number"]', stage_name='event["stage_name"]'),
            _t('The "%(situation)s" situation of the %(country)s reached stage %(stage)s.',
               situation=D, stage='event["stage_number"]'),
        ),
    ),
    "crisis_level_up": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "star", POINT,
        _crisis_level_sentence(),
    ),
    "ascension_perk": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "star", POINT,
        _ruled('The %(country)s unlocked the "%(perk)s" ascension perk.', perk=D),
    ),
    "government_reform": EventDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "gavel", POINT,
        _if(
            'event["description"]',
            _ruled(
                "The %(country)s reformed their government: %(reforms)s",
                note="%(reforms)s: sentences describing the changes of the government, "
                     "e.g. \"Adopted civics A, B. Embraced ethics C.\"",
                reforms=D,
            ),
            _ruled("The %(country)s reformed their government."),
        ),
    ),
    "empire_renamed": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "gavel", POINT,
        _ruled(
            "The %(country)s was renamed from %(old_name)s to %(new_name)s.",
            note="%(old_name)s and %(new_name)s are the names of the empire",
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
        _if(
            'event["selected"] == event["policy"]',
            # e.g. the gestalt logistics option is also called "Logistics Policy"
            _ruled('The %(country)s adopted the "%(policy)s".', note="%(policy)s: the name of a policy",
                   policy='event["policy"]'),
            _ruled(
                'The %(country)s set their policy on "%(policy)s" to "%(selected)s".',
                note="%(policy)s: the area of the policy, %(selected)s: the option chosen",
                policy='event["policy"]', selected='event["selected"]',
            ),
        ),
    ),
    "changed_policy": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "policy", POINT,
        _ruled(
            'The %(country)s changed their policy on "%(policy)s" from "%(old_choice)s" to "%(new_choice)s".',
            note="%(policy)s: the area of the policy, %(old_choice)s and %(new_choice)s: the options",
            policy='event["policy"]', old_choice='event["old_choice"]', new_choice='event["new_choice"]',
        ),
    ),
    "tradition": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "tradition", POINT,
        _tradition_sentence(),
    ),
    "agenda_launch": EventDescriptor(
        Category.STATECRAFT, Tier.ROUTINE, "agenda", POINT,
        _ruled('The %(country)s launched the "%(agenda)s" agenda.', agenda=D),
    ),
    "edict": EventDescriptor(
        Category.STATECRAFT, Tier.TRIVIA, "edict", SPAN,
        _ruled('The %(country)s enacted the "%(edict)s" edict.', edict=D),
    ),
    "agenda_preparation": EventDescriptor(
        Category.STATECRAFT, Tier.TRIVIA, "agenda", SPAN,
        _ruled('The %(country)s prepared the "%(agenda)s" agenda.', agenda=D),
    ),
    # ---- Leaders --------------------------------------------------------
    "ruled_empire": EventDescriptor(
        Category.LEADERS, Tier.MAJOR, "crown", SPAN,
        _sentence(
            "%(leader)s ruled the %(country)s{capital}{system}.",
            Part("capital", '"planet" in event', " from the capital %(planet)s"), _SYSTEM_OF_PLANET,
            leader=LEADER_AS_SUBJECT,
        ),
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
        # hive minds' councilors are named after their seat, e.g. "Growth Node"
        _if(
            '"leader" not in event or event["description"] != event["leader"].rendered_name',
            _t(
                "%(leader)s served as %(position)s on the council of the %(country)s.",
                "%(position)s: the council position, e.g. \"Chancellor\"",
                leader=LEADER_AS_SUBJECT, position=D,
            ),
            _t("%(leader)s served on the council of the %(country)s.", leader=LEADER_AS_SUBJECT),
        ),
    ),
    "gained_trait": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "dna", POINT,
        _if(
            'event["description"]',
            _t('%(leader)s gained the "%(trait)s" trait.', leader=LEADER_AS_SUBJECT, trait=D),
            _t("%(leader)s gained a trait.", leader=LEADER_AS_SUBJECT),
        ),
    ),
    "lost_trait": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "dna", POINT,
        _if(
            'event["description"]',
            _t('%(leader)s lost the "%(trait)s" trait.', leader=LEADER_AS_SUBJECT, trait=D),
            _t("%(leader)s lost a trait.", leader=LEADER_AS_SUBJECT),
        ),
    ),
    "leader_left_country": EventDescriptor(
        Category.LEADERS, Tier.ROUTINE, "person", POINT,
        _t("%(leader)s left the service of the %(country)s.", leader=LEADER_AS_SUBJECT),
    ),
    "faction_leader": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "faction", SPAN,
        _t(
            '%(leader)s led the "%(faction)s" faction.',
            leader=LEADER_AS_SUBJECT, faction='event["faction"].rendered_name',
        ),
    ),
    "governed_sector": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "sector", SPAN,
        _sentence(
            "%(leader)s governed the %(sector)s sector{capital}{system}.",
            Part("capital", '"planet" in event', " from %(planet)s"), _SYSTEM_OF_PLANET,
            note="%(sector)s: the name of the sector",
            leader=LEADER_AS_SUBJECT, sector=D,
        ),
    ),
    "governed_planet": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "planet", SPAN,
        _sentence("%(leader)s governed the planet %(planet)s{system}.", IN_SYSTEM, leader=LEADER_AS_SUBJECT),
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
    ),
    "level_up": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "chevron-up", POINT,
        _t("%(leader)s reached level %(level)s.", leader=LEADER_AS_SUBJECT, level=D),
    ),
    "leader_changed_ethic": EventDescriptor(
        Category.LEADERS, Tier.TRIVIA, "dna", POINT,
        _t("%(leader)s changed their ethic to %(ethic)s.", leader=LEADER_AS_SUBJECT, ethic=D),
    ),
}


@dataclass(frozen=True)
class StorylineDescriptor:
    category: Category
    tier: Tier
    icon: str
    sentence: str  # Jinja: the arc's title; the storyline item dict is named ``event``
    state_labels: dict[str, str]  # marked with N_, translated when shown (see state_label)
    stage_rows: dict[tuple, str]  # marked with N_, see render_stage
    # labels that replace state_labels for arcs whose full progress is the bad outcome (item["negative"])
    negative_state_labels: Optional[dict[str, str]] = None


_STORYLINE_LEADER = '{% if "leader" in event %} ({{ render_leader(event) }}){% endif %}'
_BY_COUNTRY = Part("country", '"country" in event', " by the %(country)s")


# StorylineType name -> StorylineDescriptor
STORYLINE_REGISTRY: dict[str, StorylineDescriptor] = {
    "dig_site": StorylineDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "shovel",
        _sentence(
            'Excavation of the "%(site)s" archaeological site{planet}{system}{country}',
            Part("planet", '"planet" in event', " on %(planet)s"), IN_SYSTEM, _BY_COUNTRY,
            site='event["title"]', planet=PLANET_AND_CLASS,
        ) + _STORYLINE_LEADER,
        {"active": N_("excavating"), "paused": N_("paused"), "completed": N_("fully excavated")},
        {
            (): N_("Chapter %(index)s"),
            ("detail",): N_("Chapter %(index)s: %(detail)s"),
            ("country",): N_("Chapter %(index)s by the %(country)s"),
            ("detail", "country"): N_("Chapter %(index)s: %(detail)s by the %(country)s"),
        },
    ),
    "astral_rift": StorylineDescriptor(
        Category.EXPLORATION, Tier.MAJOR, "compass",
        _sentence(
            'Exploration of the astral rift "%(rift)s"{system}{country}', IN_SYSTEM, _BY_COUNTRY,
            rift='event["title"]',
        ) + _STORYLINE_LEADER,
        {"active": N_("exploring"), "paused": N_("paused"), "completed": N_("fully explored")},
        {
            (): N_("Step %(index)s"),
            ("detail",): N_("Step %(index)s: %(detail)s"),
            ("country",): N_("Step %(index)s by the %(country)s"),
            ("detail", "country"): N_("Step %(index)s: %(detail)s by the %(country)s"),
        },
    ),
    "first_contact": StorylineDescriptor(
        Category.DIPLOMACY, Tier.MAJOR, "handshake",
        _sentence("First contact between the %(country)s and the %(target)s{system}", IN_SYSTEM)
        # both sides' envoys once the two sides are merged
        + '{% if "leader" in event %} ({{ render_envoys(event) }}){% endif %}',
        {"active": N_("in progress"), "completed": N_("contact made"), "abandoned": N_("abandoned")},
        {
            (): N_("Stage %(index)s"),
            ("detail",): N_("Stage %(index)s: %(detail)s"),
            ("country",): N_("Stage %(index)s by the %(country)s"),
            ("detail", "country"): N_("Stage %(index)s: %(detail)s by the %(country)s"),
        },
    ),
    "situation": StorylineDescriptor(
        Category.STATECRAFT, Tier.MAJOR, "alert",
        _sentence(
            'The "%(situation)s" situation of the %(country)s{planet}{target}', _ON_PLANET, _CONCERNING_TARGET,
            situation='event["title"]', planet=PLANET_AND_CLASS,
        ),
        {"active": N_("in progress"), "completed": N_("completed"), "failed": N_("failed"), "abandoned": N_("ended")},
        {
            (): N_("Stage %(index)s"),
            ("detail",): N_("Stage %(index)s: %(detail)s"),
            ("country",): N_("Stage %(index)s by the %(country)s"),
            ("detail", "country"): N_("Stage %(index)s: %(detail)s by the %(country)s"),
        },
        # Translators: how a harmful situation (a deficit, a revolt) ended: at its worst, or dropped to nothing
        {"completed": N_("escalated"), "failed": N_("averted")},
    ),
}


def state_label(item) -> str:
    """The state of a storyline item in the dashboard language, e.g. "fully excavated"."""
    descriptor = STORYLINE_REGISTRY.get(item.get("storyline_type"))
    state = item.get("state")
    labels = descriptor.state_labels if descriptor else {}
    if item.get("negative") and descriptor.negative_state_labels:
        labels = {**labels, **descriptor.negative_state_labels}
    label = labels.get(state)
    label = _(label) if label else (state or "")
    if state == "active" and item.get("progress") is not None:
        # Translators: the state of a situation and how far its progress bar is filled, e.g. "in progress, 62%"
        return _("%(state)s, %(percent)s%%", state=label, percent=round(100 * item["progress"]))
    return label


def stage_count_label(item) -> str:
    """How many stages a storyline item reached, e.g. "3 chapters"."""
    count = item["stage_count"]
    if item["storyline_type"] == "dig_site":
        return ngettext("%(num)s chapter", "%(num)s chapters", count)
    if item["storyline_type"] == "astral_rift":
        return ngettext("%(num)s step", "%(num)s steps", count)
    if item["storyline_type"] == "situation":
        # Translators: the furthest stage a situation reached, e.g. "stage 3"
        return pgettext("situation", "stage %(num)s", num=count)
    return ngettext("%(num)s stage", "%(num)s stages", count)


def render_stage(item, stage, links) -> Markup:
    """One stage of a storyline: its number, what happened, and the country if it differs from the arc's.

    Stages without a number (both sides of a first contact) show what happened, capitalized."""
    descriptor = STORYLINE_REGISTRY[item["storyline_type"]]
    country = Markup(links[stage["country"]]) if stage.get("country") is not None else None
    detail = stage.get("detail")
    if "index" not in stage:
        detail = (detail or "")[:1].upper() + (detail or "")[1:]
        if country is None:
            return escape(detail)
        # Translators: a stage of a first contact and the empire that reached it, e.g.
        # "Communications established by the United Nations of Earth". %(country)s is the empire (a link)
        return sentence_gettext("%(detail)s by the %(country)s", detail=detail, country=country)
    parts = tuple(p for p, present in (("detail", bool(detail)), ("country", country is not None)) if present)
    values = dict(index=stage["index"])
    if detail:
        values["detail"] = detail
    if country is not None:
        values["country"] = country
    return sentence_gettext(descriptor.stage_rows[parts], **values)


def _compiled(key: str, source: Optional[str]):
    """Lazily compile (and cache) a sentence template using Flask's own Jinja environment."""
    if key not in _COMPILED_SENTENCES:
        template = None
        if source is not None:
            from stellarisdashboard.dashboard_app import flask_app

            template = flask_app.jinja_env.from_string(source)
        _COMPILED_SENTENCES[key] = template
    return _COMPILED_SENTENCES[key]


_COMPILED_SENTENCES: dict[str, object] = {}


def render_sentence(event, links) -> Markup:
    """Just the sentence of an event or storyline item, without the date or list markup."""
    if event.get("event_type") == "storyline":
        key = "storyline:" + event.get("storyline_type", "")
        descriptor = STORYLINE_REGISTRY.get(event.get("storyline_type"))
    else:
        key = event.get("event_type")
        descriptor = REGISTRY.get(key)
    template = _compiled(key, descriptor.sentence if descriptor is not None else None)
    if template is None:
        return Markup("")
    # collapse the template's line breaks and indents, but keep no-break spaces (French "\u00a0:")
    text = re.sub(r"[ \t\n\r\f\v]+", " ", template.render(**_render_context(event, links))).strip()
    # an empty value leaves a space before the punctuation that follows it (French spaces before ":" and ";" stay)
    return Markup(re.sub(r" ([,.)])", r"\1", text))


def jinja_sources():
    """(name, source) of every Jinja template kept in this module, for extracting
    their translatable messages (see stellarisdashboard/i18n/extract.py)."""
    for event_type, descriptor in REGISTRY.items():
        if descriptor.sentence is not None:
            yield f"REGISTRY[{event_type}]", descriptor.sentence
    for storyline_type, descriptor in STORYLINE_REGISTRY.items():
        yield f"STORYLINE_REGISTRY[{storyline_type}]", descriptor.sentence


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
