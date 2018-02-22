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
    session = models.SessionFactory()
    games = {g.game_name: g for g in session.query(models.Game).order_by(models.Game.game_name).all()}
    session.close()
    return games


AVAILABLE_GAMES = populate_available_games()
SELECTED_GAME_NAME = None
if AVAILABLE_GAMES:
    SELECTED_GAME_NAME = next(iter(AVAILABLE_GAMES.keys()))  # "unitednationsofearth_-15512622"  #

DEFAULT_SELECTED_PLOT = next(iter(visualization.THEMATICALLY_GROUPED_PLOTS.keys()))

DEFAULT_PLOT_LAYOUT = go.Layout(
    yaxis={"type": "linear"},
    height=480,
)
GRAPHS = {g_id: dcc.Graph(id='pop-count-graph', figure={"layout": DEFAULT_PLOT_LAYOUT}) for g_id in visualization.PLOT_SPECIFICATIONS}

app.layout = html.Div([
    dcc.Dropdown(
        id='select-game-dropdown',
        options=[
            {'label': g, 'value': g} for g in AVAILABLE_GAMES
        ],
        value=SELECTED_GAME_NAME,
    ),
    html.Div([
        dcc.Tabs(
            tabs=[
                {'label': category, 'value': category}
                for category in visualization.THEMATICALLY_GROUPED_PLOTS
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


def get_plot_data() -> visualization.EmpireProgressionPlotData:
    return visualization.get_current_execution_plot_data(SELECTED_GAME_NAME)


def update_selected_game(new_selected_game):
    global SELECTED_GAME_NAME
    if new_selected_game and new_selected_game != SELECTED_GAME_NAME:
        print(f"Selected game is {new_selected_game}")
        SELECTED_GAME_NAME = new_selected_game


@app.callback(Output('tab-content', 'children'), [Input('tabs', 'value'),
                                                  Input('select-game-dropdown', 'value'), ])
def update_content(tab_value, game_value):
    update_selected_game(game_value)
    children = []
    plots = visualization.THEMATICALLY_GROUPED_PLOTS[tab_value]
    for plot_spec in plots:
        figure_data = get_figure_data(plot_spec)
        figure_layout = get_figure_layout(plot_spec)
        figure = {'data': figure_data, 'layout': figure_layout}

        children.append(html.H3(f"{plot_spec.title}  -  {SELECTED_GAME_NAME}"))
        children.append(dcc.Graph(
            id=f"{plot_spec.plot_id}",
            figure=figure,
        ))
    return children


def get_figure_data(plot_spec: visualization.PlotSpecification):
    start = time.time()
    plot_data = get_plot_data()
    plot_list = get_plot_lines(plot_data, plot_spec)
    end = time.time()
    print(f"Update took {end - start} seconds!")
    return plot_list


def get_plot_lines(plot_data: visualization.EmpireProgressionPlotData, plot_spec: visualization.PlotSpecification) -> List[Dict[str, Any]]:
    plot_list = []
    y_previous = None
    for key, x_values, y_values in plot_data.iterate_data_sorted(plot_spec):
        line = {'x': x_values, 'y': y_values, 'name': key, "text": [f"{val:.1f}% - {key}" for val in y_values]}
        if plot_spec.style == visualization.PlotStyle.stacked:
            if y_previous is None:
                y_previous = [0.0 for _ in x_values]
            line["text"] = [f"{val:.1f}% - {key}" for val in line["y"]]
            y_previous = [(a + b) for a, b in zip(y_previous, y_values)]
            line["y"] = [a for a in y_previous]
            line["hoverinfo"] = "x+text"
            line["fill"] = "tonexty"
        if line["y"]:
            plot_list.append(line)
    return sorted(plot_list, key=lambda p: p["y"][-1])


def get_figure_layout(plot_spec: visualization.PlotSpecification):
    layout = DEFAULT_PLOT_LAYOUT
    if plot_spec.style == visualization.PlotStyle.stacked:
        layout["yaxis"] = {}
    return layout


@app.callback(Output('select-game-dropdown', 'options'))
def update_game_options(n) -> List[Dict[str, str]]:
    global AVAILABLE_GAMES
    AVAILABLE_GAMES = populate_available_games()
    print("Updated game list:")
    for g in AVAILABLE_GAMES:
        print(f"    {g}")
    return [{'label': g, 'value': g} for g in AVAILABLE_GAMES]


def start_server():
    app.run_server()


if __name__ == '__main__':
    start_server()
