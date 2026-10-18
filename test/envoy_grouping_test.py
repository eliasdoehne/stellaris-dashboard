from types import SimpleNamespace

from stellarisdashboard import datamodel
from stellarisdashboard.dashboard_app.event_grouping import group_envoy_assignments

ET = datamodel.HistoricalEventType


def _event(start, end, event_type=ET.envoy_harming_relations, target=2, leader=None):
    return SimpleNamespace(
        event_type=event_type,
        country_id=1,
        target_country_id=target,
        description_id=None,
        start_date_days=start,
        end_date_days=end,
        leader=leader,
    )


def _spans(events):
    return [
        (len(g.events), g.events[0].start_date_days, g.end_date_days)
        for g in group_envoy_assignments(events)
    ]


def test_rotating_assignments_merge_into_one_entry():
    events = [_event(0, 359), _event(360, 719), _event(900, 1259), _event(1500, None)]
    assert _spans(events) == [(4, 0, None)]


def test_long_gap_starts_a_new_entry():
    assert _spans([_event(0, 359), _event(1200, 1559)]) == [(1, 0, 359), (1, 1200, 1559)]


def test_overlapping_assignments_merge():
    assert _spans([_event(0, 700), _event(100, 300)]) == [(2, 0, 700)]


def test_different_targets_and_activities_stay_apart():
    events = [
        _event(0, 359),
        _event(0, 359, target=3),
        _event(360, 719, event_type=ET.envoy_improving_relations),
    ]
    assert _spans(events) == [(1, 0, 359), (1, 0, 359), (1, 360, 719)]


def test_other_events_are_not_grouped():
    events = [
        _event(0, 359, event_type=ET.espionage_operation),
        _event(360, 719, event_type=ET.espionage_operation),
    ]
    assert _spans(events) == [(1, 0, 359), (1, 360, 719)]


def test_open_assignment_of_departed_leader_ends_with_the_leader():
    gone = SimpleNamespace(is_active=False, last_date=500)
    assert _spans([_event(0, None, leader=gone)]) == [(1, 0, 499)]
