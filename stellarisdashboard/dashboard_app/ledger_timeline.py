"""The event ledger as one filterable timeline: which events to show, paged by year.

Filters come from the URL: a detail level (by event tier), categories, an
optional focus entity (empire, leader, war, system or planet) and a window of
years. Without a focus the galaxy-wide view counts other empires' events one
tier lower than the player's own, so the default view isn't flooded by AI
diplomacy.

Everything is counted in rows, as shown: a storyline (with both sides of a
first contact), a run of envoy assignments or a mirrored pair of contact
events is one row. Each row is listed once, in the year it started, with all
its events, so paging, the decade list, the category chips and "+ N more"
agree with the rows on the page.
"""
import collections
import dataclasses
from typing import NamedTuple, Optional

import sqlalchemy
from flask import request, url_for

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import event_grouping, event_registry, utils
from stellarisdashboard.dashboard_app.event_registry import Category, Tier
from stellarisdashboard.i18n import N_

HE = datamodel.HistoricalEvent

# (key, label, minimum tier); the labels are translated where they are shown
LEVELS = [
    # Translators: detail levels of the event ledger, from the fewest events to all of them
    ("headlines", N_("Headlines"), Tier.HEADLINE),
    ("notable", N_("Notable"), Tier.MAJOR),
    ("detailed", N_("Detailed"), Tier.ROUTINE),
    ("everything", N_("Everything"), Tier.TRIVIA),
]
DEFAULT_LEVEL = "notable"
_LEVEL_TIERS = {key: tier for key, _, tier in LEVELS}

# a page holds whole years until it has at least this many events
PAGE_EVENTS = 120

# the labels are translated where they are shown
FOCUS_KINDS = {
    # Translators: what kind of thing an event ledger page is about, shown above its name
    "country": (datamodel.Country, N_("Empire")),
    "leader": (datamodel.Leader, N_("Leader")),
    "war": (datamodel.War, N_("War")),
    "system": (datamodel.System, N_("System")),
    "planet": (datamodel.Planet, N_("Planet")),
}

DAYS_PER_YEAR = 360

# ids per IN (...) query, well below SQLite's limit on query parameters
_MAX_IDS = 5000


def _days(year: int) -> int:
    return (year - 2200) * DAYS_PER_YEAR


def year_of(days) -> int:
    # events from before the game started (leaders hired earlier) count as its first year
    return max(int(days // DAYS_PER_YEAR) + 2200, 2200)


@dataclasses.dataclass
class LedgerParams:
    game_id: str = ""
    level: str = DEFAULT_LEVEL
    categories: frozenset = frozenset(Category)
    newest_first: bool = True
    before: Optional[int] = None  # newest first: show years < before
    after: Optional[int] = None  # oldest first: show years > after
    year: Optional[int] = None  # a single year
    # htmx fragments: "year" is one year's section, "page" the next page's sections
    part: Optional[str] = None
    base: Optional[str] = None  # the level of the page a year section was expanded from
    focus_kind: Optional[str] = None
    focus_id: Optional[int] = None
    focus_player: bool = False

    @classmethod
    def from_request(cls, game_id: str) -> "LedgerParams":
        args = request.args
        params = cls(game_id=game_id)
        if args.get("level") in _LEVEL_TIERS:
            params.level = args["level"]
        cats = args.getlist("cat")
        if cats:
            by_value = {c.name.lower(): c for c in Category}
            selected = frozenset(by_value[c] for c in cats if c in by_value)
            if selected:
                params.categories = selected
        params.newest_first = args.get("order") != "oldest"
        params.before = _int_arg("before")
        params.after = _int_arg("after")
        params.year = _int_arg("year", bits=32)  # SQL compares years in days, which must fit 64 bits
        if args.get("part") in ("year", "page"):
            params.part = args["part"]
        params.base = args.get("base") if args.get("base") in _LEVEL_TIERS else None
        for kind in FOCUS_KINDS:
            focus_id = _int_arg(kind)
            if focus_id is not None:
                params.focus_kind, params.focus_id = kind, focus_id
                break
        params.focus_player = params.focus_kind is None and args.get("focus") == "player"
        return params

    def toggled_categories(self, category) -> frozenset:
        """The categories with one switched on or off; never none at all."""
        toggled = self.categories ^ {category}
        return toggled or self.categories

    @property
    def tier(self) -> Tier:
        return _LEVEL_TIERS[self.level]

    @property
    def base_level(self) -> str:
        return self.base or self.level

    def url(self, **changes) -> str:
        """The ledger URL for these params with some changed; paging resets unless given."""
        values = dict(level=self.level, categories=self.categories, newest_first=self.newest_first,
                      focus_kind=self.focus_kind, focus_id=self.focus_id, focus_player=self.focus_player)
        values.update(changes)
        args = {}
        if values["focus_kind"]:
            args[values["focus_kind"]] = values["focus_id"]
        elif values["focus_player"]:
            args["focus"] = "player"
        if values["level"] != DEFAULT_LEVEL:
            args["level"] = values["level"]
        if values["categories"] != frozenset(Category):
            args["cat"] = sorted(c.name.lower() for c in values["categories"])
        if not values["newest_first"]:
            args["order"] = "oldest"
        for key in ("before", "after", "year", "part", "base"):
            if values.get(key) is not None:
                args[key] = values[key]
        return url_for("history_page", game_id=self.game_id, **args)


def _int_arg(name, bits: int = 64) -> Optional[int]:
    """A URL argument as a signed integer; None if missing, malformed or too large for SQLite."""
    try:
        value = int(request.args[name])
    except (KeyError, ValueError):
        return None
    return value if -(2 ** (bits - 1)) <= value < 2 ** (bits - 1) else None


def _types(categories, min_tier) -> list:
    return [
        datamodel.HistoricalEventType[name]
        for name, d in event_registry.REGISTRY.items()
        if d.category in categories and d.tier >= min_tier and name in datamodel.HistoricalEventType.__members__
    ]


def _category(event_type) -> Category:
    return event_registry.REGISTRY[event_type.name].category


_REAL_COUNTRY_TYPES = ("default", "fallen_empire", "awakened_fallen_empire")
# shown even for countries of other types
_ANY_COUNTRY_EVENT_TYPES = (
    datamodel.HistoricalEventType.pre_ftl_age_change,
    datamodel.HistoricalEventType.galactic_crisis,
)


def _visible() -> list:
    """The rules of EventTemplateDictBuilder.build_items as SQL."""
    # counts apply the visibility settings too, or they would promise rows that build_items then hides
    c = config.CONFIG
    Country = datamodel.Country
    rules = []
    if not c.show_everything:
        rules.append(HE.event_is_known_to_player)
    elif c.hide_other_players:
        # without show_everything the rule above already drops every event this one could
        others = sqlalchemy.select(Country.country_id).where(Country.is_other_player)
        other_combats = (
            sqlalchemy.select(datamodel.CombatParticipant.combat_id)
            .join(datamodel.WarParticipant)
            .where(datamodel.WarParticipant.country_id.in_(others))
        )
        rules.append(sqlalchemy.or_(
            HE.event_is_known_to_player,
            HE.country_id.is_(None),
            sqlalchemy.and_(
                HE.country_id.notin_(others),
                sqlalchemy.or_(HE.target_country_id.is_(None), HE.target_country_id.notin_(others)),
                sqlalchemy.or_(HE.combat_id.is_(None), HE.combat_id.notin_(other_combats)),
            ),
        ))
    if not c.show_all_country_types:
        special = sqlalchemy.select(Country.country_id).where(sqlalchemy.or_(
            Country.country_type.is_(None),
            Country.country_type.notin_(_REAL_COUNTRY_TYPES + tuple(datamodel.CRISIS_COUNTRY_TYPES)),
        ))
        rules.append(sqlalchemy.or_(
            HE.country_id.is_(None),
            HE.country_id.notin_(special),
            HE.event_type.in_(_ANY_COUNTRY_EVENT_TYPES),
        ))
    return rules


class _Storyline(NamedTuple):
    storyline_id: int
    storyline_type: datamodel.StorylineType
    start_date_days: int
    end_date_days: Optional[int]
    country_id: Optional[int]
    target_country_id: Optional[int]


class _Leader(NamedTuple):
    is_active: Optional[bool]
    last_date: Optional[int]


class _EnvoyEvent(NamedTuple):
    """What event_grouping.group_envoy_assignments reads of an event."""
    historical_event_id: int
    event_type: datamodel.HistoricalEventType
    country_id: Optional[int]
    target_country_id: Optional[int]
    description_id: Optional[int]
    start_date_days: int
    end_date_days: Optional[int]
    leader: Optional[_Leader]


@dataclasses.dataclass
class Rows:
    """The rows one level shows in the whole game: how many per (year, category), and the storylines and
    envoy assignments listed in each year (with the category of their row), which may have events in
    other years."""
    counts: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    storylines: dict = dataclasses.field(default_factory=lambda: collections.defaultdict(dict))
    envoy_events: dict = dataclasses.field(default_factory=lambda: collections.defaultdict(dict))

    def year_counts(self, categories) -> dict:
        counts = collections.Counter()
        for (year, category), n in self.counts.items():
            if category in categories:
                counts[year] += n
        return dict(counts)


class LedgerQuery:
    def __init__(self, session, game_id: str, params: LedgerParams):
        self.session = session
        self.game_id = game_id
        self.params = params
        self.player_ids = [cid for (cid,) in session.query(datamodel.Country.country_id).filter(datamodel.Country.is_player)]
        if params.focus_player and self.player_ids:
            params.focus_kind, params.focus_id = "country", self.player_ids[0]
        self.focus = None
        if params.focus_kind:
            model, _ = FOCUS_KINDS[params.focus_kind]
            self.focus = session.get(model, params.focus_id)
        self._rows = {}
        self._storylines = None

    def _clause(self, categories, min_tier):
        """Which events pass the level and category filters, the focus and the visibility settings."""
        own = _types(categories, min_tier)
        if self.params.focus_kind:
            column = {
                "leader": HE.leader_id,
                "war": HE.war_id,
                "system": HE.system_id,
                "planet": HE.planet_id,
            }.get(self.params.focus_kind)
            if column is not None:
                entity = column == self.params.focus_id
            else:
                entity = sqlalchemy.or_(HE.country_id == self.params.focus_id,
                                        HE.target_country_id == self.params.focus_id)
            return sqlalchemy.and_(entity, HE.event_type.in_(own), *_visible())
        others = _types(categories, min(min_tier + 1, Tier.HEADLINE))
        involves_player = sqlalchemy.or_(HE.country_id.in_(self.player_ids), HE.target_country_id.in_(self.player_ids))
        return sqlalchemy.and_(
            sqlalchemy.or_(
                sqlalchemy.and_(involves_player, HE.event_type.in_(own)),
                HE.event_type.in_(others),
            ),
            *_visible(),
        )

    def rows(self, level: Optional[str] = None) -> Rows:
        """The rows a level shows in the whole game, in every category."""
        level = level or self.params.level
        if level not in self._rows:
            self._rows[level] = self._count_rows(self._clause(frozenset(Category), _LEVEL_TIERS[level]))
        return self._rows[level]

    def _count_rows(self, clause) -> Rows:
        rows = Rows()
        # single events, where both directions of a mirrored contact event are one row
        mirrored = sqlalchemy.and_(
            HE.event_type.in_(event_grouping.MIRRORED_CONTACT_TYPES),
            HE.country_id.isnot(None),
            HE.target_country_id.isnot(None),
        )
        pair = sqlalchemy.case((mirrored, sqlalchemy.func.printf(
            "%d %d %d", sqlalchemy.func.min(HE.country_id, HE.target_country_id),
            sqlalchemy.func.max(HE.country_id, HE.target_country_id), HE.start_date_days,
        )))
        count = sqlalchemy.func.count
        year = sqlalchemy.cast(HE.start_date_days / DAYS_PER_YEAR, sqlalchemy.Integer)
        single = (
            self.session.query(year, HE.event_type, count() - count(pair) + count(sqlalchemy.distinct(pair)))
            .filter(clause, HE.storyline_id.is_(None), HE.event_type.notin_(datamodel.ENVOY_EVENT_TYPES))
            .group_by(year, HE.event_type)
        )
        for y, event_type, n in single:
            rows.counts[(max(y, 0) + 2200, _category(event_type))] += n

        # storylines in the order build_items meets them, so both sides of a first contact merge alike
        firsts = (
            self.session.query(HE.storyline_id, sqlalchemy.func.min(HE.start_date_days))
            .filter(clause, HE.storyline_id.isnot(None))
            .group_by(HE.storyline_id)
        )
        storylines = self._all_storylines()
        ordered = [storylines[sid] for sid, _ in sorted(firsts, key=lambda r: (r[1], r[0])) if sid in storylines]
        for sides in event_grouping.storyline_rows(ordered):
            y = year_of(min(s.start_date_days for s in sides))
            category = event_registry.STORYLINE_REGISTRY[str(sides[0].storyline_type)].category
            rows.counts[(y, category)] += 1
            rows.storylines[y].update((s.storyline_id, category) for s in sides)

        # envoy assignments, merged like build_items merges them
        envoys = (
            self.session.query(
                HE.historical_event_id, HE.event_type, HE.country_id, HE.target_country_id, HE.description_id,
                HE.start_date_days, HE.end_date_days, HE.leader_id,
                datamodel.Leader.is_active, datamodel.Leader.last_date,
            )
            .outerjoin(datamodel.Leader, HE.leader_id == datamodel.Leader.leader_id)
            .filter(clause, HE.event_type.in_(datamodel.ENVOY_EVENT_TYPES))
            .order_by(HE.start_date_days.asc(), HE.event_type.asc(), HE.historical_event_id.asc())
        )
        events = [
            _EnvoyEvent(*r[:7], _Leader(r[8], r[9]) if r[7] is not None else None)
            for r in envoys
        ]
        for group in event_grouping.group_envoy_assignments(events):
            first = group.events[0]
            y = year_of(first.start_date_days)
            category = _category(first.event_type)
            rows.counts[(y, category)] += 1
            rows.envoy_events[y].update((e.historical_event_id, category) for e in group.events)
        return rows

    def _all_storylines(self) -> dict:
        if self._storylines is None:
            S = datamodel.Storyline
            query = self.session.query(S.storyline_id, S.storyline_type, S.start_date_days, S.end_date_days,
                                       S.country_id, S.target_country_id)
            self._storylines = {r[0]: _Storyline(*r) for r in query}
        return self._storylines

    def year_counts(self) -> dict:
        """Rows per year in the selected categories."""
        return self.rows().year_counts(self.params.categories)

    def category_counts(self) -> dict:
        counts = collections.Counter()
        for (_, category), n in self.rows().counts.items():
            counts[category] += n
        return counts

    def window(self, year_counts: dict) -> list:
        """The years on this page."""
        p = self.params
        if p.year is not None:
            return [p.year]
        years = sorted(year_counts, reverse=p.newest_first)
        if p.newest_first and p.before is not None:
            years = [y for y in years if y < p.before]
        if not p.newest_first and p.after is not None:
            years = [y for y in years if y > p.after]
        page, total = [], 0
        for y in years:
            page.append(y)
            total += year_counts[y]
            if total >= PAGE_EVENTS:
                break
        return page

    def events(self, years: list) -> list:
        """The events of the rows listed in these years, chronologically; storylines and envoy assignments
        come with all their events, also those of other years."""
        if not years:
            return []
        clause = self._clause(self.params.categories, self.params.tier)
        rows = self.rows()
        queries = [
            self.session.query(HE).filter(
                clause,
                HE.storyline_id.is_(None),
                HE.event_type.notin_(datamodel.ENVOY_EVENT_TYPES),
                _in_years(min(years), max(years)),
            )
        ]
        for column, listed in ((HE.storyline_id, rows.storylines), (HE.historical_event_id, rows.envoy_events)):
            ids = [i for y in years for i, category in listed.get(y, {}).items() if category in self.params.categories]
            for start in range(0, len(ids), _MAX_IDS):
                queries.append(self.session.query(HE).filter(clause, column.in_(ids[start:start + _MAX_IDS])))
        events = [event for query in queries for event in query]
        events.sort(key=lambda e: (e.start_date_days, e.event_type.name, e.historical_event_id))
        return events

    def hidden_counts(self, shown: dict) -> dict:
        """Per year, how many more rows the next detail level would show."""
        level = self.next_level()
        if level is None:
            return {}
        more = self.rows(level).year_counts(self.params.categories)
        return {y: more.get(y, 0) - shown.get(y, 0) for y in more}

    def decades(self, year_counts: dict) -> list:
        """(decade, row count) pairs in display order, for the jump list."""
        counts = collections.Counter()
        for year, n in year_counts.items():
            counts[year // 10 * 10] += n
        return sorted(counts.items(), reverse=self.params.newest_first)

    def row_keys(self, year: int, level: str) -> set:
        """The keys of the rows the given level shows in this year (see row_key), every side's of a first contact."""
        listed = sqlalchemy.and_(HE.storyline_id.is_(None), _in_years(year, year))
        storyline_ids = list(self.rows().storylines.get(year, {}))
        if storyline_ids:
            listed = sqlalchemy.or_(listed, HE.storyline_id.in_(storyline_ids))
        rows = (
            self.session.query(HE.historical_event_id, HE.storyline_id)
            .filter(self._clause(self.params.categories, _LEVEL_TIERS[level]), listed)
        )
        return {row_key(event_id, storyline_id) for event_id, storyline_id in rows}

    def next_level(self) -> Optional[str]:
        return _step_level(self.params.level, 1)

    def previous_level(self) -> Optional[str]:
        return _step_level(self.params.level, -1)


def _in_years(first: int, last: int):
    return sqlalchemy.and_(
        HE.start_date_days >= (_days(first) if first > 2200 else -10 ** 9),
        HE.start_date_days < _days(last + 1),
    )


def _step_level(level: str, step: int) -> Optional[str]:
    keys = [key for key, _, _ in LEVELS]
    i = keys.index(level) + step
    return keys[i] if 0 <= i < len(keys) else None


def row_key(event_id: int, storyline_id: Optional[int]) -> str:
    """Identifies a ledger row across levels: its storyline, or its (first) event."""
    return f"s{storyline_id}" if storyline_id is not None else f"e{event_id}"
