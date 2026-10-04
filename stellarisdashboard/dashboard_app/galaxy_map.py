"""Flask routes for the standalone galaxy map page.

The galaxy map used to be a tab inside the Dash timeline app, rendered with Plotly.
It now lives on its own page at ``/galaxy/<game_id>`` and is drawn client-side with
deck.gl (see ``templates/galaxy_map.html`` + ``assets/galaxy_map.js``). This module
only serves the page shell, the map data as JSON, and the timelapse export trigger;
all ownership/date logic still lives in ``visualization_data.GalaxyMapData``.
"""
import logging

from flask import render_template, request, jsonify, redirect, url_for
from PIL import features

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import (
    flask_app,
    utils,
    visualization_data,
    timelapse_exporter,
)

logger = logging.getLogger(__name__)

TIMELAPSE_DEFAULT_START = "2200.01.01"
TIMELAPSE_DEFAULT_STEP = 120
TIMELAPSE_DEFAULT_FRAME_TIME = 100
TIMELAPSE_DEFAULT_DPI = 100


def _rgb(game_id: str, country_name: str):
    """Empire color as an [r, g, b] int triple for deck.gl layers."""
    r, g, b = visualization_data.get_color_vals(game_id, country_name)
    return [int(r), int(g), int(b)]


@flask_app.route("/galaxy")
@flask_app.route("/galaxy/<game_id>")
def galaxy_page(game_id=""):
    """The galaxy map page shell. The actual map is fetched as JSON and rendered
    client-side by assets/galaxy_map.js."""
    matches = datamodel.get_known_games(game_id)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        return render_template("404_page.html", game_not_found=True, game_name=game_id)
    game_id = matches[0]
    games_dict = datamodel.get_available_games_dict()
    country = games_dict[game_id]["country_name"]

    with datamodel.get_db_session(game_id) as session:
        max_date = utils.get_most_recent_date(session)

    return render_template(
        "galaxy_map.html",
        game_name=game_id,
        country=country,
        current_date=datamodel.days_to_date(max_date),
        max_date=max_date,
        webp_supported=features.check("webp_anim"),
        timelapse_defaults={
            "start": TIMELAPSE_DEFAULT_START,
            "end": datamodel.days_to_date(max_date),
            "step": TIMELAPSE_DEFAULT_STEP,
            "frame_time": TIMELAPSE_DEFAULT_FRAME_TIME,
            "dpi": TIMELAPSE_DEFAULT_DPI,
        },
    )


@flask_app.route("/galaxy/<game_id>/geometry")
def galaxy_geometry(game_id):
    """Static, date-independent geometry: system positions/names, hyperlane
    segments, and territory (Voronoi) polygons. Fetched once per game; the
    per-date endpoint only sends ownership colors and borders on top of this."""
    matches = datamodel.get_known_games(game_id)
    if not matches:
        return jsonify({"error": "unknown game"}), 404
    game_id = matches[0]

    galaxy = visualization_data.get_galaxy_data(game_id)
    graph = galaxy.galaxy_graph

    systems = []
    territory = []
    for node in graph.nodes:
        ndata = graph.nodes[node]
        x, y = ndata["pos"]
        systems.append(
            {
                "id": node,
                "system_id": ndata["system_id"],
                "name": ndata["name"],
                "x": x,
                "y": y,
            }
        )
        shape_x, shape_y = ndata.get("shape", ([], []))
        if len(shape_x):
            territory.append(
                {"id": node, "polygon": [[sx, sy] for sx, sy in zip(shape_x, shape_y)]}
            )

    hyperlanes = []
    for a, b in graph.edges:
        x0, y0 = graph.nodes[a]["pos"]
        x1, y1 = graph.nodes[b]["pos"]
        hyperlanes.append({"a": a, "b": b, "source": [x0, y0], "target": [x1, y1]})

    return jsonify(
        {"systems": systems, "territory": territory, "hyperlanes": hyperlanes}
    )


@flask_app.route("/galaxy/<game_id>/data")
def galaxy_data(game_id):
    """Per-date projection: which country owns each system (with its color) and
    the white border ridges for that date. Keyed by in-game system id so the
    client can recolor the static geometry without refetching it."""
    matches = datamodel.get_known_games(game_id)
    if not matches:
        return jsonify({"error": "unknown game"}), 404
    game_id = matches[0]

    try:
        days = int(float(request.args.get("days", 0)))
    except (TypeError, ValueError):
        days = 0

    galaxy = visualization_data.get_galaxy_data(game_id)
    galaxy.update_graph_for_date(days)
    graph = galaxy.galaxy_graph
    system_borders = graph.graph["system_borders"]
    UNCLAIMED = visualization_data.GalaxyMapData.UNCLAIMED

    color_cache = {}

    def color_for(name):
        if name not in color_cache:
            color_cache[name] = _rgb(game_id, name)
        return color_cache[name]

    owners = {}
    country_ridges = {UNCLAIMED: set(system_borders.get(galaxy.ARTIFICIAL_NODE, set()))}
    for node in graph.nodes:
        ndata = graph.nodes[node]
        country = ndata["country"]
        if country != UNCLAIMED:
            owners[node] = {
                "country_id": ndata["country_id"],
                "country_name": country,
                "color": color_for(country),
            }
        country_ridges.setdefault(country, set()).update(
            system_borders.get(node, set())
        )

    borders = [
        [[xs[0], ys[0]], [xs[1], ys[1]]]
        for xs, ys in galaxy.get_country_border_ridges(country_ridges)
    ]

    return jsonify(
        {
            "days": days,
            "date": datamodel.days_to_date(days),
            "owners": owners,
            "borders": borders,
        }
    )


def _timelapse_toast(message, game_id, kind):
    """Reply to a timelapse trigger: an out-of-band toast for htmx requests,
    a redirect back to the galaxy page otherwise."""
    if request.headers.get("HX-Request"):
        return (
            '<div id="toast-slot" hx-swap-oob="innerHTML">'
            f'<div class="toast toast--{kind}" role="status">{message}</div>'
            "</div>"
        )
    return redirect(url_for("galaxy_page", game_id=game_id))


class _TimelapseFormError(Exception):
    """A user-facing validation error in the timelapse export form."""


def _positive_int(raw, default, field_label):
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise _TimelapseFormError(f"{field_label} must be a whole number.")
    if value < 1:
        raise _TimelapseFormError(f"{field_label} must be at least 1.")
    return value


@flask_app.route("/galaxy/<game_id>/timelapse", methods=["POST"])
def galaxy_timelapse(game_id):
    """Validate the timelapse form and hand the export to the background queue.

    The export itself is long-running; it runs on a single worker thread (see
    ``timelapse_exporter.export_queue``) so the request returns immediately and
    only one export runs at a time."""
    matches = datamodel.get_known_games(game_id)
    if not matches:
        return jsonify({"error": "unknown game"}), 404
    game_id = matches[0]

    form = request.form
    start_date = form.get("start") or TIMELAPSE_DEFAULT_START
    end_date = form.get("end")
    if not end_date:
        with datamodel.get_db_session(game_id) as session:
            end_date = datamodel.days_to_date(utils.get_most_recent_date(session))

    export_gif = "export_gif" in form
    export_webp = "export_webp" in form
    export_frames = "export_frames" in form
    square_aspect_ratio = "square_aspect_ratio" in form

    try:
        step_days = _positive_int(form.get("step"), TIMELAPSE_DEFAULT_STEP, "Step size")
        frame_time_ms = _positive_int(
            form.get("frame_time"), TIMELAPSE_DEFAULT_FRAME_TIME, "Frame time"
        )
        dpi = _positive_int(form.get("dpi"), TIMELAPSE_DEFAULT_DPI, "DPI")
        try:
            tl_start_days = datamodel.date_to_days(start_date)
            tl_end_days = datamodel.date_to_days(end_date)
        except ValueError:
            raise _TimelapseFormError("Invalid date(s). Use YYYY.MM.DD format.")
        if tl_start_days >= tl_end_days:
            raise _TimelapseFormError("Start date must be before end date.")
    except _TimelapseFormError as e:
        logger.error(f"Rejected timelapse export for {game_id}: {e}")
        return _timelapse_toast(str(e), game_id, "error")

    if not (export_gif or export_webp or export_frames):
        return _timelapse_toast(
            "Select at least one export format (gif, webp, or frames).",
            game_id,
            "error",
        )

    width, height = (16, 16) if square_aspect_ratio else (16, 9)
    ahead = timelapse_exporter.export_queue.submit(
        timelapse_exporter.TimelapseRequest(
            game_id=game_id,
            width=width,
            height=height,
            dpi=dpi,
            start_date=tl_start_days,
            end_date=tl_end_days,
            step_days=step_days,
            tl_duration=frame_time_ms,
            export_gif=export_gif,
            export_webp=export_webp,
            export_frames=export_frames,
        )
    )
    logger.info(f"Queued timelapse export for {game_id} ({ahead} ahead in queue)")

    if ahead:
        message = (
            f"Timelapse export queued ({ahead} already in progress). "
            "It will run in the background; check your output folder when done."
        )
    else:
        message = (
            "Timelapse export started in the background. "
            "Check your output folder when it finishes."
        )
    return _timelapse_toast(message, game_id, "success")
