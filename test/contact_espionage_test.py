from stellarisdashboard.datamodel import (
    date_to_days,
    render_espionage_description,
    render_first_contact_description,
)
from stellarisdashboard.parsing.timeline import (
    espionage_completion_days,
    espionage_start_day,
    first_contact_end_day,
)

START = date_to_days("2243.05.16")


def test_first_contact_end_day_open_while_in_progress():
    completed = [{"stage": "caravaneers_stage_1", "date": "2244.05.13"}]
    assert first_contact_end_day("in_progress", completed, START) is None


def test_first_contact_end_day_is_last_completed_stage():
    completed = [
        {"stage": "caravaneers_stage_1", "date": "2244.05.13"},
        {"stage": "caravaneers_stage_2", "date": "2245.08.01"},
    ]
    assert first_contact_end_day("finished", completed, START) == date_to_days("2245.08.01")
    # a lone completed stage is stored as a plain dict
    assert first_contact_end_day("aborted", completed[0], START) == date_to_days("2244.05.13")


def test_first_contact_end_day_falls_back_to_start():
    for completed in (None, "none", [], [{"date": "garbage"}, {"stage": "x"}, "none"]):
        assert first_contact_end_day("finished", completed, START) == START


def test_render_first_contact_description():
    assert render_first_contact_description("finished") == "made first contact with"
    assert render_first_contact_description("in_progress") == "began first contact with"
    assert render_first_contact_description("aborted") == "abandoned first contact with"
    assert render_first_contact_description("") == "made contact with"


def log(*entries):
    return [{"date": date, "info": info} for date, info in entries]


def test_espionage_start_day_is_first_roll():
    operation = {"log": log(("2289.06.22", 0), ("2289.07.22", 0))}
    assert espionage_start_day(operation) == date_to_days("2289.06.22")
    for operation in ({}, {"log": []}, {"log": "none"}, {"log": [{"date": "garbage"}]}):
        assert espionage_start_day(operation) is None


def test_espionage_completion_of_one_off_operation_is_the_roll_reaching_full_info():
    operation = {
        "type": "operation_gather_information",
        "log": log(("2290.04.22", 9), ("2290.05.22", 10), ("2290.06.22", 10)),
    }
    assert espionage_completion_days(operation) == [date_to_days("2290.05.22")]
    operation["log"] = log(("2290.04.22", 9))
    assert espionage_completion_days(operation) == []


def test_espionage_completion_of_repeatable_operation_is_the_completed_list():
    operation = {
        "type": "operation_extort_favors",
        "log": log(("2299.05.06", 10)),
        "completed": [{"date": "2299.06.06"}, {"date": "garbage"}, {"date": "2299.11.06"}],
    }
    assert espionage_completion_days(operation) == [
        date_to_days("2299.06.06"),
        date_to_days("2299.11.06"),
    ]
    # a repeatable operation that has not completed yet is not counted at full info
    operation = {"type": "operation_acquire_asset", "log": log(("2290.01.29", 10))}
    assert espionage_completion_days(operation) == []
    assert espionage_completion_days({"completed": {"date": "2290.02.29"}}) == [date_to_days("2290.02.29")]


def test_render_espionage_description():
    assert render_espionage_description("operation_steal_technology|822083587") == "Steal Technology"
    assert render_espionage_description("operation_gather_information") == "Gather Information"
