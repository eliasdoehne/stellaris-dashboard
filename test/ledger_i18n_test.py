"""Every ledger sentence renders in English and through a (pseudo) translation catalog."""
import gettext
import itertools
import re

import pytest

from stellarisdashboard import i18n
from stellarisdashboard.dashboard_app import event_registry
from stellarisdashboard.dashboard_app.event_registry import (
    GALACTIC_CRISIS_PHRASES,
    REGISTRY,
    STORYLINE_REGISTRY,
    render_event,
)

MARKER = "[xx]"


class PseudoTranslations(gettext.NullTranslations):
    """Marks every message as translated, keeping its placeholders."""

    def gettext(self, message):
        return f"{MARKER} {message}"

    def ngettext(self, singular, plural, n):
        return f"{MARKER} {singular if n == 1 else plural}"

    def pgettext(self, context, message):
        return f"{MARKER} {message}"

    def npgettext(self, context, singular, plural, n):
        return f"{MARKER} {singular if n == 1 else plural}"


@pytest.fixture
def pseudo_translation(monkeypatch):
    translations = PseudoTranslations()
    monkeypatch.setattr(i18n, "_translations", lambda: translations)


class Thing:
    """Stands in for the database objects that the ledger links to."""

    def __init__(self, name, **attributes):
        self.name = name
        self.__dict__.update(attributes)

    def __repr__(self):
        return self.name


def make_links(**objects):
    return {obj: f'<a class="textlink" href=#{obj.name}>{obj.name}</a>' for obj in objects.values()}


COUNTRY = Thing("EmpireOne")
TARGET = Thing("EmpireTwo")
LEADER = Thing("Jane Doe", leader_class="scientist")
OTHER_LEADER = Thing("John Roe", leader_class="official")
SYSTEM = Thing("Sol")
PLANET = Thing("Terra", planet_class="pc_gaia", planetclass="Gaia World")
PLANET_WITHOUT_CLASS = Thing("Dust", planet_class=None)
WAR = Thing("Great War", ingame_name="Great War")
UNNAMED_WAR = Thing("A vs B", ingame_name=None)
FACTION = Thing("Free Folk", rendered_name="Free Folk")
FLEET = Thing("First Fleet", is_civilian_fleet=False, rendered_name="First Fleet")
SCIENCE_SHIP = Thing("Argo", is_civilian_fleet=True, rendered_name="Argo")
LINKS = make_links(
    country=COUNTRY, target=TARGET, leader=LEADER, other=OTHER_LEADER, system=SYSTEM, planet=PLANET,
    planet2=PLANET_WITHOUT_CLASS, war=WAR, war2=UNNAMED_WAR,
)

BASE = dict(
    country=COUNTRY, war=WAR, planet=PLANET, start_date="2300.01.01", end_date="2310.01.01", is_active=False, description="DESCRIPTION",
    attackers="<a>A</a>", defenders="<a>B</a>", attacker_victory=True,
    attacker_exhaustion="10.0%", defender_exhaustion="20.0%",
    federation_name="Fed", federation_title='Alliance "Fed"', old_federation_name="OldFed",
    federation_partners="<a>P</a> and <a>Q</a>", is_associate=False, changed_role=False, federation_member_count=3,
    faction=FACTION, faction_type="Free Folk", fleet=FLEET,
    old_name="Old", new_name="New", old_class="Barren", new_class="Gaia", policy="Pol", selected="Sel",
    old_choice="Old", new_choice="New", old_age="Steam Age", new_age="Atomic Age", regressed=False,
)
# optional parts of an event, which select between the variants of a sentence
OPTIONAL = dict(leader=LEADER, system=SYSTEM, target_country=TARGET)
# sentences that also work for events without a planet
PLANET_IS_OPTIONAL = {
    "discovered_anomaly", "researched_anomaly", "excavated_dig_site", "colonization", "capital_relocation",
    "sector_creation", "ruled_empire",
}
# values of the description_key and related fields that select a variant
KEY_VARIANTS = [
    dict(description_key=key, call_type=key, contact_status=key, cause=key, crisis_key=key)
    for key in ["", "primary", "overlord", "offensive", "defensive", "alliance", "other", "in_progress", "aborted",
                "finished", "war", "subject", *GALACTIC_CRISIS_PHRASES]
]
OTHER_VARIANTS = [
    {},
    dict(attacker_victory=False),
    dict(changed_role=True),
    dict(changed_role=True, is_associate=True),
    dict(is_associate=True),
    dict(federation_member_count=0),
    dict(regressed=True),
    dict(fleet=SCIENCE_SHIP),
    dict(planet=PLANET_WITHOUT_CLASS),
    dict(planet=None),
    dict(war=UNNAMED_WAR),
    dict(end_date=None, is_active=True),
    dict(faction_type="The Free Folk"),
    dict(faction_type="Supremacists"),
    dict(assignment_count=3),
    dict(assignment_count=2, leaders=[LEADER, OTHER_LEADER], other_envoy_count=0),
    dict(assignment_count=5, leaders=[LEADER, OTHER_LEADER, LEADER], other_envoy_count=2),
    dict(assignment_count=5, leaders=[LEADER], other_envoy_count=0),
]


def event_variants(event_type):
    for present in itertools.product([False, True], repeat=len(OPTIONAL)):
        optional = {k: v for (k, v), p in zip(OPTIONAL.items(), present) if p}
        for variant in OTHER_VARIANTS:
            if variant.get("planet", True) is None:
                if event_type not in PLANET_IS_OPTIONAL:
                    continue
                variant = {"planet": None}
                event = {k: v for k, v in {**BASE, **optional, "event_type": event_type}.items() if k != "planet"}
                yield event
                continue
            yield {**BASE, **optional, **variant, "event_type": event_type}
    for variant in KEY_VARIANTS:
        yield {**BASE, **OPTIONAL, **variant, "event_type": event_type}
        yield {**BASE, **variant, "event_type": event_type}


def render(event):
    return str(render_event(event, LINKS))


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


SENTENCE_TYPES = [name for name, descriptor in REGISTRY.items() if descriptor.sentence]


@pytest.mark.parametrize("event_type", SENTENCE_TYPES)
def test_sentence_renders_in_english(event_type):
    for event in event_variants(event_type):
        html = render(event)
        assert "eventtext" in html
        text = text_of(html)
        assert "%(" not in text and MARKER not in text
        assert "Undefined" not in text


@pytest.mark.parametrize("event_type", SENTENCE_TYPES)
def test_sentence_is_translated(event_type, pseudo_translation):
    for event in event_variants(event_type):
        html = render(event)
        # the whole sentence goes through the catalog, not just parts of it
        eventtext = html.split('class="eventtext">', 1)[1].split("</span>", 1)[0]
        assert MARKER in eventtext, eventtext
        assert "%(" not in html


def test_english_sentences():
    event = {**BASE, **OPTIONAL, "event_type": "war", "description_key": "defensive"}
    assert text_of(render(event)) == (
        "2300.01.01: Under the rule of Scientist Jane Doe, the EmpireOne entered the Great War "
        "due to their defensive pact with the EmpireTwo."
    )
    event = {**BASE, "target_country": TARGET, "event_type": "defensive_pact"}
    assert text_of(render(event)) == (
        "2300.01.01 - 2310.01.01: The EmpireOne entered a defensive pact with the EmpireTwo."
    )
    event = {**BASE, "event_type": "envoy_improving_relations", "target_country": TARGET,
             "leaders": [LEADER, OTHER_LEADER, LEADER], "other_envoy_count": 2, "assignment_count": 7}
    assert text_of(render(event)).endswith(
        "The EmpireOne sent Scientist Jane Doe, Official John Roe, Scientist Jane Doe and 2 other envoys "
        "to improve relations with the EmpireTwo (7 assignments)."
    )
    event["leaders"], event["other_envoy_count"] = [LEADER, OTHER_LEADER], 0
    assert " sent Scientist Jane Doe and Official John Roe to " in text_of(render(event))
    event = {**BASE, "event_type": "leader_died"}
    assert text_of(render(event)).endswith("An unknown leader died or retired.")


GERMAN = r'''msgid ""
msgstr ""
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

msgctxt "leader class"
msgid "Scientist"
msgstr "Wissenschaftler"

msgctxt "leader with class"
msgid "%(leader_class)s %(leader)s"
msgstr "%(leader_class)s %(leader)s"

msgid "Under the rule of %(leader)s, the %(country)s entered a defensive pact with the %(target)s."
msgstr "Unter der Herrschaft von %(leader)s schloss %(country)s einen Verteidigungspakt mit %(target)s."

msgid "The %(country)s entered a defensive pact with the %(target)s."
msgstr "%(country)s schloss einen Verteidigungspakt mit %(target)s."

msgid "The %(country)s sent %(envoys)s to harm relations with the %(target)s (%(num)s assignment)."
msgid_plural "The %(country)s sent %(envoys)s to harm relations with the %(target)s (%(num)s assignments)."
msgstr[0] "%(country)s entsandte %(envoys)s, um die Beziehungen zu %(target)s zu schaedigen (%(num)s Auftrag)."
msgstr[1] "%(country)s entsandte %(envoys)s, um die Beziehungen zu %(target)s zu schaedigen (%(num)s Auftraege)."

msgid "%(envoys)s and %(num)s other envoy"
msgid_plural "%(envoys)s and %(num)s other envoys"
msgstr[0] "%(envoys)s und %(num)s weiterer Gesandter"
msgstr[1] "%(envoys)s und %(num)s weitere Gesandte"
'''


def test_translation_can_reorder_the_sentence(tmp_path, monkeypatch):
    path = tmp_path / "de.po"
    path.write_text(GERMAN, encoding="utf-8")
    translations = i18n.POTranslations(path)
    monkeypatch.setattr(i18n, "_translations", lambda: translations)
    event = {**BASE, **OPTIONAL, "event_type": "defensive_pact"}
    assert text_of(render(event)) == (
        "2300.01.01 - 2310.01.01: Unter der Herrschaft von Wissenschaftler Jane Doe schloss EmpireOne "
        "einen Verteidigungspakt mit EmpireTwo."
    )
    del event["leader"]
    assert text_of(render(event)).endswith("EmpireOne schloss einen Verteidigungspakt mit EmpireTwo.")
    event = {**BASE, "event_type": "envoy_harming_relations", "target_country": TARGET, "leaders": [LEADER],
             "other_envoy_count": 2, "assignment_count": 4}
    assert text_of(render(event)).endswith(
        "EmpireOne entsandte Wissenschaftler Jane Doe und 2 weitere Gesandte, "
        "um die Beziehungen zu EmpireTwo zu schaedigen (4 Auftraege)."
    )


def test_galactic_crisis_phrases_are_sentences_about_the_country():
    for key, sentence in GALACTIC_CRISIS_PHRASES.items():
        assert sentence.startswith("The %(country)s "), key
        assert sentence.endswith("."), key


def test_leader_classes_are_translatable(pseudo_translation):
    assert event_registry.leader_class_label("scientist") == f"{MARKER} Scientist"
    # classes the dashboard does not know are shown as they are
    assert event_registry.leader_class_label("space_pirate") == "Space_pirate"


@pytest.mark.parametrize("storyline_type", list(STORYLINE_REGISTRY))
@pytest.mark.parametrize("translated", [False, True])
def test_storyline_cards(storyline_type, translated, monkeypatch):
    if translated:
        translations = PseudoTranslations()
        monkeypatch.setattr(i18n, "_translations", lambda: translations)
    descriptor = STORYLINE_REGISTRY[storyline_type]
    for state in [*descriptor.state_labels, "failed"]:
        for present in itertools.product([False, True], repeat=4):
            optional = {
                k: v for (k, v), p in zip(
                    dict(planet=PLANET, system=SYSTEM, leader=LEADER, target_country=TARGET).items(), present
                ) if p
            }
            optional.setdefault("planet", None)
            if optional["planet"] is None:
                del optional["planet"]
            stages = [
                {"start_date": "2300.01.01", "index": 1},
                {"start_date": "2300.02.01", "index": 2, "detail": "Something", "country": TARGET, "leader": LEADER},
            ]
            for stage_list in ([], stages):
                event = dict(
                    BASE, **optional, event_type="storyline", storyline_type=storyline_type, state=state,
                    title="Title", stages=stage_list, stage_count=len(stage_list),
                )
                html = render(event)
                text = text_of(html)
                assert "%(" not in text and "Undefined" not in text
                if translated:
                    assert MARKER in html
                    assert f"{MARKER} {descriptor.state_labels.get(state, state)}" in text or state == "failed"


def _event(event_type, text):
    from stellarisdashboard import datamodel

    return datamodel.HistoricalEvent(
        event_type=datamodel.HistoricalEventType[event_type], db_description=datamodel.SharedDescription(text=text)
    )


def test_description_fields_keep_the_parts_of_a_description_apart():
    assert _event("war", "defensive").description_fields == dict(call_type="defensive")
    assert _event("first_contact_process", "aborted").description_fields == dict(contact_status="aborted")
    assert _event("country_destroyed", "war").description_fields == dict(cause="war")
    assert _event("galactic_crisis", "synth_queen_war").description_fields == dict(crisis_key="synth_queen_war")
    fields = _event("pre_ftl_age_change", "atomic_age|stone_age").description_fields
    assert fields["regressed"] is True
    assert _event("researched_technology", "tech_x").description_fields == {}


def test_government_reform_lines_are_whole_sentences(pseudo_translation):
    from stellarisdashboard import datamodel

    old = datamodel.Government(gov_type="gov_a", authority="auth_oligarchic", ethics_1="ethic_x", civic_1="civic_a")
    new = datamodel.Government(gov_type="gov_b", authority="auth_oligarchic", ethics_1="ethic_y", civic_1="civic_a",
                               civic_2="civic_b")
    assert new.get_reform_description_lines(old) == [
        f'{MARKER} Type changed from "A" to "B".',
        f"{MARKER} Adopted civics B.",
        f"{MARKER} Abandoned ethics X.",
        f"{MARKER} Embraced ethics Y.",
    ]
