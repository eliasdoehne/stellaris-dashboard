"""Flask route for the Overview: an empire's headline numbers as KPI tiles, each with its change
over the selected period, its rank among the known empires and a sparkline."""
import dataclasses
import json
import logging
import math
from types import SimpleNamespace
from typing import List, Optional, Tuple

import flask
from flask import render_template, request
from sqlalchemy import func, select

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import flask_app, shell, visualization_data
from stellarisdashboard.i18n import N_, gettext as _, ngettext

logger = logging.getLogger(__name__)

CD = datamodel.CountryData

SPARK_POINTS = 60  # samples per sparkline at most, picked in SQL
SPARK_HEIGHT = 32  # viewBox units; the width is 100

# (URL value, years, label); None covers the whole game
SPANS = [
    ("10", 10, N_("10 years")),
    ("50", 50, N_("50 years")),
    ("all", None, N_("Whole game")),
]
DEFAULT_SPAN = "10"


@dataclasses.dataclass(frozen=True)
class Kpi:
    key: str
    label: str
    column: object  # SQL expression over CountryData
    visibility: str  # CountryData.show_*_info method that reveals it for other empires
    plot_id: str  # the graph the tile links to
    style: str = "count"  # count, income or rank
    higher_is_better: Optional[bool] = True


_POPS = (
    select(func.sum(datamodel.PopStatsBySpecies.pop_count))
    .where(datamodel.PopStatsBySpecies.country_data_id == CD.country_data_id)
    .scalar_subquery()
)
_RESEARCH = CD.net_physics_research + CD.net_society_research + CD.net_engineering_research

# (group heading, tiles); four per group keeps the grid even
GROUPS = [
    (
        N_("Standing"),
        [
            Kpi("victory_rank", N_("Victory rank"), CD.victory_rank, "show_geography_info", "victory-rank", "rank", False),
            Kpi("economy", N_("Economic power"), CD.economy_power, "show_geography_info", "victory-economy-score"),
            Kpi("military", N_("Military strength"), CD.military_power, "show_military_info", "military-power"),
            Kpi("techs", N_("Technologies"), CD.tech_count, "show_tech_info", "tech-count"),
        ],
    ),
    (
        N_("Monthly income"),
        [
            Kpi("energy", N_("Energy"), CD.net_energy, "show_economic_info", "net-energy-income", "income"),
            Kpi("minerals", N_("Minerals"), CD.net_minerals, "show_economic_info", "net-mineral-income", "income"),
            Kpi("alloys", N_("Alloys"), CD.net_alloys, "show_economic_info", "net-alloys-income", "income"),
            Kpi("research", N_("Research"), _RESEARCH, "show_tech_info", "empire-research-output-comparison", "income"),
        ],
    ),
    (
        N_("Realm"),
        [
            Kpi("pops", N_("Pops"), _POPS, "show_demographic_info", "empire-species-distribution"),
            Kpi("systems", N_("Systems"), CD.controlled_systems, "show_geography_info", "system-count"),
            Kpi("planets", N_("Planets"), CD.owned_planets, "show_geography_info", "planet-count"),
            Kpi("empire_size", N_("Empire size"), CD.empire_size, "show_economic_info", "empire-size", higher_is_better=None),
        ],
    ),
]
KPIS = [kpi for _group, kpis in GROUPS for kpi in kpis]

_VISIBILITY_COLUMNS = [
    CD.attitude_towards_player,
    CD.has_sensor_link_with_player,
    CD.has_research_agreement_with_player,
    CD.has_migration_treaty_with_player,
    CD.has_defensive_pact_with_player,
    CD.has_federation_with_player,
]


@flask_app.route("/overview")
@flask_app.route("/overview/<game_id>")
def overview_page(game_id=""):
    matches = datamodel.get_known_games(game_id)
    games_dict = datamodel.get_available_games_dict()
    if not matches or matches[0] not in games_dict:
        logger.warning(f"Could not find a game matching {game_id}")
        return render_template("404_page.html", game_not_found=True, missing_game=game_id)
    game_id = matches[0]
    game = games_dict[game_id]
    span = request.args.get("span", DEFAULT_SPAN)
    if span not in {key for key, _years, _label in SPANS}:
        span = DEFAULT_SPAN

    with datamodel.get_db_session(game_id) as session:
        empires = _known_empires(session)
        if not empires:
            return render_template(
                "overview_page.html", game_name=game_id, country=game["country_name"],
                current_date=game["game_date"], empire=None, empires=[], spans=[], groups=[],
            )
        empire = _selected_empire(empires)
        groups, last_date = _build_tiles(session, empire, empires, span, game_id)

    def url_args(**changes):
        return {"game_id": game_id, "span": span, "country": None if empire.is_player else empire.country_id, **changes}

    return render_template(
        "overview_page.html",
        game_name=game_id,
        country=game["country_name"],
        current_date=game["game_date"],
        empire=dict(
            name=empire.rendered_name,
            is_player=bool(empire.is_player),
            ledger_url=flask.url_for("history_page", game_id=game_id, country=empire.country_id),
            last_date=datamodel.days_to_date(last_date) if last_date is not None else None,
        ),
        empires=[
            dict(
                name=c.rendered_name,
                is_player=bool(c.is_player),
                is_active=c is empire,
                url=flask.url_for("overview_page", **url_args(country=None if c.is_player else c.country_id)),
            )
            for c in empires
        ],
        spans=[
            (_(label), key == span, flask.url_for("overview_page", **url_args(span=key)))
            for key, _years, label in SPANS
        ],
        groups=[(_(label), tiles) for (label, _kpis), tiles in zip(GROUPS, groups)],
    )


def _known_empires(session) -> list:
    """The empires shown in the Codex: the player's first, then alphabetical."""
    empires = [
        c
        for c in session.query(datamodel.Country).all()
        if (config.CONFIG.show_all_country_types or c.is_real_country()) and not c.is_hidden_country()
    ]
    empires.sort(key=lambda c: (not c.is_player, c.rendered_name.lower()))
    return empires


def _selected_empire(empires):
    """The empire in the URL, or the player's."""
    country_id = request.args.get("country", type=int)
    return next((c for c in empires if c.country_id == country_id), empires[0])


def _row_columns():
    return [kpi.column.label(kpi.key) for kpi in KPIS] + _VISIBILITY_COLUMNS


def _visible_keys(row, country) -> set:
    """The KPIs of this row the player may see, by the same rules as the graphs."""
    if country.is_player or (config.CONFIG.show_everything and not country.is_hidden_country()):
        return {kpi.key for kpi in KPIS}
    cd = SimpleNamespace(country=country, **{c.key: getattr(row, c.key) for c in _VISIBILITY_COLUMNS})
    if cd.attitude_towards_player is None:
        cd.attitude_towards_player = datamodel.Attitude.unknown
    visible = {name: getattr(CD, name)(cd) for name in {kpi.visibility for kpi in KPIS}}
    return {kpi.key for kpi in KPIS if visible[kpi.visibility]}


def _series(session, country, since: int) -> list:
    """Up to SPARK_POINTS rows of the country's data since the given day, evenly spread."""
    first, last = (
        session.query(func.min(CD.date), func.max(CD.date))
        .filter(CD.country_id == country.country_id, CD.date >= since)
        .one()
    )
    if last is None:
        return []
    bucket = max(1, math.ceil((last - first + 1) / SPARK_POINTS))
    # SQLite takes the other columns of a max() aggregate from the row that has the maximum
    return (
        session.query(func.max(CD.date).label("date"), *_row_columns())
        .filter(CD.country_id == country.country_id, CD.date >= since)
        .group_by((CD.date - first) // bucket)
        .order_by(func.max(CD.date))
        .all()
    )


def _ranks(session, empire, empires, date: int, values: dict) -> dict:
    """Per KPI: (rank of the empire, number of empires whose value is known) on the given date."""
    by_id = {c.country_id: c for c in empires}
    rows = (
        session.query(CD.country_id, *_row_columns())
        .filter(CD.date == date, CD.country_id.in_(list(by_id)))
        .all()
    )
    known = {kpi.key: [] for kpi in KPIS}
    for row in rows:
        visible = _visible_keys(row, by_id[row.country_id])
        for kpi in KPIS:
            value = getattr(row, kpi.key)
            if kpi.key in visible and value is not None:
                known[kpi.key].append(value)
    ranks = {}
    for kpi in KPIS:
        value = values.get(kpi.key)
        if value is None or kpi.style == "rank" or len(known[kpi.key]) < 2:
            continue
        ranks[kpi.key] = (1 + sum(v > value for v in known[kpi.key]), len(known[kpi.key]))
    return ranks


def _build_tiles(session, empire, empires, span, game_id) -> Tuple[list, Optional[int]]:
    years = {key: years for key, years, _label in SPANS}[span]
    latest = session.query(func.max(CD.date)).filter(CD.country_id == empire.country_id).scalar()
    since = latest - 360 * years if years and latest is not None else -(2 ** 62)
    rows = _series(session, empire, since) if latest is not None else []

    points = {kpi.key: [] for kpi in KPIS}
    for row in rows:
        visible = _visible_keys(row, empire)
        for kpi in KPIS:
            value = getattr(row, kpi.key) if kpi.key in visible else None
            points[kpi.key].append((row.date, value))

    # ranks only for values known on the latest date, so they compare like with like
    current = {key: series[-1][1] for key, series in points.items() if series and series[-1][1] is not None}
    ranks = _ranks(session, empire, empires, latest, current) if latest is not None else {}
    graph_tabs = _graph_tab_of_plots()
    groups = []
    for _label, kpis in GROUPS:
        tiles = []
        for kpi in kpis:
            series = points[kpi.key]
            tab = graph_tabs.get(kpi.plot_id)
            tiles.append(
                _tile(kpi, series, ranks.get(kpi.key), shell.section_url("graphs", game_id, tab) if tab else None)
            )
        groups.append(tiles)
    return groups, latest


def _graph_tab_of_plots() -> dict:
    tabs = {}
    for tab, specs in visualization_data.get_plot_specifications_for_tab_layout().items():
        for spec in specs:
            if spec:
                tabs.setdefault(spec.plot_id, tab)
    return tabs


def _last_value(series):
    return next(((d, v) for d, v in reversed(series) if v is not None), None)


def _first_value(series):
    return next(((d, v) for d, v in series if v is not None), None)


def _tile(kpi: Kpi, series, rank, href) -> dict:
    last = _last_value(series)
    first = _first_value(series)
    tile = dict(key=kpi.key, label=_(kpi.label), href=href, value=None, delta=None, rank=None, spark=None, stale=None)
    if last is None:
        return tile
    tile["value"] = format_value(last[1], kpi.style)
    if first is not None and first[0] < last[0]:
        tile["delta"] = format_delta(kpi, first[1], last[1])
        tile["delta"]["since"] = datamodel.days_to_date(first[0])[:4]
    if last[0] < series[-1][0]:
        tile["stale"] = datamodel.days_to_date(last[0])
    if rank:
        # Translators: an empire's place among the empires whose value the player knows, e.g. "#3 of 12"
        tile["rank"] = _("#%(rank)s of %(total)s", rank=rank[0], total=rank[1])
    tile["spark"] = sparkline(series, kpi.style, invert=kpi.style == "rank")
    return tile


def _compact(value: float) -> str:
    v = abs(value)
    if v >= 1e8:
        return f"{v / 1e6:.0f}M"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e5:
        return f"{v / 1e3:.0f}K"
    if v >= 1e4:
        return f"{v / 1e3:.1f}K"
    if v >= 100 or v == int(v):
        return f"{v:,.0f}"
    return f"{v:.1f}"


def format_value(value: float, style: str) -> str:
    """A tile's headline number: compact (12.9K), signed for incomes, #n for ranks."""
    if style == "rank":
        return f"#{int(value)}"
    text = _compact(value)
    if text in {"0", "0.0"}:
        return "0"
    if value < 0:
        return "−" + text
    return ("+" + text) if style == "income" else text


def format_delta(kpi: Kpi, old: float, new: float) -> dict:
    """The change from old to new: text, arrow (up or down) and tone (good, bad or neutral)."""
    if kpi.style == "rank":
        change = old - new  # a smaller rank is better
        # Translators: change of the victory rank in the overview, shown after an up or down arrow
        text = ngettext("%(num)s place", "%(num)s places", int(abs(change)))
    elif kpi.style == "income" or old <= 0:
        change = new - old
        text = _compact(change)
    else:
        change = new - old
        percent = 100 * change / old
        text = f"{abs(percent):.1f}%" if abs(percent) < 10 else f"{abs(percent):,.0f}%"
    if change == 0 or text in {"0", "0.0", "0.0%"}:
        return dict(text="±0", arrow="", tone="neutral")
    up = change > 0
    better = up if kpi.style == "rank" else (up == kpi.higher_is_better if kpi.higher_is_better is not None else None)
    sign = "" if kpi.style == "rank" else ("+" if up else "−")
    return dict(
        text=sign + text,
        arrow="up" if up else "down",
        tone="neutral" if better is None else ("good" if better else "bad"),
    )


def sparkline(series: List[Tuple[int, Optional[float]]], style: str, invert: bool = False) -> Optional[dict]:
    """Geometry for a 100 × SPARK_HEIGHT sparkline; gaps where the value isn't known."""
    known = [(d, v) for d, v in series if v is not None]
    if len(known) < 2:
        return None
    d0, d1 = series[0][0], series[-1][0]
    lo, hi = min(v for _d, v in known), max(v for _d, v in known)
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    pad = 3

    def xy(d, v):
        frac = (v - lo) / (hi - lo)
        return 100 * (d - d0) / (d1 - d0), pad + (frac if invert else 1 - frac) * (SPARK_HEIGHT - 2 * pad)

    path, pen_down, points = [], False, []
    for d, v in series:
        if v is None:
            pen_down = False
            continue
        x, y = xy(d, v)
        path.append(f"{'L' if pen_down else 'M'}{x:.2f} {y:.2f}")
        pen_down = True
        points.append([round(x, 2), round(100 * y / SPARK_HEIGHT, 2), f"{datamodel.days_to_date(d)} · {format_value(v, style)}"])
    zero = xy(d0, 0)[1] if lo < 0 < hi else None
    return dict(
        path=" ".join(path),
        zero=f"{zero:.2f}" if zero is not None else None,
        end=points[-1][:2],
        points=json.dumps(points),
    )
