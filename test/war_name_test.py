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
