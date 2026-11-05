"""Groups ledger events: repeated envoy assignments per (country, target, activity), and both sides of a first contact."""
import collections
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


# Both empires of a first contact are recorded: each side's own process (storyline) with its stages,
# and the communications relation once per direction. The ledger shows each contact once.
MIRRORED_CONTACT_TYPES = frozenset(
    {datamodel.HistoricalEventType.first_contact, datamodel.HistoricalEventType.first_contact_process}
)
# best first: once one side makes contact the game aborts the other side's process
STATE_RANK = [
    datamodel.StorylineState.completed,
    datamodel.StorylineState.active,
    datamodel.StorylineState.paused,
    datamodel.StorylineState.failed,
    datamodel.StorylineState.abandoned,
]


def contact_pair(record) -> frozenset:
    """The two countries of a contact event or storyline, in either direction."""
    return frozenset((record.country_id, record.target_country_id))


def spans_overlap(a, b) -> bool:
    a_end = float("inf") if a.end_date_days is None else a.end_date_days
    b_end = float("inf") if b.end_date_days is None else b.end_date_days
    return a.start_date_days <= b_end and b.start_date_days <= a_end


def combined_contact_state(states):
    """The state of a first contact as a whole: made if either side made it."""
    return min(states, key=STATE_RANK.index)


def find_contact_partner(contact_items: dict, storyline) -> Optional[dict]:
    """The item of the other side of this first contact storyline, if already built."""
    if storyline.storyline_type != datamodel.StorylineType.first_contact:
        return None
    for item in contact_items.get(contact_pair(storyline), ()):
        sides = item["sides"]
        if all(s.country_id != storyline.country_id for s in sides) and any(
            spans_overlap(s, storyline) for s in sides
        ):
            return item
    return None


def storyline_rows(storylines: Sequence) -> List[list]:
    """Which of these storylines (in the order of their first shown event) share a ledger row: each its own,
    except both sides of a first contact, merged as build_items merges them."""
    rows, contact_items = [], collections.defaultdict(list)
    for storyline in storylines:
        item = find_contact_partner(contact_items, storyline)
        if item is not None:
            item["sides"].append(storyline)
            continue
        item = {"sides": [storyline]}
        rows.append(item)
        if storyline.storyline_type == datamodel.StorylineType.first_contact:
            contact_items[contact_pair(storyline)].append(item)
    return [item["sides"] for item in rows]


def label_contact_stages(stages: list) -> list:
    """(sort key, stage dict, country) of both sides, sorted: each stage names its side, except stages both
    sides reached on the same day, shown once."""
    countries = collections.defaultdict(set)
    for key, stage, country in stages:
        countries[(key, stage.get("detail"))].add(country)
    result, shown = [], set()
    for key, stage, country in stages:
        shared = (key, stage.get("detail"))
        if len(countries[shared]) > 1:
            if shared in shown:
                continue
            shown.add(shared)
        elif country is not None:
            stage = dict(stage, country=country)
        result.append((key, stage, country))
    return result


def _mirror_key(event):
    if (
        event.event_type not in MIRRORED_CONTACT_TYPES
        or event.storyline_id is not None
        or event.country_id is None
        or event.target_country_id is None
    ):
        return None
    return event.event_type, contact_pair(event), event.start_date_days


def _contact_rank(event):
    status = event.db_description.text if event.db_description is not None else None
    return status != "finished", not (event.country is not None and event.country.is_player)


def drop_mirrored_contacts(events: Sequence) -> list:
    """Keeps one of each mirrored pair of contact events outside storylines (A->B and B->A of the same
    type on the same day): the finished process, else the player's side. It takes the first one's place."""
    best = {}
    for event in events:
        key = _mirror_key(event)
        if key is not None and (key not in best or _contact_rank(event) < _contact_rank(best[key])):
            best[key] = event
    result, placed = [], set()
    for event in events:
        key = _mirror_key(event)
        if key is None:
            result.append(event)
        elif key not in placed:
            placed.add(key)
            result.append(best[key])
    return result
