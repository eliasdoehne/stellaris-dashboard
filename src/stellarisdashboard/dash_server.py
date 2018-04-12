import logging
import random
import time
from typing import Dict, Any, List
from urllib import parse

import dash
import dash_core_components as dcc
import dash_html_components as html
import flask
import plotly.graph_objs as go
from dash.dependencies import Input, Output
from flask import render_template

from stellarisdashboard import config, models, visualization_data, game_info

logger = logging.getLogger(__name__)

flask_app = flask.Flask(__name__)
flask_app.logger.setLevel(logging.DEBUG)
app = dash.Dash(name="Stellaris Timeline", server=flask_app, compress=False, url_base_pathname="/timeline")
app.css.config.serve_locally = True
app.scripts.config.serve_locally = True

COLOR_PHYSICS = 'rgba(30,100,170,0.5)'
COLOR_SOCIETY = 'rgba(60,150,90,0.5)'
COLOR_ENGINEERING = 'rgba(190,150,30,0.5)'


@flask_app.route("/")
def index_page():
    games = [dict(country=country, game_name=g) for g, country in get_available_games_dict().items()]
    return render_template("index.html", games=games)


@flask_app.route("/history/<game_name>")
def history_page(game_name):
    games_dict = get_available_games_dict()
    if game_name not in games_dict:
        matches = list(models.get_game_names_matching(game_name))
        if not matches:
            logger.warning(f"Could not find a game matching {game_name}")
            return "404"
        game_name = matches[0]
    country = games_dict[game_name]
    with models.get_db_session(game_name) as session:
        last_gs = session.query(models.GameState).order_by(models.GameState.date.desc()).first()
        if last_gs is None:
            logger.warning(f"Found no gamestate for game {game_name}...")
            return "500"
        wars = get_war_dicts(session, last_gs.date)
        leaders = get_leader_dicts(session, last_gs.date)
    print(leaders[:5])
    return render_template("history_page.html", country=country, wars=wars, leaders=leaders)


def get_available_games_dict() -> Dict[str, str]:
    """ Returns a dictionary mapping game id to the name of the game's player country. """
    games = {}
    for game_name in sorted(models.get_known_games()):
        with models.get_db_session(game_name) as session:
            game = session.query(models.Game).order_by(models.Game.game_name).one_or_none()
            if game is None:
                continue
            games[game_name] = game.player_country_name
    return games


DEFAULT_PLOT_LAYOUT = go.Layout(
    yaxis=dict(
        type="linear",
    ),
    height=640,
)

CATEGORY_TABS = [{'label': category, 'value': category} for category in visualization_data.THEMATICALLY_GROUPED_PLOTS]
DEFAULT_SELECTED_CATEGORY = next(iter(visualization_data.THEMATICALLY_GROUPED_PLOTS.keys()))

app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    html.A("Return to index", id='index-link', href="/"),
    html.Div([
        dcc.Tabs(
            tabs=CATEGORY_TABS,
            value=DEFAULT_SELECTED_CATEGORY,
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


def get_figure_layout(plot_spec: visualization_data.PlotSpecification):
    layout = DEFAULT_PLOT_LAYOUT
    if plot_spec.style == visualization_data.PlotStyle.stacked:
        layout["yaxis"] = {}
    return go.Layout(**layout)


@app.callback(Output('tab-content', 'children'), [Input('tabs', 'value'), Input('url', 'search')])
def update_content(tab_value, search):
    game_id = parse.parse_qs(parse.urlparse(search).query).get("game_name", [None])[0]
    if game_id is None:
        game_id = ""
    available_games = get_available_games_dict()
    if game_id not in available_games:
        for g in available_games:
            if g.startswith(game_id):
                logger.info(f"Found game {g} matching prefix {game_id}!")
                game_id = g
                break
        else:
            logger.warning(f"Game {game_id} does not match any known game!")
            return []
    logger.info(f"dash_server.update_content: Tab is {tab_value}, Game is {game_id}")
    children = [html.H1(f"{available_games[game_id]} ({game_id})")]
    plots = visualization_data.THEMATICALLY_GROUPED_PLOTS[tab_value]
    for plot_spec in plots:
        figure_data = get_figure_data(game_id, plot_spec)
        figure_layout = get_figure_layout(plot_spec)
        figure = go.Figure(data=figure_data, layout=figure_layout)

        children.append(html.H2(f"{plot_spec.title}"))
        children.append(dcc.Graph(
            id=f"{plot_spec.plot_id}",
            figure=figure,
        ))
    return children


def get_most_recent_date(session):
    most_recent_gs = session.query(models.GameState).order_by(models.GameState.date.desc()).first()
    if most_recent_gs is None:
        most_recent_date = 0
    else:
        most_recent_date = most_recent_gs.date
    return most_recent_date


def get_figure_data(game_id: str, plot_spec: visualization_data.PlotSpecification):
    start = time.time()
    plot_data = visualization_data.get_current_execution_plot_data(game_id)
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
    for key, x_values, y_values in plot_data.iterate_data(plot_spec):
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
    if plot_list:
        plot_list.append({
            'x': plot_list[0]["x"],
            'y': net_gain,
            'name': 'Net gain',
            'line': {'color': 'rgba(0,0,0,1)'},
            'text': [f'{val:.2f} - net gain' for val in net_gain],
            'hoverinfo': 'x+text',
        })
    return plot_list


def get_leader_dicts(session, most_recent_date):
    rulers = []
    scientists = []
    governors = []
    admirals = []
    generals = []
    for leader in session.query(models.Leader).order_by(models.Leader.date_hired).all():
        leader_dict = dict(
            name=leader.leader_name,
            in_game_id=leader.leader_id_in_game,
            birthday=models.days_to_date(leader.date_born),
            date_hired=models.days_to_date(leader.date_hired),
            status=f"active (as of {models.days_to_date(most_recent_date)})",
            species=leader.species.species_name,
        )
        if leader.last_date < most_recent_date - 720:
            leader_dict["status"] = f"dismissed or deceased around {models.days_to_date(leader.last_date + random.randint(0, 30))}"
        leader_dict["achievements"] = [str(a) for a in leader.achievements]
        if leader.leader_class == models.LeaderClass.scientist:
            leader_dict["class"] = "Scientist"
            scientists.append(leader_dict)
        elif leader.leader_class == models.LeaderClass.governor:
            leader_dict["class"] = "Governor"
            governors.append(leader_dict)
        elif leader.leader_class == models.LeaderClass.admiral:
            leader_dict["class"] = "Admiral"
            admirals.append(leader_dict)
        elif leader.leader_class == models.LeaderClass.general:
            leader_dict["class"] = "General"
            generals.append(leader_dict)
        elif leader.leader_class == models.LeaderClass.ruler:
            leader_dict["class"] = "Ruler"
            rulers.append(leader_dict)

    leaders = (
            rulers
            + scientists
            + governors
            + admirals
            + generals
    )
    return leaders


def get_war_dicts(session, current_date):
    wars = []
    for war in session.query(models.War).order_by(models.War.start_date_days).all():
        start = models.days_to_date(war.start_date_days)
        end = models.days_to_date(current_date)
        if war.end_date_days:
            end = models.days_to_date(war.end_date_days)

        attackers = [
            f'{wp.country.country_name}: "{wp.war_goal}" war goal' for wp in war.participants
            if wp.is_attacker
        ]
        defenders = [
            f'{wp.country.country_name}: "{wp.war_goal}" war goal' for wp in war.participants
            if not wp.is_attacker
        ]

        victories = sorted([we for wp in war.participants for we in wp.victories], key=lambda we: we.date)
        war_event_list = []
        for vic in victories:
            country_name = vic.war_participant.country.country_name
            if vic.combat_type == models.CombatType.ships:
                war_event_list.append(
                    f"{models.days_to_date(vic.date)}: {country_name} fleet combat victory in the {vic.system} system. War exhaustion {vic.inflicted_war_exhaustion}"
                )
            elif vic.combat_type == models.CombatType.armies:
                verb = "defended against" if vic.attacker_victory else "succeeded in"
                war_event_list.append(
                    f"{models.days_to_date(vic.date)}: {country_name} {verb} planetary invasion of {vic.planet}. War exhaustion {vic.inflicted_war_exhaustion}"
                )
        wars.append(dict(
            name=war.name,
            start=start,
            end=end,
            attackers=attackers,
            defenders=defenders,
            combat=war_event_list,

        ))

    return wars


def start_server():
    app.run_server(port=config.CONFIG.port)


if __name__ == '__main__':
    start_server()
