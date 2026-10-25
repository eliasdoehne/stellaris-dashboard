import json

from stellarisdashboard import datamodel


def _war(name=None):
    war = datamodel.War(name=name)
    for country_name, is_attacker in [("Attacker", True), ("Defender", False)]:
        country = datamodel.Country(
            country_name=json.dumps({"key": country_name, "literal": "yes"})
        )
        war.participants.append(
            datamodel.WarParticipant(country=country, is_attacker=is_attacker)
        )
    return war


def test_war_uses_ingame_name():
    war = _war(name=json.dumps({"key": "Great War", "literal": "yes"}))
    assert war.ingame_name == "Great War"
    assert war.rendered_name == "Great War"


def test_war_without_name_lists_participants():
    war = _war()
    assert war.ingame_name is None
    assert war.rendered_name.startswith("Attacker")
    assert "vs Defender" in war.rendered_name


def test_war_in_heaven_names_are_disambiguated_by_main_parties():
    war = _war(name=json.dumps({"key": "NAME_The_War_in_Heaven"}))
    assert war.name_is_shared
    assert war.rendered_name.startswith("The War in Heaven (Attacker") and " vs Defender" in war.rendered_name
    assert not _war(name=json.dumps({"key": "Great War", "literal": "yes"})).name_is_shared
