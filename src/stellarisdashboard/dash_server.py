import logging
import time
from typing import Dict, Any, List

import dash
import dash_core_components as dcc
import dash_html_components as html
import flask
import plotly.graph_objs as go
from dash.dependencies import Input, Output

from stellarisdashboard import config, models, visualization_data

logger = logging.getLogger(__name__)

flask_app = flask.Flask(__name__)
flask_app.logger.setLevel(logging.WARNING)
app = dash.Dash(name="Stellaris Timeline", server=flask_app, url_base_pathname="/")
app.css.config.serve_locally = True
app.scripts.config.serve_locally = True

COLOR_PHYSICS = 'rgba(30,100,170,0.5)'
COLOR_SOCIETY = 'rgba(60,150,90,0.5)'
COLOR_ENGINEERING = 'rgba(190,150,30,0.5)'


def populate_available_games() -> Dict[str, models.Game]:
    games = {}
    for game_name in sorted(models.get_known_games()):
        with models.get_db_session(game_name) as session:
            game = session.query(models.Game).order_by(models.Game.game_name).one_or_none()
            if game is None:
                continue
            games[game_name] = game.player_country_name
    return games


AVAILABLE_GAMES = populate_available_games()
SELECTED_GAME_NAME = config.get_last_updated_game()
if AVAILABLE_GAMES and SELECTED_GAME_NAME is not None and SELECTED_GAME_NAME not in AVAILABLE_GAMES:
    logger.warning("Last updated game no longer available, fallback to arbitrary save game")
    SELECTED_GAME_NAME = next(iter(AVAILABLE_GAMES))

DEFAULT_SELECTED_PLOT = next(iter(visualization_data.THEMATICALLY_GROUPED_PLOTS.keys()))
DEFAULT_PLOT_LAYOUT = go.Layout(
    yaxis={
        "type": "linear"
    },
    height=640,
)

dropdown_options = [{'label': f"{country} ({g})", 'value': g} for g, country in AVAILABLE_GAMES.items()]
GAME_SELECTION_DROPDOWN = dcc.Dropdown(id='select-game-dropdown', options=dropdown_options, value=SELECTED_GAME_NAME, )
app.layout = html.Div([
    GAME_SELECTION_DROPDOWN,
    html.Div([
        dcc.Tabs(
            tabs=[
                {'label': category, 'value': category}
                for category in visualization_data.THEMATICALLY_GROUPED_PLOTS
            ],
            value=DEFAULT_SELECTED_PLOT,
            id='tabs',
        ),
        html.Div(id='tab-content', style={
            'width': '100%',
            'margin-left': 'auto',
            'margin-right': 'auto'
        }),
    ], style={
        'width': '100%',
        'fontFamily': 'Sans-Serif',
        'margin-left': 'auto',
        'margin-right': 'auto'
    }),
])


def update_selected_game(new_selected_game):
    global SELECTED_GAME_NAME
    populate_available_games()
    if new_selected_game in AVAILABLE_GAMES and new_selected_game != SELECTED_GAME_NAME:
        logger.info(f"Selected game is {new_selected_game}")
        SELECTED_GAME_NAME = new_selected_game
        visualization_data.get_current_execution_plot_data(SELECTED_GAME_NAME)  # to ensure everything is initialized before the dropdown's callback is handled
        GAME_SELECTION_DROPDOWN.value = new_selected_game


@app.callback(Output('tab-content', 'children'), [Input('tabs', 'value'),
                                                  Input('select-game-dropdown', 'value'), ])
def update_content(tab_value, game_id):
    children = []
    if game_id is not None:
        update_selected_game(game_id)
        children = [html.H1(f"{AVAILABLE_GAMES[game_id]} ({game_id})")]
        plots = visualization_data.THEMATICALLY_GROUPED_PLOTS[tab_value]
        for plot_spec in plots:
            figure_data = get_figure_data(plot_spec)
            figure_layout = get_figure_layout(plot_spec)
            figure = go.Figure(data=figure_data, layout=figure_layout)

            children.append(html.H2(f"{plot_spec.title}"))
            children.append(dcc.Graph(
                id=f"{plot_spec.plot_id}",
                figure=figure,
            ))
    return children


def get_figure_data(plot_spec: visualization_data.PlotSpecification):
    start = time.time()
    plot_data = visualization_data.get_current_execution_plot_data(SELECTED_GAME_NAME)
    plot_list = get_plot_lines(plot_data, plot_spec)
    end = time.time()
    logger.info(f"Update took {end - start} seconds!")
    return plot_list


def get_plot_lines(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification) -> List[Dict[str, Any]]:
    if plot_spec.style == visualization_data.PlotStyle.line:
        plot_list = _get_line_plot_data(plot_data, plot_spec)
    elif plot_spec.style == visualization_data.PlotStyle.stacked:
        plot_list = _get_stacked_plot_data(plot_data, plot_spec)
    elif plot_spec.style == visualization_data.PlotStyle.budget:
        plot_list = _get_budget_plot_data(plot_data, plot_spec)
    else:
        logger.warning(f"Unknown Plot type {plot_spec}")
        plot_list = []
    return sorted(plot_list, key=lambda p: p["y"][-1])


def _get_line_plot_data(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification):
    plot_list = []
    for key, x_values, y_values in plot_data.data_sorted_by_last_value(plot_spec):
        if not any(y_values):
            continue
        line = {'x': x_values, 'y': y_values, 'name': key, "text": [f"{val:.2f} - {key}" for val in y_values]}
        plot_list.append(line)
    return plot_list


def _get_stacked_plot_data(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification):
    y_previous = None
    plot_list = []
    for key, x_values, y_values in plot_data.data_sorted_by_last_value(plot_spec):
        if not any(y_values):
            continue
        line = {'x': x_values, 'name': key, "fill": "tonexty", "hoverinfo": "x+text"}
        if y_previous is None:
            y_previous = [0.0 for _ in x_values]
        y_previous = [(a + b) for a, b in zip(y_previous, y_values)]
        line["y"] = y_previous[:]  # make a copy
        if line["y"]:
            line["text"] = [f"{val:.2f} - {key}" if val else "" for val in y_values]
            if key == "physics":
                line["line"] = {"color": COLOR_PHYSICS}
                line["fillcolor"] = COLOR_PHYSICS
            elif key == "society":
                line["line"] = {"color": COLOR_SOCIETY}
                line["fillcolor"] = COLOR_SOCIETY
            elif key == "engineering":
                line["line"] = {"color": COLOR_ENGINEERING}
                line["fillcolor"] = COLOR_ENGINEERING
            plot_list.append(line)
    return plot_list


def _get_budget_plot_data(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification):
    net_gain = None
    y_previous_pos, y_previous_neg = None, None
    pos_initiated = False
    plot_list = []
    for key, x_values, y_values in plot_data.data_sorted_by_last_value(plot_spec):
        if not any(y_values):
            continue
        if net_gain is None:
            net_gain = [0.0 for _ in x_values]
            y_previous_pos = [0.0 for _ in x_values]
            y_previous_neg = [0.0 for _ in x_values]
        fill_mode = "tozeroy"
        if all(y <= 0 for y in y_values):
            y_previous = y_previous_neg
        elif all(y >= 0 for y in y_values):
            y_previous = y_previous_pos
            if pos_initiated:
                fill_mode = "tonexty"
            pos_initiated = True
        else:
            logger.warning("Not a real budget Graph!")
            break
        line = {'x': x_values, 'name': key, "hoverinfo": "x+text"}
        for i, y in enumerate(y_values):
            y_previous[i] += y
            net_gain[i] += y
        line["y"] = y_previous[:]
        line["fill"] = fill_mode
        line["text"] = [f"{val:.2f} - {key}" if val else "" for val in y_values]
        plot_list.append(line)
    plot_list.append({
        'x': plot_list[0]["x"],
        'y': net_gain,
        'name': 'Net gain',
        'line': {'color': 'rgba(0,0,0,1)'},
        'text': [f'{val:.2f} - net gain' for val in net_gain],
        'hoverinfo': 'x+text',
    })
    return plot_list


def get_figure_layout(plot_spec: visualization_data.PlotSpecification):
    layout = DEFAULT_PLOT_LAYOUT
    if plot_spec.style == visualization_data.PlotStyle.stacked:
        layout["yaxis"] = {}
    return go.Layout(**layout)


@app.callback(Output('select-game-dropdown', 'options'))
def update_game_options() -> List[Dict[str, str]]:
    global AVAILABLE_GAMES
    AVAILABLE_GAMES = sorted(models.get_known_games())
    logger.info("Updated list of available games:")
    for g in AVAILABLE_GAMES:
        logger.info(f"    {g}")
    return [{'label': g, 'value': g} for g in AVAILABLE_GAMES]


def start_server():
    app.run_server(port=config.CONFIG.port)


if __name__ == '__main__':
    start_server()
