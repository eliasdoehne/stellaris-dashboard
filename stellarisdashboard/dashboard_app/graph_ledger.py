import logging
import time
from collections import defaultdict
from typing import Dict, Any, List
from urllib import parse

import dash.exceptions
import plotly.graph_objs as go
from dash import Dash, callback_context, dcc, html, Input, Output
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

BACKGROUND = "rgba(33,43,39,1)"
GALAXY_BACKGROUND = "rgba(0,0,0,1)"
BACKGROUND_DARK = "rgba(20,25,25,1)"
TEXT_COLOR = "rgba(217,217,217,1)"
TEXT_HIGHLIGHT_COLOR = "rgba(195, 133, 33, 1)"
DEFAULT_PLOT_LAYOUT = dict(
    xaxis=dict(showgrid=False),
    yaxis=dict(showgrid=False, type="linear"),
    plot_bgcolor=BACKGROUND_DARK,
    paper_bgcolor=BACKGROUND,
    font={"color": TEXT_COLOR},
    showlegend=True,
)
BUTTON_STYLE = {
    "color": TEXT_HIGHLIGHT_COLOR,
    "font-family": "verdana",
    "font-size": "20px",
    "-webkit-appearance": "button",
    "-moz-appearance": "button",
    "appearance": "button",
    "background-color": BACKGROUND,
    "display": "inline",
    "text-decoration": "none",
    "padding": "0.1cm",
    "margin": "0.1cm",
}
HEADER_STYLE = {
    "font-family": "verdana",
    "color": TEXT_COLOR,
    "margin-top": "20px",
    "margin-bottom": "10px",
    "text-align": "center",
}
TEXT_STYLE = {
    "font-family": "verdana",
    "color": "rgba(217, 217, 217, 1)",
}
SELECTED_TAB_STYLE = {
    "width": "inherit",
    "boxShadow": "none",
    "borderLeft": "thin lightgrey solid",
    "borderRight": "thin lightgrey solid",
    "borderTop": "2px #0074D9 solid",
    "background": BACKGROUND,
    "color": TEXT_HIGHLIGHT_COLOR,
}
TAB_CONTAINER_STYLE = {
    "width": "inherit",
    "boxShadow": "inset 0px -1px 0px 0px lightgrey",
    "background": BACKGROUND,
}
TAB_STYLE = {
    "width": "inherit",
    "border": "none",
    "boxShadow": "inset 0px -1px 0px 0px lightgrey",
    "background": BACKGROUND_DARK,
    "color": TEXT_COLOR,
}
SELECT_SYSTEM_DEFAULT = html.P(
    children=[f"Click the map to select a system"],
    id="click-data",
    style=dict(width=f"{config.CONFIG.plot_width}px"),
)

TIMELAPSE_DEFAULT_START = "2200.01.01"
TIMELAPSE_DEFAULT_STEP = 120
TIMELAPSE_DEFAULT_FRAME_TIME = 100

timeline_app = Dash(
    __name__,
    title="Stellaris Dashboard",
    server=flask_app,
    compress=False,
    url_base_pathname="/timeline/",
)


def get_figure_layout(plot_spec: visualization_data.PlotSpecification):
    layout = dict(DEFAULT_PLOT_LAYOUT)
    layout["xaxis"]["title"] = plot_spec.x_axis_label
    layout["yaxis"]["title"] = plot_spec.y_axis_label
    layout["width"] = config.CONFIG.plot_width
    layout["height"] = config.CONFIG.plot_height
    if plot_spec.style == visualization_data.PlotStyle.line:
        layout["hovermode"] = "closest"
    else:
        layout["hovermode"] = "x"
    return go.Layout(**layout)


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
                and (c.has_met_player() or config.CONFIG.show_everything)
                and not c.is_other_player
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
        return ""
    points = clickData.get("points")
    if not points:
        return ""
    p = points[0]
    custom_data = p.get("customdata", {})
    system_id = custom_data.get("system_id")
    game_id = custom_data.get("game_id")
    text = p.get("text")
    if not system_id or not game_id or not text:
        return SELECT_SYSTEM_DEFAULT
    return html.P(
        children=[
            f"Selected system: ",
            html.A(
                children=text,
                href=utils.flask.url_for(
                    "history_page", game_id=game_id, system=system_id
                ),
                className="textlink",
            ),
        ]
    )


@timeline_app.callback(
    Output(component_id="galaxy-tab-ui", component_property="style"),
    [Input("tabs-container", "value")],
)
def show_hide_galaxy_tab_ui(tab_value):
    style_dict = {
        "display": "none",
        "width": f"{int(0.90 * config.CONFIG.plot_width)}px",
        "margin": "auto",
        # "text-align": "center",
        "padding-left": "1%",
        "padding-right": "1%",
        "background-color": BACKGROUND_DARK,
    }
    if tab_value == config.GALAXY_MAP_TAB:
        style_dict["display"] = "block"
    return style_dict


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

    export_gif = "export_gif" in export_mode
    export_webp = "export_webp" in export_mode
    export_frames = "export_frames" in export_mode

    changed_ids = [p["prop_id"].split(".")[0] for p in callback_context.triggered]
    button_pressed = "galaxy-export-button" in changed_ids
    if button_pressed:
        logger.info(f"Triggering timelapse export for {game_id}")
        logger.info(
            f"{start_date=}, {end_date=}, {step_days=}, {frame_time_ms=}, {export_gif=}, {export_webp=}, {export_frames=}"
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

        te = timelapse_exporter.TimelapseExporter(game_id)
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
        for plot_spec in plots:
            if not plot_spec:
                continue  # just in case it's possible to sneak in an invalid ID
            start = time.time()
            figure_data = get_raw_plot_data_dicts(plot_data, plot_spec)
            end = time.time()
            logger.debug(
                f"Prepared figure {plot_spec.title} in {end - start:5.3f} seconds."
            )
            if not figure_data:
                continue
            figure_layout = get_figure_layout(plot_spec)
            figure_layout["title"] = plot_spec.title
            figure = go.Figure(data=figure_data, layout=figure_layout)

            children.append(
                html.Div(
                    [
                        dcc.Graph(
                            id=plot_spec.plot_id,
                            figure=figure,
                            style=dict(textAlign="center"),
                        )
                    ],
                    style=dict(margin="auto", width=f"{config.CONFIG.plot_width}px"),
                )
            )
    else:
        slider_date = 0.01 * date_fraction * current_date

        children.append(
            html.Div(
                [get_galaxy(game_id, slider_date)],
                style=dict(
                    margin="auto",
                    width=f"{config.CONFIG.plot_width}px",
                    # height=f"{config.CONFIG.plot_height}px",
                    backgroundColor=BACKGROUND_DARK,
                ),
            )
        )
    return children


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
        return _get_raw_data_for_line_plot(plot_data, plot_spec)
    elif plot_spec.style in [
        visualization_data.PlotStyle.stacked,
        visualization_data.PlotStyle.budget,
    ]:
        return _get_raw_data_for_stacked_and_budget_plots(plot_data, plot_spec)
    else:
        logger.warning(f"Unknown Plot type {plot_spec}")
        return []


def _get_raw_data_for_line_plot(
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
            line={"color": get_country_color(key, 1.0)},
            text=get_plot_value_labels(x_values, y_values, key),
            hoverinfo="text",
        )
        plot_list.append(line)
    return plot_list


def _get_raw_data_for_stacked_and_budget_plots(
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

        stackgroup = "pos"
        if min(y_values) < 0:
            stackgroup = "neg"
        lines.append(
            dict(
                x=x_values,
                y=y_values,
                name=dict_key_to_legend_label(key),
                hoverinfo="text",
                mode="lines",
                line=dict(width=0.5, color=get_country_color(key, 1.0)),
                stackgroup=stackgroup,
                groupnorm="percent" if normalized else "",
                fillcolor=get_country_color(key, 0.5),
                text=get_plot_value_labels(x_values, y_values, key),
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
    galaxy = visualization_data.get_galaxy_data(game_id)
    graph = galaxy.get_graph_for_date(int(slider_date))
    edge_traces_data = {}
    for edge in graph.edges:
        country = graph.edges[edge]["country"]
        if country not in edge_traces_data:
            edge_traces_data[country] = dict(
                x=[],
                y=[],
                text=[],
                line=go.scatter.Line(width=0.5, color=get_country_color(country)),
                hoverinfo="text",
                mode="lines",
                showlegend=False,
            )
        x0, y0 = graph.nodes[edge[0]]["pos"]
        x1, y1 = graph.nodes[edge[1]]["pos"]
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
        visualization_data.GalaxyMapData.UNCLAIMED
    ] |= galaxy.galaxy_graph.graph.get("galaxy_edge_ridge_vertices", set())

    for i, node in enumerate(graph.nodes):
        country = graph.nodes[node]["country"]
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
        color = get_country_color(country)
        country_system_markers[country]["marker"]["color"].append(color)
        x, y = graph.nodes[node]["pos"]
        country_system_markers[country]["x"].append(x)
        country_system_markers[country]["y"].append(y)
        text = f'{graph.nodes[node]["name"]} ({country})'
        country_system_markers[country]["text"].append(text)
        customdata = {"system_id": graph.nodes[node]["system_id"], "game_id": game_id}
        country_system_markers[country]["customdata"].append(customdata)
        if country != visualization_data.GalaxyMapData.UNCLAIMED:
            shape_x, shape_y = graph.nodes[node].get("shape", ([], []))
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
        country_border_ridges[country] |= galaxy.galaxy_graph.nodes[node].get("ridge_vertices", set())

    country_borders = []
    for c1, r1 in country_border_ridges.items():
        for c2, r2 in country_border_ridges.items():
            if c2 <= c1:
                continue
            for rv1, rv2 in r1 & r2:
                country_borders.append(
                    go.Scatter(
                        dict(
                            x=[rv1[0], rv2[0]],
                            y=[rv1[1], rv2[1]],
                            text=[],
                            line=go.scatter.Line(
                                width=1.0, color="rgba(255,255,255,1)"
                            ),
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
        legend=dict(orientation="v", x=1.0, y=1.0),
        width=config.CONFIG.plot_width,
        height=config.CONFIG.plot_height,
        hovermode="closest",
        clickmode="event",
        plot_bgcolor=GALAXY_BACKGROUND,
        paper_bgcolor=BACKGROUND_DARK,
        font={"color": TEXT_COLOR},
        title=f"Galaxy Map at {datamodel.days_to_date(slider_date)}",
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
        style=dict(textAlign="center"),
    )


def get_country_color(country_name: str, alpha: float = 1.0) -> str:
    alpha = min(alpha, 1)
    alpha = max(alpha, 0)
    r, g, b = visualization_data.get_color_vals(country_name)
    color = f"rgba({r},{g},{b},{alpha})"
    return color


def start_dash_app(host, port):
    timeline_app.css.config.serve_locally = True
    timeline_app.scripts.config.serve_locally = True
    timeline_app.layout = get_layout()
    timeline_app.run_server(host=host, port=port)


def get_layout():
    tab_names = list(config.CONFIG.tab_layout)
    tab_names.append(config.GALAXY_MAP_TAB)
    top_navigation = html.Div(
        [
            html.A(
                html.Button("Game Selection", style=BUTTON_STYLE),
                id="index-link",
                href="/",
            ),
            html.A(
                html.Button(f"Settings", style=BUTTON_STYLE),
                id="settings-link",
                href="/settings/",
                style=BUTTON_STYLE,
            ),
            html.A(
                html.Button(f"Event Ledger", style=BUTTON_STYLE),
                id="ledger-link",
                href="/history",
                style=BUTTON_STYLE,
            ),
        ]
    )
    global_graph_controls = html.Div(
        [
            dcc.Dropdown(
                id="country-perspective-dropdown",
                options=[],
                placeholder="Select a country",
                value=None,
                style={
                    "width": "100%",
                    "verticalAlign": "middle",
                    "font-family": "verdana",
                    "color": TEXT_HIGHLIGHT_COLOR,
                    # "margin-top": "10px",
                    # "margin-bottom": "10px",
                    "text-align": "center",
                    "text-color": TEXT_HIGHLIGHT_COLOR,
                    "background": BACKGROUND_DARK,
                },
            ),
            dcc.Checklist(
                id="dash-plot-checklist",
                options=[
                    {
                        "label": "Normalize stacked plots",
                        "value": "normalize_stacked_plots",
                    },
                ],
                value=[],
                labelStyle=dict(color=TEXT_COLOR),
                style={
                    "verticalAlign": "center",
                    "width": "50%",
                },
            ),
        ],
        style={"display": "flex", "width": "100%"},
    )
    galaxy_tab_ui = html.Div(
        [
            html.H3("Galaxy Map Controls"),
            SELECT_SYSTEM_DEFAULT,
            dcc.Slider(
                id="dateslider",
                min=0,
                max=100,
                step=0.01,
                value=100,
                marks={},
            ),
            html.H3("Timelapse Export"),
            html.Div(
                [
                    html.P(f"Start date"),
                    dcc.Input(
                        id="timelapse-start-input",
                        type="text",
                        placeholder=TIMELAPSE_DEFAULT_START,
                    ),
                ],
                style={"display": "inline-block", "width": "20%"},
            ),
            html.Div(
                [
                    html.P("End date"),
                    dcc.Input(
                        id="timelapse-end-input",
                        type="text",
                        placeholder="2210.01.01",
                    ),
                ],
                style={"display": "inline-block", "width": "20%"},
            ),
            html.Div(
                [
                    html.P(f"Step size (days)"),
                    dcc.Input(
                        id="timelapse-step-input",
                        type="number",
                        placeholder=TIMELAPSE_DEFAULT_STEP,
                    ),
                ],
                style={"display": "inline-block", "width": "20%"},
            ),
            html.Div(
                [
                    html.P(f"Frame time (ms)"),
                    dcc.Input(
                        id="timelapse-duration-input",
                        type="number",
                        placeholder=TIMELAPSE_DEFAULT_FRAME_TIME,
                    ),
                ],
                style={"display": "inline-block", "width": "20%"},
            ),
            html.Div(
                [
                    dcc.Checklist(
                        id="timelapse-export-mode",
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
                        ],
                        value=[
                            "export_gif",
                        ],
                        labelStyle=dict(color=TEXT_COLOR),
                        style={"text-align": "center"},
                    ),
                ],
                style={"display": "inline-block"},
            ),
            html.Button(
                f"Export Timelapse",
                id="galaxy-export-button",
                style=BUTTON_STYLE,
            ),
            html.Div(id="hidden-div", style={"display": "none"}),
        ],
        style={
            "display": "block",
            "width": "100%",
            "height": "100%",
            "margin-left": "auto",
            "margin-right": "auto",
        },
        id="galaxy-tab-ui",
    )

    tabs = dcc.Tabs(
        id="tabs-container",
        style=TAB_CONTAINER_STYLE,
        parent_style=TAB_CONTAINER_STYLE,
        children=[
            dcc.Tab(
                id=tab_label,
                label=tab_label,
                value=tab_label,
                style=TAB_STYLE,
                selected_style=SELECTED_TAB_STYLE,
            )
            for tab_label in tab_names
        ],
        value=tab_names[0],
    )
    return html.Div(
        [
            dcc.Location(id="url", refresh=False),
            html.Div(
                [
                    top_navigation,
                    html.H1(
                        children="Unknown Game",
                        id="game-name-header",
                        style=HEADER_STYLE,
                    ),
                    global_graph_controls,
                    tabs,
                    html.Div(
                        id="tab-content",
                        style={
                            "width": "100%",
                            "height": "100%",
                            "margin-left": "auto",
                            "margin-right": "auto",
                        },
                    ),
                    galaxy_tab_ui,
                ],
                style={
                    "width": "100%",
                    "height": "100%",
                    "fontFamily": "Sans-Serif",
                    "margin-left": "auto",
                    "margin-right": "auto",
                },
            ),
        ],
        style={
            "width": "100%",
            "height": "100%",
            "padding": 0,
            "margin": 0,
            "background-color": BACKGROUND,
        },
    )
