import time
from typing import Dict, Any, List

import dash
import dash_core_components as dcc
import dash_html_components as html
import flask
import plotly.graph_objs as go
from dash.dependencies import Input, Output

from stellaristimeline import models, visualization

flask_app = flask.Flask(__name__)  # in case we want to extend other functionality later, e.g. a ledger
app = dash.Dash(name="Stellaris Timeline", server=flask_app, url_base_pathname="/")
app.css.config.serve_locally = True
app.scripts.config.serve_locally = True


def populate_available_games() -> Dict[str, models.Game]:
    return {g.game_name: g for g in models.Game.query.all()}


AVAILABLE_GAMES = populate_available_games()
SELECTED_GAME_NAME = None
if AVAILABLE_GAMES:
    SELECTED_GAME_NAME = next(iter(AVAILABLE_GAMES.keys()))

app.layout = html.Div([
    html.H1('Select your game'),
    dcc.Dropdown(
        id='select-game-dropdown',
        options=[
            {'label': g, 'value': g} for g in AVAILABLE_GAMES
        ],
        value=SELECTED_GAME_NAME,
    ),
    html.H1('Population count'),
    dcc.Graph(
        id='pop-count-graph',
        figure={"layout": go.Layout(yaxis={"type": "log"})},
    ),
    html.H1('Surveyed Systems'),
    dcc.Graph(
        id='survey-count-graph',
        figure={"layout": go.Layout(yaxis={"type": "log"})},
    ),
    dcc.Interval(
        id='interval-component',
        interval=3000,  # in milliseconds
        n_intervals=0
    )
])


def get_plot_data() -> visualization.EmpireProgressionPlotData:
    session = models.SessionFactory()
    game: models.Game = session.query(models.Game).filter(models.Game.game_name == SELECTED_GAME_NAME).one()
    return visualization.get_current_execution_plot_data(game)


def get_plot_lines(plot_data: visualization.EmpireProgressionPlotData, plot_data_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    plot_list = []
    for country, pop_count in visualization.EmpireProgressionPlotData.iterate_data_sorted(plot_data_dict):
        plot_list.append({
            'x': plot_data.dates,
            'y': pop_count,
            'name': country,
        })
    return plot_list


def update_selected_game(new_selected_game):
    global SELECTED_GAME_NAME
    assert new_selected_game in AVAILABLE_GAMES
    SELECTED_GAME_NAME = new_selected_game


@app.callback(Output('pop-count-graph', 'figure'), [Input('interval-component', 'n_intervals'), Input('select-game-dropdown', 'value')])
def update_graph(n, value):
    print(f"Selected game is {value}")
    update_selected_game(value)
    start = time.time()
    plot_data = get_plot_data()
    plot_list = get_plot_lines(plot_data, plot_data.pop_count)
    end = time.time()
    print(f"Update took {end - start} seconds!")
    return {'data': plot_list}


@app.callback(Output('select-game-dropdown', 'options'), [Input('interval-component', 'n_intervals')])
def update_game_options(n_intervals) -> List[Dict[str, str]]:
    global AVAILABLE_GAMES
    AVAILABLE_GAMES = populate_available_games()
    print("Updated game list:")
    for g in AVAILABLE_GAMES:
        print(f"    {g}")
    return [{'label': g, 'value': g} for g in AVAILABLE_GAMES]


if __name__ == '__main__':
    app.run_server()
