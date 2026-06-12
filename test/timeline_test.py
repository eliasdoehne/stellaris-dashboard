import pytest
from stellarisdashboard import datamodel
from stellarisdashboard.parsing import timeline

def test_official_envoy_event_no_duplicates(timeline_helper):
    # Setup leaders
    envoy = timeline_helper.add_leader(10, "envoy")
    official = timeline_helper.add_leader(11, "official")

    # Day 30 - assignments start
    leaders_state = {
        10: {"class": "envoy", "location": {"assignment": "galactic_community"}},
        11: {"class": "official", "location": {"assignment": "galactic_community"}},
    }
    timeline_helper.run_processor(timeline.EnvoyEventProcessor, 30, leaders_state)

    # Verify both envoy and official got one event created
    events = timeline_helper.session.query(datamodel.HistoricalEvent).all()
    assert len(events) == 2
    assert all(e.end_date_days is None for e in events)

    # Day 60 - same assignments
    timeline_helper.run_processor(timeline.EnvoyEventProcessor, 60, leaders_state)

    # Verify no duplicates were created
    events = timeline_helper.session.query(datamodel.HistoricalEvent).all()
    assert len(events) == 2

    # Day 90 - official changes assignment, envoy goes idle
    leaders_state_new = {
        10: {"class": "envoy", "location": {"assignment": "idle"}},
        11: {"class": "official", "location": {"assignment": "federation", "id": 123}},
    }
    timeline_helper.run_processor(
        timeline.EnvoyEventProcessor,
        90,
        leaders_state_new,
        extra_gamestate={"federation": {123: {"name": "The Galactic Alliance"}}}
    )

    # Verify end dates and new assignment event
    all_events = (
        timeline_helper.session.query(datamodel.HistoricalEvent)
        .order_by(datamodel.HistoricalEvent.start_date_days)
        .all()
    )
    assert len(all_events) == 3
    
    # Old envoy event
    old_envoy_event = next(e for e in all_events if e.leader_id == envoy.leader_id)
    assert old_envoy_event.end_date_days == 89
    
    # Old official event
    old_official_event = next(e for e in all_events if e.leader_id == official.leader_id and e.event_type == datamodel.HistoricalEventType.envoy_community)
    assert old_official_event.end_date_days == 89
    
    # New official event
    new_official_event = next(e for e in all_events if e.leader_id == official.leader_id and e.event_type == datamodel.HistoricalEventType.envoy_federation)
    assert new_official_event.start_date_days == 90
    assert new_official_event.end_date_days is None
