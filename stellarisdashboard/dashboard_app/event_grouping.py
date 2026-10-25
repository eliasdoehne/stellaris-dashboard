"""Groups a country's repeated envoy assignments into one ledger entry per (country, target, activity)."""
import dataclasses
from typing import List, Optional, Sequence

from stellarisdashboard import datamodel

# The AI rotates its envoys between jobs about once a year, so a job lapses for up to a year between
# assignments; longer gaps start a new entry.
ENVOY_MAX_GAP_DAYS = 400


@dataclasses.dataclass
class EventGroup:
    events: list  # chronological; the first event represents the group
    end_date_days: Optional[int]  # None while the group is ongoing


def envoy_end_days(event) -> Optional[int]:
    """End of an envoy assignment; DBs from before the fix keep assignments of departed leaders open."""
    if event.end_date_days is not None:
        return event.end_date_days
    leader = event.leader
    if leader is not None and leader.is_active is False and leader.last_date is not None:
        return max(event.start_date_days, leader.last_date - 1)
    return None


def group_envoy_assignments(
    events: Sequence, max_gap_days: int = ENVOY_MAX_GAP_DAYS
) -> List[EventGroup]:
    """Group chronologically sorted events: envoy assignments with the same country, target and activity
    merge while they overlap or follow within max_gap_days, whichever envoy holds them; other events stay
    single. Groups are ordered by their first event."""
    groups = []
    latest_group = {}
    for event in events:
        if event.event_type not in datamodel.ENVOY_EVENT_TYPES:
            groups.append(EventGroup([event], event.end_date_days))
            continue
        end = envoy_end_days(event)
        key = (
            event.event_type,
            event.country_id,
            event.target_country_id,
            event.description_id,
        )
        group = latest_group.get(key)
        if group is not None and (
            group.end_date_days is None
            or event.start_date_days <= group.end_date_days + max_gap_days
        ):
            group.events.append(event)
            if end is None or group.end_date_days is None:
                group.end_date_days = None
            else:
                group.end_date_days = max(group.end_date_days, end)
            continue
        latest_group[key] = group = EventGroup([event], end)
        groups.append(group)
    return groups
