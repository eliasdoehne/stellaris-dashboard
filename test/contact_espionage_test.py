from stellarisdashboard.datamodel import date_to_days, render_first_contact_description
from stellarisdashboard.parsing.timeline import first_contact_end_day

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
