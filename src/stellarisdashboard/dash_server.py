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

flask_app = flask.Flask(__name__)  # in case we want to extend other functionality later, e.g. a ledger
app = dash.Dash(name="Stellaris Timeline", server=flask_app, url_base_pathname="/")
app.css.config.serve_locally = True
app.scripts.config.serve_locally = True


def populate_available_games() -> Dict[str, models.Game]:
    session = models.SessionFactory()
    games = {g.game_name: g.player_country_name for g in session.query(models.Game).order_by(models.Game.game_name).all()}
    session.close()
    return games


AVAILABLE_GAMES = populate_available_games()
SELECTED_GAME_NAME = None
if AVAILABLE_GAMES:
    SELECTED_GAME_NAME = next(iter(AVAILABLE_GAMES.keys()))  # "unitednationsofearth_-15512622"  #

DEFAULT_SELECTED_PLOT = next(iter(visualization_data.THEMATICALLY_GROUPED_PLOTS.keys()))

DEFAULT_PLOT_LAYOUT = go.Layout(
    yaxis={
        "type": "linear"
    },
    height=480,
)

GAME_SELECTION_DROPDOWN = dcc.Dropdown(id='select-game-dropdown', options=[{'label': f"{country} ({g})", 'value': g} for g, country in AVAILABLE_GAMES.items()], value=SELECTED_GAME_NAME, )
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


def get_plot_data() -> visualization_data.EmpireProgressionPlotData:
    return visualization_data.get_current_execution_plot_data(SELECTED_GAME_NAME)


def update_selected_game(new_selected_game):
    global SELECTED_GAME_NAME
    if new_selected_game and new_selected_game != SELECTED_GAME_NAME:
        print(f"Selected game is {new_selected_game}")
        SELECTED_GAME_NAME = new_selected_game
        GAME_SELECTION_DROPDOWN.value = new_selected_game


@app.callback(Output('tab-content', 'children'), [Input('tabs', 'value'),
                                                  Input('select-game-dropdown', 'value'), ])
def update_content(tab_value, game_id):
    update_selected_game(game_id)
    children = [html.H3(f"{AVAILABLE_GAMES[game_id]} ({game_id})")]
    plots = visualization_data.THEMATICALLY_GROUPED_PLOTS[tab_value]
    for plot_spec in plots:
        figure_data = get_figure_data(plot_spec)
        figure_layout = get_figure_layout(plot_spec)
        figure = go.Figure(data=figure_data, layout=figure_layout)

        children.append(html.H3(f"{plot_spec.title}"))
        children.append(dcc.Graph(
            id=f"{plot_spec.plot_id}",
            figure=figure,
        ))
    return children


def get_figure_data(plot_spec: visualization_data.PlotSpecification):
    start = time.time()
    plot_data = get_plot_data()
    plot_list = get_plot_lines(plot_data, plot_spec)
    end = time.time()
    logger.debug(f"Update took {end - start} seconds!")
    return plot_list


def get_plot_lines(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification) -> List[Dict[str, Any]]:
    plot_list = []
    y_previous = None
    y_previous_pos, y_previous_neg = None, None
    for key, x_values, y_values in plot_data.iterate_data_sorted(plot_spec):
        if not any(y_values):
            continue
        line = {'x': x_values, 'y': y_values, 'name': key, "text": [f"{val:.1f} - {key}" for val in y_values]}
        if plot_spec.style == visualization_data.PlotStyle.stacked:
            if y_previous is None:
                y_previous = [0.0 for _ in x_values]
            line["text"] = [f"{val:.1f} - {key}" for val in line["y"]]
            y_previous = [(a + b) for a, b in zip(y_previous, y_values)]
            line["y"] = [a for a in y_previous]
            line["hoverinfo"] = "x+text"
            line["fill"] = "tonexty"
        elif plot_spec.style == visualization_data.PlotStyle.budget:
            if y_previous_pos is None:
                y_previous = [0.0 for _ in x_values]  # use y_previous to record the net gain/loss
                y_previous_pos = [0.0 for _ in x_values]
                y_previous_neg = [0.0 for _ in x_values]
            line["text"] = [f"{val:.1f} - {key}" for val in line["y"]]
            if all(y <= 0 for y in y_values):
                y_prev = y_previous_neg
                is_positive = False
            elif all(y >= 0 for y in y_values):
                y_prev = y_previous_pos
                is_positive = True
            else:
                logger.warning("Not a real budget Graph!")
                break
            for i, y in enumerate(y_values):
                y_prev[i] += y
                y_previous[i] += y
            line["y"] = y_prev[:]
            line["hoverinfo"] = "x+text"
            line["fill"] = "tonexty" if is_positive else "tozeroy"
        if line["y"]:
            plot_list.append(line)
    if plot_list and plot_spec.style == visualization_data.PlotStyle.budget:
        plot_list.append({
            "x": plot_list[0]["x"],
            'y': y_previous,
            'name': 'Net gain',
            "text": [f"{val:.1f} - net gain" for val in y_previous],
            "fill": "tozeroy",
            "hoverinfo": "x+text",
        })
    return sorted(plot_list, key=lambda p: p["y"][-1])


def get_figure_layout(plot_spec: visualization_data.PlotSpecification):
    layout = DEFAULT_PLOT_LAYOUT
    if plot_spec.style == visualization_data.PlotStyle.stacked:
        layout["yaxis"] = {}
    return go.Layout(**layout)


@app.callback(Output('select-game-dropdown', 'options'))
def update_game_options(n) -> List[Dict[str, str]]:
    global AVAILABLE_GAMES
    AVAILABLE_GAMES = populate_available_games()
    print("Updated game list:")
    for g in AVAILABLE_GAMES:
        print(f"    {g}")
    return [{'label': g, 'value': g} for g in AVAILABLE_GAMES]


def start_server():
    app.run_server(port=config.CONFIG.port)


if __name__ == '__main__':
    start_server()
