"""The event ledger as one filterable timeline: which events to show, paged by year.

Filters come from the URL: a detail level (by event tier), categories, an
optional focus entity (empire, leader, war, system or planet) and a window of
years. Without a focus the galaxy-wide view counts other empires' events one
tier lower than the player's own, so the default view isn't flooded by AI
diplomacy.
"""
import collections
import dataclasses
from typing import Optional

import sqlalchemy
from flask import request, url_for

from stellarisdashboard import datamodel
from stellarisdashboard.dashboard_app import event_registry, utils
from stellarisdashboard.dashboard_app.event_registry import Category, Tier

HE = datamodel.HistoricalEvent

# (key, label, minimum tier)
LEVELS = [
    ("headlines", "Headlines", Tier.HEADLINE),
    ("notable", "Notable", Tier.MAJOR),
    ("detailed", "Detailed", Tier.ROUTINE),
    ("everything", "Everything", Tier.TRIVIA),
]
DEFAULT_LEVEL = "notable"
_LEVEL_TIERS = {key: tier for key, _, tier in LEVELS}

# a page holds whole years until it has at least this many events
PAGE_EVENTS = 120

FOCUS_KINDS = {
    "country": (datamodel.Country, "Empire"),
    "leader": (datamodel.Leader, "Leader"),
    "war": (datamodel.War, "War"),
    "system": (datamodel.System, "System"),
    "planet": (datamodel.Planet, "Planet"),
}

DAYS_PER_YEAR = 360


def _days(year: int) -> int:
    return (year - 2200) * DAYS_PER_YEAR


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
        params.year = _int_arg("year")
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


def _int_arg(name) -> Optional[int]:
    try:
        return int(request.args[name])
    except (KeyError, ValueError):
        return None


def _types(categories, min_tier) -> list:
    return [
        datamodel.HistoricalEventType[name]
        for name, d in event_registry.REGISTRY.items()
        if d.category in categories and d.tier >= min_tier and name in datamodel.HistoricalEventType.__members__
    ]


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

    def _clause(self, categories, min_tier):
        """Which events pass the level and category filters (and the focus)."""
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
            return sqlalchemy.and_(entity, HE.event_type.in_(own))
        others = _types(categories, min(min_tier + 1, Tier.HEADLINE))
        involves_player = sqlalchemy.or_(HE.country_id.in_(self.player_ids), HE.target_country_id.in_(self.player_ids))
        return sqlalchemy.or_(
            sqlalchemy.and_(involves_player, HE.event_type.in_(own)),
            HE.event_type.in_(others),
        )

    def _year_counts(self, clause) -> dict:
        year = sqlalchemy.cast(HE.start_date_days / DAYS_PER_YEAR, sqlalchemy.Integer)
        rows = self.session.query(year, sqlalchemy.func.count()).filter(clause).group_by(year)
        counts = collections.Counter()
        for y, n in rows:
            # events from before the game started (leaders hired earlier) count as its first year
            counts[max(y, 0) + 2200] += n
        return dict(counts)

    def year_counts(self) -> dict:
        return self._year_counts(self._clause(self.params.categories, self.params.tier))

    def category_counts(self) -> dict:
        rows = (
            self.session.query(HE.event_type, sqlalchemy.func.count())
            .filter(self._clause(frozenset(Category), self.params.tier))
            .group_by(HE.event_type)
        )
        counts = collections.Counter()
        for event_type, n in rows:
            descriptor = event_registry.REGISTRY.get(event_type.name)
            if descriptor:
                counts[descriptor.category] += n
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
        if not years:
            return []
        lo, hi = min(years), max(years)
        order = HE.start_date_days.desc() if self.params.newest_first else HE.start_date_days.asc()
        return (
            self.session.query(HE)
            .filter(self._clause(self.params.categories, self.params.tier))
            .filter(HE.start_date_days >= (_days(lo) if lo > 2200 else -10 ** 9), HE.start_date_days < _days(hi + 1))
            .order_by(order, HE.event_type.asc())
            .all()
        )

    def hidden_counts(self, shown: dict) -> dict:
        """Per year, how many more events the next detail level would show."""
        tiers = [tier for _, _, tier in LEVELS]
        i = tiers.index(self.params.tier)
        if i + 1 >= len(tiers):
            return {}
        more = self._year_counts(self._clause(self.params.categories, tiers[i + 1]))
        return {y: more.get(y, 0) - shown.get(y, 0) for y in more}

    def decades(self, year_counts: dict) -> list:
        """(decade, event count) pairs in display order, for the jump list."""
        counts = collections.Counter()
        for year, n in year_counts.items():
            counts[year // 10 * 10] += n
        return sorted(counts.items(), reverse=self.params.newest_first)

    def row_keys(self, years: list, level: str) -> set:
        """The keys of the rows the given level shows in these years (see row_key)."""
        lo, hi = min(years), max(years)
        rows = (
            self.session.query(HE.historical_event_id, HE.storyline_id)
            .filter(self._clause(self.params.categories, _LEVEL_TIERS[level]))
            .filter(HE.start_date_days >= (_days(lo) if lo > 2200 else -10 ** 9), HE.start_date_days < _days(hi + 1))
        )
        return {row_key(event_id, storyline_id) for event_id, storyline_id in rows}

    def next_level(self) -> Optional[str]:
        return _step_level(self.params.level, 1)

    def previous_level(self) -> Optional[str]:
        return _step_level(self.params.level, -1)


def _step_level(level: str, step: int) -> Optional[str]:
    keys = [key for key, _, _ in LEVELS]
    i = keys.index(level) + step
    return keys[i] if 0 <= i < len(keys) else None


def row_key(event_id: int, storyline_id: Optional[int]) -> str:
    """Identifies a ledger row across levels: its storyline, or its (first) event."""
    return f"s{storyline_id}" if storyline_id is not None else f"e{event_id}"
