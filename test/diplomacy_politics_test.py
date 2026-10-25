from stellarisdashboard.datamodel import SUBJECT_PRESET_VARIANT_RE, date_to_days, split_federation_description
from stellarisdashboard.parsing.timeline import (
    MEGASTRUCTURE_STAGES,
    PassedResolution,
    custodian_role_and_start,
    federation_id_of_description,
    federation_level_of_description,
    is_finished_megastructure,
    megastructure_final_type,
    passed_resolutions,
    president_start_date,
)


def days(year, month=1, day=1):
    return (year - 2200) * 360 + (month - 1) * 30 + (day - 1)


def test_passed_resolutions_skips_unpassed_and_malformed():
    resolutions = {
        0: {"type": "resolution_b", "pass_date": "2260.12.30", "country": 5},
        1: {"type": "resolution_a", "pass_date": "2250.01.01"},  # proposer gone
        2: {"type": "resolution_c", "country": 5},  # never passed
        3: {"type": "resolution_d", "pass_date": "garbage"},
        4: "none",
        5: {"type": "resolution_custodian", "pass_date": "2300.01.01", "country": 5, "target": 9},
    }
    assert passed_resolutions(resolutions) == [
        PassedResolution("resolution_a", days(2250), None, None),
        PassedResolution("resolution_b", days(2260, 12, 30), 5, None),
        PassedResolution("resolution_custodian", days(2300), 5, 9),
    ]
    assert passed_resolutions("none") == []


def nomination(target, year):
    return PassedResolution("resolution_galacticreforms_nominate_custodian", days(year), 1, target)


def proclamation(year):
    return PassedResolution("resolution_custodian_form_empire", days(year), 8, None)


def test_custodian_start_is_the_nomination_of_the_current_custodian():
    passed = [nomination(7, 2400), nomination(8, 2440)]
    assert custodian_role_and_start(passed, 8, days(2450)) == ("custodian", days(2440))
    assert custodian_role_and_start(passed, 7, days(2450)) == ("custodian", days(2400))
    assert custodian_role_and_start(passed, 9, days(2450)) == ("custodian", days(2450))
    assert custodian_role_and_start(passed, 8, days(2420)) == ("custodian", days(2420))


def test_imperium_proclamation_makes_the_leader_emperor_until_a_new_nomination():
    passed = [nomination(8, 2440), proclamation(2460), nomination(9, 2480)]
    assert custodian_role_and_start(passed, 8, days(2450)) == ("custodian", days(2440))
    assert custodian_role_and_start(passed, 8, days(2470)) == ("emperor", days(2460))
    assert custodian_role_and_start(passed, 9, days(2490)) == ("custodian", days(2480))


def test_subject_preset_variants_reduce_to_the_base_preset():
    for preset in ("preset_vassal_mean_02", "preset_vassal_oppressive_ai", "preset_vassal_balanced", "preset_vassal"):
        assert SUBJECT_PRESET_VARIANT_RE.sub("", preset) == "preset_vassal"


def test_federation_descriptions():
    text = '16777217|3|military_federation|{"key": "a|b"}'
    assert split_federation_description(text) == ("16777217", "3", "military_federation", '{"key": "a|b"}')
    assert split_federation_description("3") == ("3", "", "", "")
    assert federation_id_of_description(text) == 16777217
    assert federation_level_of_description(text) == 3
    assert federation_level_of_description('0||default_federation|{"key": "x"}') is None
    for junk in (None, "", "abc|1|t|n"):
        assert federation_id_of_description(junk) is None


def test_president_start_date_uses_exact_date_only_when_plausible():
    now = date_to_days("2300.05.01")
    previous = date_to_days("2290.01.01")
    assert president_start_date("2295.03.04", previous, now) == date_to_days("2295.03.04")
    assert president_start_date("2295.03.04", None, now) == date_to_days("2295.03.04")
    # a succession date before the previous term (re-election) or in the future is ignored
    assert president_start_date("2280.03.04", previous, now) == now
    assert president_start_date("2301.01.01", previous, now) == now
    assert president_start_date(None, previous, now) == now


def test_megastructure_stages():
    assert megastructure_final_type("dyson_sphere_0") == "dyson_sphere_5"
    assert megastructure_final_type("ring_world_2_intermediate") == "ring_world_3_intermediate"
    assert not is_finished_megastructure("dyson_sphere_4")
    assert is_finished_megastructure("dyson_sphere_5")
    assert is_finished_megastructure("think_tank_4")  # the Groik Nexus upgrade
    for untracked in ("hyper_relay", "dyson_sphere_ruined", "dyson_swarm_3", "gateway_final", "shroud_seal", None, 3):
        assert megastructure_final_type(untracked) is None
        assert not is_finished_megastructure(untracked)
    # every family's final key is itself the first finished stage
    for final, _, finished_index in MEGASTRUCTURE_STAGES.values():
        assert MEGASTRUCTURE_STAGES[final] == (final, finished_index, finished_index)
