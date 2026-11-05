from types import SimpleNamespace

from stellarisdashboard import datamodel
from stellarisdashboard.dashboard_app import event_grouping

ET = datamodel.HistoricalEventType
SS = datamodel.StorylineState
PLAYER = SimpleNamespace(is_player=True)
AI = SimpleNamespace(is_player=False)


def _event(country_id, target_id, day, event_type=ET.first_contact, status=None, country=AI):
    return SimpleNamespace(
        event_type=event_type,
        country_id=country_id,
        target_country_id=target_id,
        start_date_days=day,
        storyline_id=None,
        country=country,
        db_description=SimpleNamespace(text=status) if status else None,
    )


def _storyline(country_id, target_id, start, end):
    return SimpleNamespace(
        storyline_type=datamodel.StorylineType.first_contact,
        country_id=country_id,
        target_country_id=target_id,
        start_date_days=start,
        end_date_days=end,
    )


def test_mirrored_contact_events_are_shown_once_from_the_players_side():
    ai_side, player_side = _event(2, 1, 100), _event(1, 2, 100, country=PLAYER)
    other = _event(2, 3, 100)
    later = _event(1, 2, 500)
    assert event_grouping.drop_mirrored_contacts([ai_side, other, player_side, later]) == [player_side, other, later]


def test_the_finished_side_of_mirrored_processes_is_kept():
    aborted = _event(1, 2, 100, ET.first_contact_process, "aborted", country=PLAYER)
    finished = _event(2, 1, 100, ET.first_contact_process, "finished")
    assert event_grouping.drop_mirrored_contacts([aborted, finished]) == [finished]


def test_contact_events_of_different_types_are_not_mirrors():
    events = [_event(1, 2, 100), _event(2, 1, 100, ET.first_contact_process, "finished")]
    assert event_grouping.drop_mirrored_contacts(events) == events


def test_the_contact_is_made_once_either_side_makes_it():
    assert event_grouping.combined_contact_state([SS.abandoned, SS.completed]) == SS.completed
    assert event_grouping.combined_contact_state([SS.abandoned, SS.active]) == SS.active
    assert event_grouping.combined_contact_state([SS.abandoned]) == SS.abandoned


def test_the_other_side_joins_an_overlapping_contact_of_the_same_pair():
    item = {"sides": [_storyline(1, 2, 0, 1000)]}
    items = {frozenset((1, 2)): [item]}
    assert event_grouping.find_contact_partner(items, _storyline(2, 1, 0, 1000)) is item
    assert event_grouping.find_contact_partner(items, _storyline(2, 1, 2000, None)) is None
    assert event_grouping.find_contact_partner(items, _storyline(1, 2, 0, 1000)) is None  # same side
    assert event_grouping.find_contact_partner(items, _storyline(2, 3, 0, 1000)) is None


def test_stages_name_their_side_unless_both_reached_them_together():
    stages = [
        ((0, 1), {"index": 1, "detail": "contact detected"}, "A"),
        ((0, 1), {"index": 1, "detail": "contact detected"}, "B"),
        ((50, 2), {"index": 2, "detail": "investigation progressed"}, "B"),
    ]
    labelled = [stage for _, stage, _ in event_grouping.label_contact_stages(stages)]
    assert labelled == [
        {"index": 1, "detail": "contact detected"},
        {"index": 2, "detail": "investigation progressed", "country": "B"},
    ]


def test_lines_from_both_sides_drop_their_numbers():
    stages = [
        ((0, 1), {"index": 1, "detail": "contact detected"}, "A"),
        ((0, 1), {"index": 1, "detail": "contact detected"}, "B"),
        ((50, 2), {"index": 2, "detail": "investigation progressed"}, "A"),
        ((60, 2), {"index": 2, "detail": "investigation progressed"}, "B"),
        ((70, 3), {"index": 3}, "B"),  # nothing else to show
    ]
    labelled = [stage for _, stage, _ in event_grouping.label_contact_stages(stages)]
    assert labelled == [
        {"detail": "contact detected"},
        {"detail": "investigation progressed", "country": "A"},
        {"detail": "investigation progressed", "country": "B"},
        {"index": 3, "country": "B"},
    ]


def test_both_sides_of_a_first_contact_share_a_row():
    a, b = _storyline(1, 2, 100, 200), _storyline(2, 1, 150, 300)
    assert event_grouping.storyline_rows([a, b]) == [[a, b]]


def test_separate_contacts_and_other_storylines_get_their_own_rows():
    early, late = _storyline(1, 2, 100, 200), _storyline(2, 1, 900, None)
    third_party = _storyline(1, 3, 100, 200)
    rift = SimpleNamespace(storyline_type=datamodel.StorylineType.astral_rift)
    assert event_grouping.storyline_rows([early, third_party, rift, late]) == [[early], [third_party], [rift], [late]]
