import logging
import random
import time
from typing import Dict, Any, List

import dash
import dash_core_components as dcc
import dash_html_components as html
import flask
import plotly.graph_objs as go
from dash.dependencies import Input, Output

from stellarisdashboard import config, models, visualization_data, game_info

logger = logging.getLogger(__name__)

flask_app = flask.Flask(__name__)
flask_app.logger.setLevel(logging.DEBUG)
app = dash.Dash(name="Stellaris Timeline", server=flask_app, url_base_pathname="/")
app.css.config.serve_locally = True
app.scripts.config.serve_locally = True

COLOR_PHYSICS = 'rgba(30,100,170,0.5)'
COLOR_SOCIETY = 'rgba(60,150,90,0.5)'
COLOR_ENGINEERING = 'rgba(190,150,30,0.5)'


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


AVAILABLE_GAMES = get_available_games_dict()
SELECTED_GAME_NAME = config.get_last_updated_game()
if AVAILABLE_GAMES and SELECTED_GAME_NAME is not None and SELECTED_GAME_NAME not in AVAILABLE_GAMES:
    logger.warning("Last updated game no longer available, fallback to arbitrary save game")
    SELECTED_GAME_NAME = next(iter(AVAILABLE_GAMES))

DEFAULT_PLOT_LAYOUT = go.Layout(
    yaxis=dict(
        type="linear",
    ),
    height=640,
)

dropdown_options = [{'label': f"{country} ({g})", 'value': g} for g, country in AVAILABLE_GAMES.items()]
GAME_SELECTION_DROPDOWN = dcc.Dropdown(id='select-game-dropdown', options=dropdown_options, value=SELECTED_GAME_NAME, )
CATEGORY_TABS = [{'label': category, 'value': category} for category in visualization_data.THEMATICALLY_GROUPED_PLOTS]
CATEGORY_TABS.append({'label': "Leaders", 'value': "Leaders"})
DEFAULT_SELECTED_CATEGORY = next(iter(visualization_data.THEMATICALLY_GROUPED_PLOTS.keys()))

app.layout = html.Div([
    GAME_SELECTION_DROPDOWN,
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


def update_selected_game(new_selected_game):
    global SELECTED_GAME_NAME, AVAILABLE_GAMES
    AVAILABLE_GAMES = get_available_games_dict()
    if new_selected_game != SELECTED_GAME_NAME:
        if new_selected_game not in AVAILABLE_GAMES:
            logger.warning(f"Selected game {new_selected_game} might not exist...")
        logger.info(f"Selected game is {new_selected_game}")
        SELECTED_GAME_NAME = new_selected_game
        visualization_data.get_current_execution_plot_data(SELECTED_GAME_NAME)  # to ensure everything is initialized before the dropdown's callback is handled
        GAME_SELECTION_DROPDOWN.value = new_selected_game


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


@app.callback(Output('tab-content', 'children'), [Input('tabs', 'value'),
                                                  Input('select-game-dropdown', 'value'), ])
def update_content(tab_value, game_id):
    logger.info(f"dash_server.update_content: Tab is {tab_value}, Game is {game_id}")
    if game_id is None:
        game_id = config.get_last_updated_game()
        if game_id is None:
            return []
    update_selected_game(game_id)
    children = [html.H1(f"{AVAILABLE_GAMES[game_id]} ({game_id})")]
    if tab_value in visualization_data.THEMATICALLY_GROUPED_PLOTS:
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
        if tab_value == "Military":
            with models.get_db_session(game_id) as session:
                children += get_war_descriptions(session, get_most_recent_date(session))
    elif tab_value == "Leaders":
        with models.get_db_session(game_id) as session:
            children += get_ruler_descriptions(session, get_most_recent_date(session))

    return children


def get_most_recent_date(session):
    most_recent_gs = session.query(models.GameState).order_by(models.GameState.date.desc()).first()
    if most_recent_gs is None:
        most_recent_date = 0
    else:
        most_recent_date = most_recent_gs.date
    return most_recent_date


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


def get_ruler_descriptions(session, most_recent_date):
    ruler_html_children = []
    rulers = {}
    scientists = {}
    governors = {}
    admirals = {}
    generals = {}
    leader_header = {}
    basic_leader_info = {}
    for leader in session.query(models.Leader).order_by(models.Leader.date_hired).all():
        name = leader.leader_name
        in_game_id = leader.leader_id_in_game
        bday = models.days_to_date(leader.date_born)
        date_hired = models.days_to_date(leader.date_hired)

        status = f"active (as of {models.days_to_date(most_recent_date)})"
        if leader.last_date < most_recent_date - 3600:
            status = f"dismissed or deceased around {models.days_to_date(leader.last_date + random.randint(0, 30))}"
        basic_leader_info[in_game_id, name] = html.Ul([
            html.Li(f"Species:  {leader.species.species_name}"),
            html.Li(f"Born: {bday}"),
            html.Li(f"Hired: {date_hired}"),
            html.Li(f"Status: {status}")
        ])
        leader_class_to_achievement_dict = {
            models.LeaderClass.scientist: scientists,
            models.LeaderClass.governor: governors,
            models.LeaderClass.admiral: admirals,
            models.LeaderClass.general: generals,
            models.LeaderClass.ruler: rulers,
        }
        if leader.leader_class == models.LeaderClass.scientist:
            scientists[in_game_id, name] = []
            leader_header[in_game_id, name] = [html.H3(f"Scientist {name}")]
        elif leader.leader_class == models.LeaderClass.governor:
            governors[in_game_id, name] = []
            leader_header[in_game_id, name] = [html.H3(f"Governor {name}")]
        elif leader.leader_class == models.LeaderClass.admiral:
            admirals[in_game_id, name] = []
            leader_header[in_game_id, name] = [html.H3(f"Admiral {name}")]
        elif leader.leader_class == models.LeaderClass.general:
            generals[in_game_id, name] = []
            leader_header[in_game_id, name] = [html.H3(f"General {name}")]
        elif leader.leader_class == models.LeaderClass.ruler:
            leader_header[in_game_id, name] = [html.H3(f"{name}")]
        for a in leader.achievements:
            start_date = models.days_to_date(a.start_date_days)
            end_date = models.days_to_date(a.end_date_days)
            if a.achievement_type == models.LeaderAchievementType.was_ruler:
                if name not in rulers:
                    rulers[in_game_id, name] = []
                achievement_text = f"{start_date} - {end_date}: Ruled the {a.achievement_description} with agenda \"{leader.leader_agenda}\""
                rulers[in_game_id, name].append(achievement_text)
                if leader.leader_class in leader_class_to_achievement_dict:
                    leader_class_to_achievement_dict[leader.leader_class][in_game_id, name].append(
                        achievement_text
                    )
            elif a.achievement_type == models.LeaderAchievementType.negotiated_peace_treaty:
                achievement_text = f"{end_date}: Negotiated peace in the {a.achievement_description}"
                rulers[in_game_id, name].append(achievement_text)
                if leader.leader_class in leader_class_to_achievement_dict:
                    leader_class_to_achievement_dict[leader.leader_class][in_game_id, name].append(
                        achievement_text
                    )
            elif a.achievement_type == models.LeaderAchievementType.passed_edict:
                achievement_text = f'{end_date}: Passed edict "{a.achievement_description}"'
                rulers[in_game_id, name].append(achievement_text)
                if leader.leader_class in leader_class_to_achievement_dict:
                    leader_class_to_achievement_dict[leader.leader_class][in_game_id, name].append(
                        achievement_text
                    )
            elif a.achievement_type == models.LeaderAchievementType.embraced_tradition:
                tradition = game_info.convert_id_to_name(a.achievement_description, remove_prefix="tr")
                achievement_text = f"{end_date}: Embraced tradition \"{tradition}\""
                rulers[in_game_id, name].append(achievement_text)
                if leader.leader_class in leader_class_to_achievement_dict:
                    leader_class_to_achievement_dict[leader.leader_class][in_game_id, name].append(
                        achievement_text
                    )
            elif a.achievement_type == models.LeaderAchievementType.achieved_ascension:
                perk = game_info.convert_id_to_name(a.achievement_description, remove_prefix="ap")
                achievement_text = f"{end_date}: Ascension: {perk}"
                rulers[in_game_id, name].append(achievement_text)
                if leader.leader_class in leader_class_to_achievement_dict:
                    leader_class_to_achievement_dict[leader.leader_class][in_game_id, name].append(
                        achievement_text
                    )
            elif a.achievement_type == models.LeaderAchievementType.researched_technology:
                perk = game_info.convert_id_to_name(a.achievement_description, remove_prefix="tech")
                scientists[in_game_id, name].append(f"{end_date}: Researched \"{perk}\"")
            elif a.achievement_type == models.LeaderAchievementType.was_faction_leader:
                leader_class_to_achievement_dict[leader.leader_class][in_game_id, name].append(
                    f"{start_date} - {end_date}: Leader of the \"{a.achievement_description}\" faction."
                )

    leaders_by_category = {
        "Rulers": rulers,
        "Scientists": scientists,
        "Governors": governors,
        "Admirals": admirals,
        "Generals": generals,
    }
    for category, leader_dict in leaders_by_category.items():
        if not leader_dict:
            continue
        ruler_html_children.append(html.H2(category))
        for id_name, achievement_list in leader_dict.items():
            ruler_html_children.append(html.H3(leader_header[id_name]))
            ruler_html_children.append("Basic Information:")
            ruler_html_children.append(basic_leader_info[id_name])
            if not achievement_list:
                continue
            ruler_html_children.append("Achievements:")
            ruler_html_children.append(html.Ul([
                html.Li(f"{a}") for a in achievement_list
            ]))
    return ruler_html_children


def get_war_descriptions(session, current_date):
    war_description_children = [html.H2("Wars")]
    for war in session.query(models.War).order_by(models.War.start_date_days).all():
        start = models.days_to_date(war.start_date_days)
        end = models.days_to_date(current_date)
        if war.end_date_days:
            end = models.days_to_date(war.end_date_days)

        war_description_children.append(html.H3(f"{war.name} ({start}  -  {end})"))
        war_description_children.append(html.P(f"Outcome: {war.outcome}"))
        war_description_children.append(html.H4(f"Attackers:"))
        war_description_children.append(html.Ul([
            html.Li(f'{wp.country.country_name}: "{wp.war_goal}" war goal') for wp in war.participants
            if wp.is_attacker
        ]))
        war_description_children.append(html.H4(f"Defenders:"))
        war_description_children.append(html.Ul([
            html.Li(f'{wp.country.country_name}: "{wp.war_goal}" war goal') for wp in war.participants
            if not wp.is_attacker
        ]))

        war_description_children.append(html.H4(f"Combat Log:"))
        victories = sorted([we for wp in war.participants for we in wp.victories], key=lambda we: we.date)
        war_event_list = []
        for vic in victories:
            country_name = vic.war_participant.country.country_name
            if vic.combat_type == models.CombatType.ships:
                war_event_list.append(html.Li(f"{models.days_to_date(vic.date)}: {country_name} fleet combat victory in the {vic.system} system."))
            if vic.combat_type == models.CombatType.armies:
                verb = "defended against" if vic.attacker_victory else "succeeded in"
                war_event_list.append(html.Li(f"{models.days_to_date(vic.date)}: {country_name} {verb} planetary invasion of {vic.planet}."))
        war_description_children.append(html.Ul(war_event_list))

    return war_description_children


def start_server():
    app.run_server(port=config.CONFIG.port)


if __name__ == '__main__':
    start_server()
