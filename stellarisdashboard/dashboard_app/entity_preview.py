"""Hover previews of entity links: a compact card with an entity's key facts and latest events.

assets/preview.js fetches the card as an HTML fragment when the pointer rests on a
link to an entity's ledger page (an empire, leader, war, system or planet).
"""
import logging
from typing import Optional

from flask import abort, render_template, url_for

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import flask_app, history_ledger, ledger_timeline
from stellarisdashboard.i18n import N_, gettext as _, pgettext

logger = logging.getLogger(__name__)

# the ledger details shown per kind, by their untranslated label (see history_ledger.EventTemplateDictBuilder)
FACTS = {
    "country": ["Country Type", "Government Type", "Ethics", "Origin", "Ruler"],
    "leader": ["Class", "Subclass", "Species", "Traits", "Hired", "Status"],
    "war": ["Attackers", "Defenders", "Start date", "End date", "Outcome"],
    "system": ["Owner", "Star Class", "Planets", "Resource Deposits"],
    "planet": ["Planet Class", "Districts", "Resource Deposits", "Modifiers"],
}
RECENT_EVENTS = 3
# the level for the latest events, and the one to fall back to if it has none
RECENT_LEVELS = ("notable", "detailed")
SPARK_POINTS = 60

# (CountryData column, label, visibility check); the labels are translated where they are shown
SPARKS = [
    # Translators: label of a small chart in the hover preview of an empire
    ("victory_score", N_("Victory score"), datamodel.CountryData.show_geography_info),
    # Translators: label of a small chart in the hover preview of an empire
    ("military_power", N_("Military power"), datamodel.CountryData.show_military_info),
]


@flask_app.route("/history/<game_id>/preview/<kind>/<int:entity_id>")
def entity_preview(game_id, kind, entity_id):
    if kind not in ledger_timeline.FOCUS_KINDS:
        abort(404)
    matches = datamodel.get_known_games(game_id)
    if not matches:
        abort(404)
    game_id = matches[0]
    model, kind_label = ledger_timeline.FOCUS_KINDS[kind]
    with datamodel.get_db_session(game_id) as session:
        entity = session.get(model, entity_id)
        if entity is None or _is_hidden(entity):
            abort(404)
        builder = history_ledger.EventTemplateDictBuilder(session, game_id, _all_scopes())
        builder._formatted_urls = {}
        details = builder._get_details(entity)
        if kind == "planet" and entity.system is not None:
            details[pgettext("ledger detail", "System")] = builder._get_url_for(entity.system)
            if entity.system.country is not None and not entity.system.country.is_hidden_country():
                details[pgettext("ledger detail", "Owner")] = builder._get_url_for(entity.system.country)
        keys = (["Owner", "System"] if kind == "planet" else []) + FACTS[kind]
        if kind == "war" and entity.outcome == datamodel.WarOutcome.in_progress:
            keys.remove("End date")  # the date it was last seen
        facts = []
        for key in keys:
            label = pgettext("ledger detail", key)
            value = details.get(label)
            if value is not None and str(value).strip() not in ("", "-"):
                facts.append((label, value))
        response = render_template(
            "_entity_preview.html",
            kind=kind,
            kind_label=_(kind_label),
            name=entity.rendered_name,
            is_player=getattr(entity, "is_player", False),
            facts=facts,
            sparks=_sparks(session, entity) if kind == "country" else [],
            exhaustion=_exhaustion(entity) if kind == "war" else None,
            recent=_recent(session, game_id, builder, kind, entity_id),
            links=builder._formatted_urls,
            ledger_url=url_for("history_page", game_id=game_id, **{kind: entity_id}),
        )
    return response, 200, {"Cache-Control": "private, max-age=60"}


def _all_scopes() -> history_ledger.EventFilter:
    event_filter = history_ledger.EventFilter()
    event_filter.scope_threshold = datamodel.HistoricalEventScope.all
    return event_filter


def _is_hidden(entity) -> bool:
    """The search's rules: no previews of hidden empires, their leaders, or wars only they fight."""
    if isinstance(entity, datamodel.Country):
        return entity.is_hidden_country()
    if isinstance(entity, datamodel.Leader):
        return entity.country is not None and entity.country.is_hidden_country()
    if isinstance(entity, datamodel.War):
        sides = [p.country for p in entity.participants if p.country is not None]
        return bool(sides) and all(c.is_hidden_country() for c in sides)
    return False


def _recent(session, game_id, builder, kind, entity_id) -> list:
    """The entity's latest ledger rows, newest first, as (date, item) pairs."""
    for level in RECENT_LEVELS:
        params = ledger_timeline.LedgerParams(game_id=game_id, level=level, focus_kind=kind, focus_id=entity_id)
        query = ledger_timeline.LedgerQuery(session, game_id, params)
        year_counts = query.year_counts()
        years, total = [], 0
        for year in sorted(year_counts, reverse=True):
            years.append(year)
            total += year_counts[year]
            if total >= RECENT_EVENTS:
                break
        items = builder.build_items(query.events(years))
        if items:
            items.sort(key=lambda i: i["start_date_days"], reverse=True)
            return [(i["start_date"], i) for i in items[:RECENT_EVENTS]]
    return []


def _sparks(session, country) -> list:
    """Small charts of an empire's strength over time, as far as the player can see it."""
    rows = (
        session.query(datamodel.CountryData)
        .filter(datamodel.CountryData.country_id == country.country_id)
        .order_by(datamodel.CountryData.date.asc())
        .all()
    )
    # the graphs' rule: show_everything reveals the data of every empire that isn't hidden
    override = config.CONFIG.show_everything and not country.is_hidden_country()
    sparks = []
    for column, label, visible in SPARKS:
        points = [(cd.date, getattr(cd, column)) for cd in rows if override or visible(cd)]
        points = [(d, v) for d, v in points if v is not None]
        if len(points) < 2:
            continue
        sparks.append(dict(
            label=_(label),
            value=_compact(points[-1][1]),
            path=_spark_path(points),
            first_year=ledger_timeline.year_of(points[0][0]),
            last_year=ledger_timeline.year_of(points[-1][0]),
        ))
    return sparks


def _spark_path(points, width=100, height=24) -> str:
    """An SVG polyline of the points, scaled to the box, with at most SPARK_POINTS points."""
    step = max(1, len(points) // SPARK_POINTS)
    sampled = points[::step]
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    d0, d1 = sampled[0][0], sampled[-1][0]
    values = [v for _, v in sampled]
    lo, hi = min(values), max(values)
    x_span = (d1 - d0) or 1
    y_span = (hi - lo) or 1
    return " ".join(
        f"{width * (d - d0) / x_span:.1f},{height - 1 - (height - 2) * (v - lo) / y_span:.1f}"
        for d, v in sampled
    )


def _compact(value: float) -> str:
    for limit, suffix in ((1e6, "M"), (1e3, "k")):
        if abs(value) >= limit:
            return f"{value / limit:.1f}{suffix}"
    return f"{value:.0f}"


def _exhaustion(war: datamodel.War) -> Optional[list]:
    """(label, percent) of both sides' war exhaustion, while it is known."""
    if not (war.attacker_war_exhaustion or war.defender_war_exhaustion):
        return None
    return [
        (pgettext("ledger detail", "Attackers"), _percent(war.attacker_war_exhaustion)),
        (pgettext("ledger detail", "Defenders"), _percent(war.defender_war_exhaustion)),
    ]


def _percent(value) -> int:
    return max(0, min(100, round(100 * (value or 0))))
