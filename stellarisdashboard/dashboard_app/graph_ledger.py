import json
import logging
import time
from typing import Dict, Any, List
from urllib import parse

import dash.exceptions
import plotly.graph_objs as go
from dash import Dash, callback_context, dcc, html, Input, Output, State, ALL

from stellarisdashboard import config, datamodel
from stellarisdashboard.dashboard_app import (
    utils,
    flask_app,
    visualization_data,
)

logger = logging.getLogger(__name__)

# Chart palette — concrete values mirroring the design tokens in assets/theme.css
# (Plotly figures need literal colours, not CSS vars). Plot backgrounds are
# transparent so the themed card surface shows through.
PLOT_PAPER = "rgba(0,0,0,0)"
PLOT_BG = "rgba(0,0,0,0)"
GRID_COLOR = "rgba(58,79,71,0.6)"  # --line
TEXT_COLOR = "rgba(217,217,217,1)"  # --text
TEXT_DIM = "rgba(151,163,156,1)"  # --text-dim
ACCENT_COLOR = "rgba(229,165,61,1)"  # --amber-bright
FONT_FAMILY = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"  # --font-body

# Stellaris games start on 2200.01.01; plot x-values count years from there.
GAME_START_YEAR = 2200

DEFAULT_PLOT_LAYOUT = dict(
    xaxis=dict(showgrid=False, zeroline=False, linecolor=GRID_COLOR, tickformat="d"),
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


COMPACT_SUBDUED_ALPHA = 0.28
COMPACT_PLAYER_WIDTH = 2.6


def _with_color_alpha(color, alpha):
    """Return an rgba() string with its alpha replaced; pass anything else through."""
    if not isinstance(color, str) or not color.startswith("rgba"):
        return color
    inner = color[color.index("(") + 1 : color.index(")")]
    r, g, b = (c.strip() for c in inner.split(",")[:3])
    return f"rgba({r},{g},{b},{alpha})"


def _emphasize_player_in_compact_data(figure_data, player_label):
    """For country-comparison plots, draw the player's series in full color on
    top and subdue every other country, so relative standing reads at a glance.
    Plots that don't contain the player's series (budget/demographics breakdowns)
    are returned unchanged."""
    if not player_label or not any(
        trace.get("name") == player_label for trace in figure_data
    ):
        return figure_data

    emphasized = []
    for trace in figure_data:
        trace = dict(trace)
        line = dict(trace.get("line") or {})
        if trace.get("name") == player_label:
            line["color"] = _with_color_alpha(line.get("color"), 1.0)
            line["width"] = COMPACT_PLAYER_WIDTH
        else:
            line["color"] = _with_color_alpha(line.get("color"), COMPACT_SUBDUED_ALPHA)
            line["width"] = min(line.get("width", 1.0), 1.0)
            trace["showlegend"] = False
            if "fillcolor" in trace:
                trace["fillcolor"] = _with_color_alpha(trace["fillcolor"], 0.12)
        trace["line"] = line
        emphasized.append(trace)
    # Draw the player's line last so it sits on top of the subdued ones.
    emphasized.sort(key=lambda t: t.get("name") == player_label)
    return emphasized


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


@timeline_app.callback(Output("galaxy-link", "href"), [Input("url", "search")])
def update_galaxy_link(search):
    game_id, _ = _get_game_ids_matching_url(search)
    if game_id:
        return f"/galaxy/{game_id}"
    return "/galaxy"


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
    Output("tab-content", "children"),
    [
        Input("tabs-container", "value"),
        Input("url", "search"),
        Input("dash-plot-checklist", "value"),
        Input("country-perspective-dropdown", "value"),
    ],
)
def update_content(tab_value, search, dash_plot_checklist, country_perspective):
    config.CONFIG.normalize_stacked_plots = (
        "normalize_stacked_plots" in dash_plot_checklist
    )
    game_id, matches = _get_game_ids_matching_url(search)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        # Dash escapes string children, so a rendered HTML template would show
        # up as its source text; return a component instead.
        return html.Div(
            [
                html.H2("Game not found"),
                html.P(f'No game matches "{game_id}".'),
                html.A("Back to game selection", href="/"),
            ],
            className="tl-content",
        )
    game_id = matches[0]
    games_dict = datamodel.get_available_games_dict()
    if game_id not in games_dict:
        logger.warning(
            f"Game ID {game_id} does not match any known game! (URL parameter {search})"
        )
        return []

    logger.info(f"dash_server.update_content: Tab is {tab_value}, Game is {game_id}")

    children = []
    if tab_value == config.MARKET_TAB:
        plots = visualization_data.get_market_graphs(config.CONFIG.market_resources)
    else:
        plots = (
            visualization_data.get_plot_specifications_for_tab_layout().get(tab_value)
            or []
        )
    plot_data = visualization_data.get_current_execution_plot_data(
        game_id, country_perspective
    )
    # Legend label of the player's own country, used to highlight it in the
    # compact thumbnails (see _emphasize_player_in_compact_data).
    player_country_name = games_dict[game_id].get("country_name")
    player_label = (
        dict_key_to_legend_label(player_country_name)
        if player_country_name
        else None
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

        compact_data = _emphasize_player_in_compact_data(figure_data, player_label)
        compact_figure = go.Figure(
            data=compact_data, layout=get_compact_figure_layout(plot_spec)
        )
        grid_children.append(_plot_grid_card(plot_spec, compact_figure))

    children.append(html.Div(grid_children, className="tl-grid"))
    children.append(dcc.Store(id="full-figures-store", data=full_figures))
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
        data = _get_raw_data_for_line_plot(game_id, plot_data, plot_spec)
    elif plot_spec.style in [
        visualization_data.PlotStyle.stacked,
        visualization_data.PlotStyle.budget,
    ]:
        data = _get_raw_data_for_stacked_and_budget_plots(game_id, plot_data, plot_spec)
    else:
        logger.warning(f"Unknown Plot type {plot_spec}")
        return []
    # The data x-values count years since game start; shift them to the in-game
    # year for display. Hover labels are already-formatted strings, so this only
    # affects the axis. (Done here so every plot style is covered in one place.)
    for trace in data:
        x_values = trace.get("x")
        if x_values:
            trace["x"] = [
                GAME_START_YEAR + v if v is not None else None for v in x_values
            ]
    return data


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


def get_country_color(game_id: str, country_name: str, alpha: float = 1.0) -> str:
    alpha = min(alpha, 1)
    alpha = max(alpha, 0)
    r, g, b = visualization_data.get_color_vals(game_id, country_name)
    color = f"rgba({r},{g},{b},{alpha})"
    return color


def start_dash_app(host, port):
    timeline_app.css.config.serve_locally = True
    timeline_app.scripts.config.serve_locally = True
    timeline_app.layout = get_layout()
    if config.CONFIG.production:
        from waitress import serve

        serve(timeline_app.server, host=host, port=port)
    else:
        timeline_app.run(host=host, port=port)


def get_layout():
    tab_names = list(config.CONFIG.tab_layout)

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
            html.A("Galaxy Map", className="tl-nav-link", id="galaxy-link", href="/galaxy"),
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
