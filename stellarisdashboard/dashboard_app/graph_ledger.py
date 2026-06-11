import json
import logging
import time
from collections import defaultdict
from typing import Dict, Any, List
from urllib import parse

import dash.exceptions
import plotly.graph_objs as go
from dash import Dash, callback_context, dcc, html, Input, Output, State, ALL
from flask import render_template
from PIL import features

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import (
    utils,
    flask_app,
    visualization_data,
    timelapse_exporter,
)

logger = logging.getLogger(__name__)

# Modernized sci-fi palette — kept in sync with assets/timeline.css.
# Plot backgrounds are transparent so the card surface shows through.
PLOT_PAPER = "rgba(0,0,0,0)"
PLOT_BG = "rgba(0,0,0,0)"
GALAXY_PLOT_BG = "rgba(6,9,14,1)"  # near-black "space"
GALAXY_PAPER = "rgba(0,0,0,0)"
GRID_COLOR = "rgba(255,255,255,0.06)"
TEXT_COLOR = "rgba(220,225,232,1)"
TEXT_DIM = "rgba(150,160,170,1)"
ACCENT_COLOR = "rgba(232,168,72,1)"
FONT_FAMILY = "system-ui, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

DEFAULT_PLOT_LAYOUT = dict(
    xaxis=dict(showgrid=False, zeroline=False, linecolor=GRID_COLOR),
    yaxis=dict(showgrid=True, gridcolor=GRID_COLOR, zeroline=False, type="linear"),
    plot_bgcolor=PLOT_BG,
    paper_bgcolor=PLOT_PAPER,
    font={"color": TEXT_COLOR, "family": FONT_FAMILY, "size": 13},
    showlegend=True,
    legend=dict(bgcolor="rgba(0,0,0,0)", font={"color": TEXT_DIM, "size": 11}),
    margin=dict(t=56, b=48, l=56, r=24),
    autosize=True,
)

# Cleaner plotly modebar (no logo, fewer buttons).
GRAPH_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["select2d", "lasso2d"],
    "responsive": True,
}
# Compact thumbnails in the grid: no modebar, static, minimal chrome.
GRAPH_CONFIG_COMPACT = {
    "displayModeBar": False,
    "staticPlot": False,
    "responsive": True,
}
COMPACT_PLOT_HEIGHT = 240

SELECT_SYSTEM_DEFAULT = html.P(
    children=["Click the map to select a system"],
    id="click-data",
    className="tl-hint",
)

TIMELAPSE_DEFAULT_START = "2200.01.01"
TIMELAPSE_DEFAULT_STEP = 120
TIMELAPSE_DEFAULT_FRAME_TIME = 100
TIMELAPSE_DEFAULT_DPI = 100

timeline_app = Dash(
    __name__,
    title="Stellaris Dashboard",
    server=flask_app,
    compress=False,
    url_base_pathname="/timeline/",
)


def get_figure_layout(plot_spec: visualization_data.PlotSpecification):
    layout = dict(DEFAULT_PLOT_LAYOUT)
    # Fresh axis dicts so we don't mutate the shared DEFAULT_PLOT_LAYOUT.
    layout["xaxis"] = dict(layout["xaxis"], title=plot_spec.x_axis_label)
    layout["yaxis"] = dict(layout["yaxis"], title=plot_spec.y_axis_label)
    # Width is fluid (autosize); the card/container controls it. Height stays configurable.
    layout["height"] = config.CONFIG.plot_height
    if plot_spec.style == visualization_data.PlotStyle.line:
        layout["hovermode"] = "closest"
    else:
        layout["hovermode"] = "x"
    return go.Layout(**layout)


def get_compact_figure_layout(plot_spec: visualization_data.PlotSpecification):
    """Minimal layout for the grid thumbnails: no title/legend/axis labels,
    tight margins. The full detail is shown in the modal instead."""
    layout = dict(DEFAULT_PLOT_LAYOUT)
    layout["xaxis"] = dict(layout["xaxis"], title=None)
    layout["yaxis"] = dict(layout["yaxis"], title=None)
    layout["height"] = COMPACT_PLOT_HEIGHT
    layout["showlegend"] = False
    layout["margin"] = dict(t=10, b=28, l=44, r=14)
    if plot_spec.style == visualization_data.PlotStyle.line:
        layout["hovermode"] = "closest"
    else:
        layout["hovermode"] = "x"
    return go.Layout(**layout)


def _plot_grid_card(plot_spec, compact_figure):
    """A compact plot thumbnail. Its header is clickable and opens the modal
    (the pattern-matching id is keyed on the plot id)."""
    return html.Div(
        className="tl-card tl-plot-card",
        children=[
            html.Div(
                className="tl-plot-card__header",
                id={"type": "expand-plot", "index": plot_spec.plot_id},
                n_clicks=0,
                title="Click to enlarge",
                children=[
                    html.Span(plot_spec.title, className="tl-plot-card__title"),
                    html.Span("⤢", className="tl-expand-icon"),
                ],
            ),
            dcc.Graph(
                id=plot_spec.plot_id,
                figure=compact_figure,
                className="tl-graph tl-graph--compact",
                responsive=True,
                config=GRAPH_CONFIG_COMPACT,
                style={"width": "100%"},
            ),
        ],
    )


@timeline_app.callback(Output("game-name-header", "children"), [Input("url", "search")])
def update_game_header(search):
    game_id, matches = _get_game_ids_matching_url(search)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        return "Unknown Game"
    game_id = matches[0]
    games_dict = datamodel.get_available_games_dict()
    country = games_dict[game_id]["country_name"]
    return f"{country} ({game_id})"


@timeline_app.callback(Output("ledger-link", "href"), [Input("url", "search")])
def update_ledger_link(search):
    game_id, _ = _get_game_ids_matching_url(search)
    if game_id:
        return f"/history/{game_id}"
    return "/history"


@timeline_app.callback(
    Output("country-perspective-dropdown", "options"), [Input("url", "search")]
)
def update_country_select_options(search):
    game_id, _ = _get_game_ids_matching_url(search)
    games_dict = datamodel.get_available_games_dict()

    if game_id not in games_dict:
        logger.warning(f"Game ID {game_id} does not match any known game!")
        return []

    options = [{"label": "None", "value": None}]
    with datamodel.get_db_session(game_id) as session:
        for c in session.query(datamodel.Country):
            if (
                c.is_real_country()
                and not c.is_hidden_country()
            ):
                options.append(
                    {"label": c.rendered_name, "value": c.country_id_in_game}
                )
    return options


@timeline_app.callback(
    Output("click-data", "children"), [Input("galaxy-map", "clickData")]
)
def galaxy_map_system_info(clickData):
    if not clickData:
        return SELECT_SYSTEM_DEFAULT
    points = clickData.get("points")
    if not points:
        return SELECT_SYSTEM_DEFAULT
    p = points[0]
    custom_data = p.get("customdata", {})
    system_id = custom_data.get("system_id")
    system_name = custom_data.get("system_name")
    country_id = custom_data.get("country_id")
    country_name = custom_data.get("country_name")
    game_id = custom_data.get("game_id")
    text = p.get("text")
    if not system_id or not game_id or not text:
        return SELECT_SYSTEM_DEFAULT
    return html.P(
        children=[
            f"Selected system: ",
            html.A(
                children=system_name,
                href=utils.flask.url_for(
                    "history_page", game_id=game_id, system=system_id
                ),
                className="textlink",
            ),
            " (",
            html.A(
                children=country_name,
                href=utils.flask.url_for(
                    "history_page", game_id=game_id, country=country_id
                ),
                className="textlink",
            ) if country_id is not None else country_name,
            ")"
        ]
    )


@timeline_app.callback(
    Output(component_id="galaxy-tab-ui", component_property="style"),
    [Input("tabs-container", "value")],
)
def show_hide_galaxy_tab_ui(tab_value):
    # The galaxy controls live in the sidebar and are only relevant on the map tab.
    if tab_value == config.GALAXY_MAP_TAB:
        return {"display": "flex"}
    return {"display": "none"}


@timeline_app.callback(
    Output(component_id="dateslider", component_property="marks"),
    [Input("tabs-container", "value"), Input("url", "search")],
)
def adjust_slider_values(tab_value, search):
    if tab_value == config.GALAXY_MAP_TAB:
        _, matches = _get_game_ids_matching_url(search)
        with datamodel.get_db_session(matches[0]) as session:
            max_date = utils.get_most_recent_date(session)
        marks = {0: "2200.01.01", 100: datamodel.days_to_date(max_date)}
        for x in range(20, 100, 20):
            marks[x] = datamodel.days_to_date(x / 100 * max_date)
        logger.info(f"Setting slider marks to {marks}")
        return marks
    else:
        raise dash.exceptions.PreventUpdate()


@timeline_app.callback(
    Output(component_id="timelapse-end-input", component_property="placeholder"),
    [Input("tabs-container", "value"), Input("url", "search")],
)
def adjust_end_date_field_value(tab_value, search):
    if tab_value == config.GALAXY_MAP_TAB:
        _, matches = _get_game_ids_matching_url(search)
        with datamodel.get_db_session(matches[0]) as session:
            max_date = utils.get_most_recent_date(session)

        return datamodel.days_to_date(max_date)
    else:
        raise dash.exceptions.PreventUpdate()


@timeline_app.callback(
    Output("hidden-div", "children"),
    [
        Input("url", "search"),
        Input("timelapse-start-input", "value"),
        Input("timelapse-end-input", "value"),
        Input("timelapse-step-input", "value"),
        Input("timelapse-duration-input", "value"),
        Input("timelapse-dpi-input", "value"),
        Input("galaxy-export-button", "n_clicks"),
        Input("timelapse-export-mode", "value"),
    ],
)
def trigger_timeline_export(
    search,
    start_date,
    end_date,
    step_days,
    frame_time_ms,
    dpi,
    clicks,
    export_mode,
):
    _, matches = _get_game_ids_matching_url(search)
    if not matches:
        logger.warning(f"Could not find a game from URL {search}")
        return "Unknown Game"
    game_id = matches[0]

    start_date = start_date or TIMELAPSE_DEFAULT_START
    if not end_date:
        with datamodel.get_db_session(game_id) as session:
            end_date = datamodel.days_to_date(utils.get_most_recent_date(session))
    step_days = step_days or TIMELAPSE_DEFAULT_STEP
    frame_time_ms = frame_time_ms or TIMELAPSE_DEFAULT_FRAME_TIME
    dpi = dpi or TIMELAPSE_DEFAULT_DPI

    export_gif = "export_gif" in export_mode
    export_webp = "export_webp" in export_mode
    export_frames = "export_frames" in export_mode
    square_aspect_ratio = "square_aspect_ratio" in export_mode

    changed_ids = [p["prop_id"].split(".")[0] for p in callback_context.triggered]
    button_pressed = "galaxy-export-button" in changed_ids
    if button_pressed:
        logger.info(f"Triggering timelapse export for {game_id}")
        logger.info(
            f"{start_date=}, {end_date=}, {step_days=}, {frame_time_ms=}, "
            f"{export_gif=}, {export_webp=}, {export_frames=}, "
            f"{square_aspect_ratio=}, {dpi=}"
        )
        try:
            tl_start_days = datamodel.date_to_days(start_date)
            tl_end_days = datamodel.date_to_days(end_date)
        except ValueError:
            logger.error(
                f"Received invalid date(s) {start_date}, {end_date} for timelapse export. "
                f"Dates must be given in YYYY.MM.DD format."
            )
            return
        if tl_start_days >= tl_end_days:
            logger.error(
                f"Start date must be before end date for timelapse export, received {start_date}, {end_date}"
            )
            return

        width, height = (16, 16) if square_aspect_ratio else (16, 9)

        te = timelapse_exporter.TimelapseExporter(game_id, width, height, dpi)
        te.create_timelapse(
            start_date=tl_start_days,
            end_date=tl_end_days,
            step_days=step_days,
            tl_duration=frame_time_ms,
            export_gif=export_gif,
            export_webp=export_webp,
            export_frames=export_frames,
        )


@timeline_app.callback(
    Output("tab-content", "children"),
    [
        Input("tabs-container", "value"),
        Input("url", "search"),
        Input("dateslider", "value"),
        Input("dash-plot-checklist", "value"),
        Input("country-perspective-dropdown", "value"),
    ],
)
def update_content(
    tab_value, search, date_fraction, dash_plot_checklist, country_perspective
):
    config.CONFIG.normalize_stacked_plots = (
        "normalize_stacked_plots" in dash_plot_checklist
    )
    game_id, matches = _get_game_ids_matching_url(search)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        return render_template("404_page.html", game_not_found=True, game_name=game_id)
    game_id = matches[0]
    games_dict = datamodel.get_available_games_dict()
    if game_id not in games_dict:
        logger.warning(
            f"Game ID {game_id} does not match any known game! (URL parameter {search})"
        )
        return []

    logger.info(f"dash_server.update_content: Tab is {tab_value}, Game is {game_id}")
    with datamodel.get_db_session(game_id) as session:
        current_date = utils.get_most_recent_date(session)

    children = []
    if tab_value in config.CONFIG.tab_layout:
        if tab_value == config.MARKET_TAB:
            plots = visualization_data.get_market_graphs(config.CONFIG.market_resources)
        else:
            plots = visualization_data.get_plot_specifications_for_tab_layout().get(
                tab_value
            )
        plot_data = visualization_data.get_current_execution_plot_data(
            game_id, country_perspective
        )
        # Each plot becomes a compact card in a responsive grid. The full-detail
        # figure is stashed in a Store and rendered in the modal on demand.
        grid_children = []
        full_figures = {}
        for plot_spec in plots:
            if not plot_spec:
                continue  # just in case it's possible to sneak in an invalid ID
            start = time.time()
            figure_data = get_raw_plot_data_dicts(game_id, plot_data, plot_spec)
            end = time.time()
            logger.debug(
                f"Prepared figure {plot_spec.title} in {end - start:5.3f} seconds."
            )
            if not figure_data:
                continue

            full_layout = get_figure_layout(plot_spec)
            full_layout["title"] = dict(
                text=plot_spec.title,
                font=dict(size=18, color=TEXT_COLOR, family=FONT_FAMILY),
                x=0.5,
                xanchor="center",
            )
            full_figures[plot_spec.plot_id] = go.Figure(
                data=figure_data, layout=full_layout
            ).to_plotly_json()

            compact_figure = go.Figure(
                data=figure_data, layout=get_compact_figure_layout(plot_spec)
            )
            grid_children.append(_plot_grid_card(plot_spec, compact_figure))

        children.append(html.Div(grid_children, className="tl-grid"))
        children.append(dcc.Store(id="full-figures-store", data=full_figures))
    else:
        slider_date = 0.01 * date_fraction * current_date

        children.append(
            html.Div(
                [get_galaxy(game_id, slider_date)],
                className="tl-card tl-card--galaxy",
            )
        )
    return children


@timeline_app.callback(
    [
        Output("plot-modal", "className"),
        Output("modal-graph", "figure"),
        Output("modal-title", "children"),
    ],
    [
        Input({"type": "expand-plot", "index": ALL}, "n_clicks"),
        Input("modal-close", "n_clicks"),
        Input("modal-backdrop", "n_clicks"),
    ],
    [State("full-figures-store", "data")],
    prevent_initial_call=True,
)
def toggle_plot_modal(expand_clicks, close_clicks, backdrop_clicks, store):
    """Open the modal with a plot's full-detail figure, or close it."""
    triggered = callback_context.triggered
    if not triggered:
        raise dash.exceptions.PreventUpdate
    prop_id = triggered[0]["prop_id"]

    # Close button / backdrop click -> hide.
    if prop_id.startswith("modal-close") or prop_id.startswith("modal-backdrop"):
        return "tl-modal tl-modal--hidden", dash.no_update, dash.no_update

    # Ignore the spurious fire when the grid (and its buttons) is first created.
    if not any(expand_clicks or []):
        raise dash.exceptions.PreventUpdate

    try:
        plot_id = json.loads(prop_id.rsplit(".", 1)[0])["index"]
    except (ValueError, KeyError):
        raise dash.exceptions.PreventUpdate

    figure = (store or {}).get(plot_id)
    if figure is None:
        raise dash.exceptions.PreventUpdate

    title = (
        figure.get("layout", {}).get("title", {}).get("text", "")
        if isinstance(figure, dict)
        else ""
    )
    return "tl-modal", figure, title


def _get_url_params(url: str) -> Dict[str, List[str]]:
    return parse.parse_qs(parse.urlparse(url).query)


def _get_game_ids_matching_url(url):
    url_params = _get_url_params(url)
    game_id = url_params.get("game_name", [None])[0]
    if game_id is None:
        game_id = ""
    matches = datamodel.get_known_games(game_id)
    return game_id, matches


def get_raw_plot_data_dicts(
    game_id: str,
    plot_data: visualization_data.PlotDataManager,
    plot_spec: visualization_data.PlotSpecification,
) -> List[Dict[str, Any]]:
    """
    Depending on the plot_spec.style attribute, retrieve the data to be plotted
    in a format that can directly be passed into a Dash Figure object.

    :param plot_data:
    :param plot_spec:
    :return:
    """
    if plot_spec.style == visualization_data.PlotStyle.line:
        return _get_raw_data_for_line_plot(game_id, plot_data, plot_spec)
    elif plot_spec.style in [
        visualization_data.PlotStyle.stacked,
        visualization_data.PlotStyle.budget,
    ]:
        return _get_raw_data_for_stacked_and_budget_plots(game_id, plot_data, plot_spec)
    else:
        logger.warning(f"Unknown Plot type {plot_spec}")
        return []


def _get_raw_data_for_line_plot(
    game_id: str,
    plot_data: visualization_data.PlotDataManager,
    plot_spec: visualization_data.PlotSpecification,
) -> List[Dict[str, Any]]:
    plot_list = []
    for key, x_values, y_values in plot_data.get_data_for_plot(plot_spec):
        if all(y != y for y in y_values):
            continue
        if not x_values:
            continue
        line = dict(
            x=x_values,
            y=y_values,
            name=dict_key_to_legend_label(key),
            line={"color": get_country_color(game_id, key, 1.0)},
            text=get_plot_value_labels(x_values, y_values, key),
            hoverinfo="text",
        )
        plot_list.append(line)
    return plot_list


def _get_raw_data_for_stacked_and_budget_plots(
    game_id: str,
    plot_data: visualization_data.PlotDataManager,
    plot_spec: visualization_data.PlotSpecification,
) -> List[Dict[str, Any]]:
    net_gain = None
    lines = []
    normalized = (
        config.CONFIG.normalize_stacked_plots
        and plot_spec.style == visualization_data.PlotStyle.stacked
    )
    for key, x_values, y_values in plot_data.get_data_for_plot(plot_spec):
        if not any(y_values):
            continue
        if plot_spec.style == visualization_data.PlotStyle.budget:
            if net_gain is None:
                net_gain = [0.0 for _ in x_values]
            net_gain = [net + y for (net, y) in zip(net_gain, y_values)]

        # Usually, each budget item contributes only positively or only negatively to the budget. To make this clear,
        # we separate them to separate stack groups that are drawn on the plot independently.
        # We must handle the edge case where a budget item has both positive and negative contributions at different times:
        if min(y_values) < 0 < max(y_values):
            # split negative and positive values, add them to separate groups and only show one legend entry
            neg, pos = [], []
            for y in y_values:
                if y < 0:
                    neg.append(y)
                    pos.append(0)
                else:
                    neg.append(0)
                    pos.append(y)
            series = [(pos, "pos"), (neg, "neg")]
        else:
            if min(y_values) < 0:
                stackgroup = "neg"
            else:
                stackgroup = "pos"
            series = [(y_values, stackgroup)]
        for i, (yv, stackgroup) in enumerate(series):
            lines.append(
                dict(
                    x=x_values,
                    y=yv,
                    name=dict_key_to_legend_label(key),
                    legendgroup=key,  # ensure that budget contributions with mixed signs still behave as a single entry
                    hoverinfo="text",
                    mode="lines",
                    line=dict(width=0.5, color=get_country_color(game_id, key, 1.0)),
                    stackgroup=stackgroup,
                    groupnorm="percent" if normalized else "",
                    fillcolor=get_country_color(game_id, key, 0.5),
                    text=get_plot_value_labels(x_values, yv, key),
                    showlegend=i == 0,  # only show one legend entry
                )
            )

    if lines and plot_spec.style == visualization_data.PlotStyle.budget:
        # Add net value over time
        name = "Net result"
        line = dict(
            x=lines[0]["x"],
            y=net_gain,
            name=name,
            line=dict(color="rgba(255,255,255,1)"),
            text=get_plot_value_labels(lines[0]["x"], net_gain, "Net result"),
            hoverinfo="text",
        )
        lines.append(line)
    return lines


def dict_key_to_legend_label(key: str):
    words = key.split("_")
    if len(words[0]) > 0 and words[0][0].islower():
        words[0] = words[0].capitalize()
    return " ".join(words)


def get_plot_value_labels(x_values, y_values, key):
    return [
        f"{datamodel.days_to_date(360 * x)}: {y:.2f} - {dict_key_to_legend_label(key)}"
        if (y and y == y)
        else ""
        for (x, y) in zip(x_values, y_values)
    ]


def get_galaxy(game_id: str, slider_date: float) -> dcc.Graph:
    """Generate the galaxy map, ready to be used in the Dash layout.

    :param game_id:
    :param slider_date:
    :return:
    """
    # adapted from https://plot.ly/python/network-graphs/
    galaxy_map_data = visualization_data.get_galaxy_data(game_id)
    galaxy_map_data.update_graph_for_date(int(slider_date))
    nx_graph = galaxy_map_data.galaxy_graph

    edge_traces_data = {}
    for edge in nx_graph.edges:
        country = nx_graph.edges[edge]["country"]
        if country not in edge_traces_data:
            edge_traces_data[country] = dict(
                x=[],
                y=[],
                text=[],
                line=go.scatter.Line(width=0.5, color=get_country_color(game_id, country)),
                hoverinfo="text",
                mode="lines",
                showlegend=False,
            )
        x0, y0 = nx_graph.nodes[edge[0]]["pos"]
        x1, y1 = nx_graph.nodes[edge[1]]["pos"]
        # insert None to prevent dash from joining the lines
        edge_traces_data[country]["x"] += [x0, x1, None]
        edge_traces_data[country]["y"] += [y0, y1, None]
        edge_traces_data[country]["text"] += [country]
    edge_traces = [
        go.Scatter(**edge_traces_data[country]) for country in edge_traces_data
    ]

    system_shapes = []
    country_system_markers = {}
    country_border_ridges = defaultdict(set)

    country_border_ridges[
        galaxy_map_data.UNCLAIMED
    ] |= galaxy_map_data.galaxy_graph.graph["system_borders"].get(
        galaxy_map_data.ARTIFICIAL_NODE, set()
    )
    for i, node in enumerate(nx_graph.nodes):
        country = nx_graph.nodes[node]["country"]
        if country not in country_system_markers:
            country_system_markers[country] = dict(
                x=[],
                y=[],
                text=[],
                customdata=[],
                mode="markers",
                hoverinfo="text",
                marker=dict(color=[], size=4),
                name=country,
            )
        color = get_country_color(game_id, country)
        country_system_markers[country]["marker"]["color"].append(color)
        x, y = nx_graph.nodes[node]["pos"]
        country_system_markers[country]["x"].append(x)
        country_system_markers[country]["y"].append(y)
        text = f'{nx_graph.nodes[node]["name"]} ({country})'
        country_system_markers[country]["text"].append(text)
        customdata = {
            "system_id": nx_graph.nodes[node]["system_id"],
            "game_id": game_id,
            "system_name": nx_graph.nodes[node]["name"],
            "country_name": country,
            "country_id": nx_graph.nodes[node]["country_id"],
        }
        country_system_markers[country]["customdata"].append(customdata)
        if country != visualization_data.GalaxyMapData.UNCLAIMED:
            shape_x, shape_y = nx_graph.nodes[node].get("shape", ([], []))
            system_shapes.append(
                go.Scatter(
                    x=shape_x,
                    y=shape_y,
                    text=[text],
                    customdata=[customdata],
                    fill="toself",
                    fillcolor=color,
                    hoverinfo="none",
                    line=dict(width=0),
                    mode="none",
                    opacity=0.2,
                    showlegend=False,
                )
            )
        country_border_ridges[country] |= galaxy_map_data.galaxy_graph.graph[
            "system_borders"
        ].get(node, set())

    country_borders = []
    for x_values, y_values in galaxy_map_data.get_country_border_ridges(
        country_border_ridges
    ):
        country_borders.append(
            go.Scatter(
                dict(
                    x=x_values,
                    y=y_values,
                    text=[],
                    line=go.scatter.Line(width=0.75, color="rgba(255,255,255,1)"),
                    hoverinfo="text",
                    mode="lines",
                    showlegend=False,
                )
            )
        )

    for country in country_system_markers:
        country_system_markers[country]["marker"] = go.scatter.Marker(
            **country_system_markers[country]["marker"]
        )
    system_markers = [
        go.Scatter(**scatter_data)
        for country, scatter_data in country_system_markers.items()
    ]

    layout = go.Layout(
        xaxis=go.layout.XAxis(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            fixedrange=False,
            range=[-500, 500],
        ),
        yaxis=go.layout.YAxis(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            scaleanchor="x",
            scaleratio=0.8,
            range=[-500, 500],
        ),
        margin=dict(t=50, b=0, l=0, r=0),
        legend=dict(
            orientation="v",
            x=1.0,
            y=1.0,
            bgcolor="rgba(0,0,0,0)",
            font={"color": TEXT_DIM, "size": 11},
        ),
        height=config.CONFIG.plot_height,
        autosize=True,
        hovermode="closest",
        clickmode="event",
        plot_bgcolor=GALAXY_PLOT_BG,
        paper_bgcolor=GALAXY_PAPER,
        font={"color": TEXT_COLOR, "family": FONT_FAMILY},
        title=dict(
            text=f"Galaxy Map at {datamodel.days_to_date(slider_date)}",
            font=dict(size=18, color=TEXT_COLOR, family=FONT_FAMILY),
            x=0.5,
            xanchor="center",
        ),
    )

    fig = go.Figure(
        data=system_shapes + system_markers + edge_traces + country_borders,
        layout=layout,
    )
    return dcc.Graph(
        id="galaxy-map",
        figure=fig,
        animate=True,
        animation_options=dict(showAxisDragHandles=True),
        className="tl-graph",
        responsive=True,
        config=GRAPH_CONFIG,
        style={"width": "100%"},
    )


def get_country_color(game_id: int, country_name: str, alpha: float = 1.0) -> str:
    alpha = min(alpha, 1)
    alpha = max(alpha, 0)
    r, g, b = visualization_data.get_color_vals(game_id, country_name)
    color = f"rgba({r},{g},{b},{alpha})"
    return color


def start_dash_app(host, port):
    timeline_app.css.config.serve_locally = True
    timeline_app.scripts.config.serve_locally = True
    timeline_app.layout = get_layout()
    if config.CONFIG.production == True:
        from waitress import serve

        serve(timeline_app.server, host=host, port=port)
    else:
        timeline_app.run(host=host, port=port)


def _timelapse_field(label, input_component):
    """A labeled input row for the timelapse export controls."""
    return html.Div(
        className="tl-field",
        children=[html.Label(label, className="tl-control-label"), input_component],
    )


def get_layout():
    tab_names = list(config.CONFIG.tab_layout)
    tab_names.append(config.GALAXY_MAP_TAB)

    brand = html.Div(
        className="tl-brand",
        children=[
            html.Span("STELLARIS", className="tl-brand-title"),
            html.Span("DASHBOARD", className="tl-brand-sub"),
        ],
    )

    nav = html.Nav(
        className="tl-nav",
        children=[
            html.A("◂  Game Selection", className="tl-nav-link", id="index-link", href="/"),
            html.A("Settings", className="tl-nav-link", id="settings-link", href="/settings/"),
            html.A("Event Ledger", className="tl-nav-link", id="ledger-link", href="/history"),
            html.A(
                "Stellaris Wiki  ↗",
                className="tl-nav-link tl-nav-link--ext",
                id="wiki-link",
                href="https://stellaris.paradoxwikis.com/Stellaris_Wiki",
            ),
        ],
    )

    global_graph_controls = html.Div(
        className="tl-control-group",
        children=[
            html.Label(
                "Country perspective",
                className="tl-control-label",
                htmlFor="country-perspective-dropdown",
            ),
            dcc.Dropdown(
                id="country-perspective-dropdown",
                className="tl-dropdown",
                options=[],
                placeholder="Galaxy default",
                value=None,
            ),
            dcc.Checklist(
                id="dash-plot-checklist",
                className="tl-checklist",
                options=[
                    {
                        "label": "Normalize stacked plots",
                        "value": "normalize_stacked_plots",
                    },
                ],
                value=[],
            ),
        ],
    )

    galaxy_tab_ui = html.Div(
        id="galaxy-tab-ui",
        className="tl-galaxy-panel",
        style={"display": "none"},
        children=[
            html.H3("Galaxy Map", className="tl-section-title"),
            SELECT_SYSTEM_DEFAULT,
            html.Label("Date", className="tl-control-label"),
            dcc.Slider(
                id="dateslider",
                className="tl-slider",
                min=0,
                max=100,
                step=0.01,
                value=100,
                marks={},
            ),
            html.H3("Timelapse Export", className="tl-section-title"),
            _timelapse_field(
                "Start date",
                dcc.Input(
                    id="timelapse-start-input",
                    type="text",
                    placeholder=TIMELAPSE_DEFAULT_START,
                ),
            ),
            _timelapse_field(
                "End date",
                dcc.Input(
                    id="timelapse-end-input",
                    type="text",
                    placeholder="2210.01.01",
                ),
            ),
            _timelapse_field(
                "Step size (days)",
                dcc.Input(
                    id="timelapse-step-input",
                    type="number",
                    placeholder=TIMELAPSE_DEFAULT_STEP,
                ),
            ),
            _timelapse_field(
                "Frame time (ms)",
                dcc.Input(
                    id="timelapse-duration-input",
                    type="number",
                    placeholder=TIMELAPSE_DEFAULT_FRAME_TIME,
                ),
            ),
            _timelapse_field(
                "DPI",
                dcc.Input(
                    id="timelapse-dpi-input",
                    type="number",
                    placeholder=TIMELAPSE_DEFAULT_DPI,
                ),
            ),
            dcc.Checklist(
                id="timelapse-export-mode",
                className="tl-checklist",
                options=[
                    {
                        "label": "Export gif (large file)",
                        "title": (
                            "Export the timelapse as a single (large) gif file. "
                            "This requires a lot of memory"
                        ),
                        "value": "export_gif",
                    },
                    {
                        "label": "Export webp (smaller than gif, slow)",
                        "title": (
                            "Export the timelapse as a single (large) webp file. "
                            "Should end up smaller than the equivalent gif. "
                            "Requires the system WebP library to support animated WebP."
                        ),
                        "value": "export_webp",
                        "disabled": not features.check("webp_anim"),
                    },
                    {
                        "label": "Export frames",
                        "title": (
                            "Export the individual frames of the timelapse in png format. "
                            "You can use a tool (e.g. ffmpeg) to stitch them to a video timelapse."
                        ),
                        "value": "export_frames",
                    },
                    {
                        "label": "1:1 aspect ratio",
                        "title": (
                            "Use a 1:1 instead of 16:9 aspect ratio for the galaxy if checked."
                        ),
                        "value": "square_aspect_ratio",
                    },
                ],
                value=["export_gif"],
            ),
            html.Button(
                "Export Timelapse",
                id="galaxy-export-button",
                className="tl-button",
            ),
            html.Div(id="hidden-div", style={"display": "none"}),
        ],
    )

    tabs = dcc.Tabs(
        id="tabs-container",
        className="tl-tabs",
        parent_className="tl-tabs-parent",
        children=[
            dcc.Tab(
                id=tab_label,
                label=tab_label,
                value=tab_label,
                className="tl-tab",
                selected_className="tl-tab--selected",
            )
            for tab_label in tab_names
        ],
        value=tab_names[0],
    )

    sidebar = html.Aside(
        className="tl-sidebar",
        children=[
            brand,
            html.H1("Unknown Game", id="game-name-header", className="tl-game-name"),
            nav,
            global_graph_controls,
            galaxy_tab_ui,
        ],
    )

    main = html.Main(
        className="tl-main",
        # plot_width is a user setting; honor it as the fluid content's max width.
        style={"--tl-content-max": f"{config.CONFIG.plot_width}px"},
        children=[
            tabs,
            html.Div(id="tab-content", className="tl-content"),
        ],
    )

    modal = html.Div(
        id="plot-modal",
        className="tl-modal tl-modal--hidden",
        children=[
            html.Div(id="modal-backdrop", className="tl-modal__backdrop", n_clicks=0),
            html.Div(
                className="tl-modal__dialog",
                children=[
                    html.Div(
                        className="tl-modal__header",
                        children=[
                            html.Span("", id="modal-title", className="tl-modal__title"),
                            html.Button(
                                "✕",
                                id="modal-close",
                                className="tl-modal__close",
                                title="Close",
                                n_clicks=0,
                            ),
                        ],
                    ),
                    html.Div(
                        className="tl-modal__body",
                        children=[
                            dcc.Graph(
                                id="modal-graph",
                                className="tl-graph",
                                responsive=True,
                                config=GRAPH_CONFIG,
                                style={"width": "100%", "height": "100%"},
                            )
                        ],
                    ),
                ],
            ),
        ],
    )

    return html.Div(
        className="tl-app",
        children=[
            dcc.Location(id="url", refresh=False),
            sidebar,
            main,
            modal,
        ],
    )
