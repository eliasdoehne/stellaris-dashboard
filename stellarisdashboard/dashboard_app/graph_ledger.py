import logging
import time
from typing import Dict, Any, List
from urllib import parse

import dash
import dash_core_components as dcc
import dash_html_components as html
import plotly.graph_objs as go
from dash.dependencies import Input, Output
from flask import render_template

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import utils, flask_app, visualization_data

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
DROPDOWN_STYLE = {
    "width": "100%",
    "font-family": "verdana",
    "color": TEXT_HIGHLIGHT_COLOR,
    "margin-top": "10px",
    "margin-bottom": "10px",
    "text-align": "center",
    "text-color": TEXT_HIGHLIGHT_COLOR,
    "background": BACKGROUND_DARK,
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


timeline_app = dash.Dash(
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
                options.append({"label": c.country_name, "value": c.country_id_in_game})
    return options


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
                    style=dict(
                        margin="auto",
                        width=f"{config.CONFIG.plot_width}px",
                        height=f"{config.CONFIG.plot_height}px",
                    ),
                )
            )
    else:
        slider_date = 0.01 * date_fraction * current_date
        children.append(get_galaxy(game_id, slider_date))
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
    for i, node in enumerate(graph.nodes):
        country = graph.nodes[node]["country"]
        if country not in country_system_markers:
            country_system_markers[country] = dict(
                x=[],
                y=[],
                text=[],
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
        country_system_markers[country]["text"].append(
            f'{graph.nodes[node]["name"]} ({country})'
        )
        if country != visualization_data.GalaxyMapData.UNCLAIMED:
            shape_x, shape_y = graph.nodes[node].get("shape", ([], []))
            system_shapes.append(
                go.Scatter(
                    x=shape_x,
                    y=shape_y,
                    fill="toself",
                    fillcolor=color,
                    hoverinfo="none",
                    line=dict(width=0),
                    mode="none",
                    opacity=0.2,
                    showlegend=False,
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
        plot_bgcolor=GALAXY_BACKGROUND,
        paper_bgcolor=BACKGROUND_DARK,
        font={"color": TEXT_COLOR},
        title=f"Galaxy Map at {datamodel.days_to_date(slider_date)}",
    )

    return dcc.Graph(
        id="galaxy-map",
        figure=go.Figure(
            data=system_shapes + system_markers + edge_traces, layout=layout,
        ),
        animate=True,
        animation_options=dict(showAxisDragHandles=True,),
    )


def get_country_color(country_name: str, alpha: float = 1.0) -> str:
    alpha = min(alpha, 1)
    alpha = max(alpha, 0)
    r, g, b = visualization_data.get_color_vals(country_name)
    color = f"rgba({r},{g},{b},{alpha})"
    return color


def start_dash_app(port):
    timeline_app.css.config.serve_locally = True
    timeline_app.scripts.config.serve_locally = True
    timeline_app.layout = get_layout()
    timeline_app.run_server(port=port)


def get_layout():
    tab_names = list(config.CONFIG.tab_layout)
    tab_names.append(config.GALAXY_MAP_TAB)
    return html.Div(
        [
            dcc.Location(id="url", refresh=False),
            html.Div(
                [
                    html.Div(
                        [
                            html.A(
                                "Go to Game Selection",
                                id="index-link",
                                href="/",
                                style=BUTTON_STYLE,
                            ),
                            html.A(
                                f"Settings",
                                id="settings-link",
                                href="/settings/",
                                style=BUTTON_STYLE,
                            ),
                            html.A(
                                f"Go to Event Ledger",
                                id="ledger-link",
                                href="/history",
                                style=BUTTON_STYLE,
                            ),
                        ]
                    ),
                    html.H1(
                        children="Unknown Game",
                        id="game-name-header",
                        style=HEADER_STYLE,
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
                        style={"text-align": "center"},
                    ),
                    dcc.Dropdown(
                        id="country-perspective-dropdown",
                        options=[],
                        placeholder="Select a country",
                        value=None,
                        style=DROPDOWN_STYLE,
                    ),
                    dcc.Tabs(
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
                    ),
                    html.Div(
                        id="tab-content",
                        style={
                            "width": "100%",
                            "height": "100%",
                            "margin-left": "auto",
                            "margin-right": "auto",
                        },
                    ),
                    dcc.Slider(
                        id="dateslider",
                        min=0,
                        max=100,
                        step=0.01,
                        value=100,
                        marks={i: "{}%".format(i) for i in range(0, 110, 10)},
                    ),
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
