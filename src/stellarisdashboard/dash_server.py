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

from stellarisdashboard import config, models, visualization_data

logger = logging.getLogger(__name__)

flask_app = flask.Flask(__name__)
flask_app.logger.setLevel(logging.DEBUG)
timeline_app = dash.Dash(name="Stellaris Timeline", server=flask_app, compress=False, url_base_pathname="/timeline")
timeline_app.css.config.serve_locally = True
timeline_app.scripts.config.serve_locally = True

VERSION_ID = "v0.1.4"

@flask_app.route("/")
@flask_app.route("/checkversion/<version>/")
def index_page(version=None):
    show_old_version_notice = False
    if version is not None:
        show_old_version_notice = version != VERSION_ID
    games = [dict(country=country, game_name=g) for g, country in models.get_available_games_dict().items()]
    return render_template(
        "index.html",
        games=games,
        show_old_version_notice=show_old_version_notice,
        version=VERSION_ID,
    )


@flask_app.route("/history")
@flask_app.route("/history/<game_name>")
@flask_app.route("/checkversion/<version>/history")
@flask_app.route("/checkversion/<version>/history/<game_name>")
def history_page(game_name=None, version=None):
    show_old_version_notice = False
    if version is not None:
        show_old_version_notice = version != VERSION_ID
    if game_name is None:
        game_name = ""

    matches = models.get_known_games(game_name)
    if not matches:
        logger.warning(f"Could not find a game matching {game_name}")
        return render_template("404_page.html", game_not_found=True, game_name=game_name)
    games_dict = models.get_available_games_dict()
    game_name = matches[0]
    country = games_dict[game_name]
    with models.get_db_session(game_name) as session:
        date = get_most_recent_date(session)
        wars = get_war_dicts(session, date)
        leaders = get_leader_dicts(session, date)
    return render_template(
        "history_page.html",
        game_name=game_name,
        country=country,
        wars=wars,
        leaders=leaders,
        show_old_version_notice=show_old_version_notice,
        version=VERSION_ID,
    )


STELLARIS_DARK_BG_COLOR = 'rgba(33,43,39,1)'
GALAXY_BG_COLOR = 'rgba(0,0,0,1)'
STELLARIS_LIGHT_BG_COLOR = 'rgba(43,59,52,1)'
STELLARIS_FONT_COLOR = 'rgba(217,217,217,1)'
STELLARIS_GOLD_FONT_COLOR = 'rgba(217,217,217,1)'
DEFAULT_PLOT_LAYOUT = go.Layout(
    yaxis=dict(
        type="linear",
    ),
    height=640,
    plot_bgcolor=STELLARIS_LIGHT_BG_COLOR,
    paper_bgcolor=STELLARIS_DARK_BG_COLOR,
    font={'color': STELLARIS_FONT_COLOR},
)

# SOME CSS ATTRIBUTES
BUTTON_STYLE = {
    "color": "rgba(195, 133, 33, 1)",
    "font-family": "verdana",
    "font-size": "20px",
    "-webkit-appearance": "button",
    "-moz-appearance": "button",
    "appearance": "button",
    "background-color": "rgba(43, 59, 52, 1)",
    "display": "inline",
    "text-decoration": "none",
    "padding": "0.1cm",
    "margin": "0.1cm",
}
HEADER_STYLE = {
    "font-family": "verdana",
    "color": "rgba(217, 217, 217, 1)",
    "margin-top": "20px",
    "margin-bottom": "10px",
    "text-align": "center",
}
TEXT_STYLE = {
    "font-family": "verdana",
    "color": "rgba(217, 217, 217, 1)",
}

CATEGORY_TABS = [{'label': category, 'value': category} for category in visualization_data.THEMATICALLY_GROUPED_PLOTS]
CATEGORY_TABS.append({'label': "Galaxy", 'value': "Galaxy"})
DEFAULT_SELECTED_CATEGORY = "Economy"

timeline_app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    html.Div([
        dcc.Tabs(
            tabs=CATEGORY_TABS,
            value=DEFAULT_SELECTED_CATEGORY,
            id='tabs',
        ),
        html.Div(id='tab-content', style={
            'width': '100%',
            'height': '100%',
            'margin-left': 'auto',
            'margin-right': 'auto'
        }),
        dcc.Slider(
            id='dateslider',
            min=0,
            max=100,
            step=0.01,
            value=100,
            # updatemode='drag',
            marks={i: '{}%'.format(i) for i in range(0, 110, 10)},
        ),
    ], style={
        'width': '100%',
        "height": "100%",
        'fontFamily': 'Sans-Serif',
        'margin-left': 'auto',
        'margin-right': 'auto'
    }),
], style={
    "width": "100%",
    "height": "100%",
    "padding": 0,
    "margin": 0,
    "background-color": STELLARIS_DARK_BG_COLOR,
})


def get_figure_layout(plot_spec: visualization_data.PlotSpecification):
    layout = DEFAULT_PLOT_LAYOUT
    # if plot_spec.style == visualization_data.PlotStyle.stacked:
    #     layout["yaxis"] = {}
    if plot_spec.style == visualization_data.PlotStyle.line:
        layout["hovermode"] = "closest"
    else:
        layout["hovermode"] = "compare"
    return go.Layout(**layout)


@timeline_app.callback(Output('tab-content', 'children'),
                       [Input('tabs', 'value'), Input('url', 'search'), Input('dateslider', 'value')])
def update_content(tab_value, search, date_fraction):
    game_id = parse.parse_qs(parse.urlparse(search).query).get("game_name", [None])[0]
    if game_id is None:
        game_id = ""
    matches = models.get_known_games(game_id)
    if not matches:
        logger.warning(f"Could not find a game matching {game_id}")
        return render_template("404_page.html", game_not_found=True, game_name=game_id)

    games_dict = models.get_available_games_dict()
    game_id = matches[0]
    if game_id not in games_dict:
        logger.warning(f"Game ID {game_id} does not match any known game!")
        return []

    logger.info(f"dash_server.update_content: Tab is {tab_value}, Game is {game_id}")
    with models.get_db_session(game_id) as session:
        current_date = get_most_recent_date(session)

    children = [
        html.Div(
            [html.A("Go to Game Selection", id='index-link', href="/", style=BUTTON_STYLE),
             html.A(f'Go to Event Ledger', id='ledger-link', href=flask.url_for("history_page", game_name=game_id), style=BUTTON_STYLE)],
            style={
                "text-align": "center",
            },
        ),
        html.H1(f"{games_dict[game_id]} ({game_id})", style=HEADER_STYLE),
    ]
    if tab_value in visualization_data.THEMATICALLY_GROUPED_PLOTS:
        plots = visualization_data.THEMATICALLY_GROUPED_PLOTS[tab_value]
        for plot_spec in plots:
            figure_data = get_figure_data(game_id, plot_spec)
            figure_layout = get_figure_layout(plot_spec)
            figure = go.Figure(data=figure_data, layout=figure_layout)

            children.append(html.H2(f"{plot_spec.title}", style=HEADER_STYLE))
            children.append(dcc.Graph(
                id=f"{plot_spec.plot_id}",
                figure=figure,
            ))
    else:
        slider_date = 0.01 * date_fraction * current_date
        children.append(get_galaxy(game_id, slider_date))
        children.append(html.P(f"Galactic Records for {models.days_to_date(slider_date)}", style=TEXT_STYLE))
    return children


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
    return plot_list


def _get_line_plot_data(plot_data: visualization_data.EmpireProgressionPlotData, plot_spec: visualization_data.PlotSpecification):
    plot_list = []
    for key, x_values, y_values in plot_data.data_sorted_by_last_value(plot_spec):
        if not any(y_values):
            continue
        line = dict(
            x=x_values,
            y=y_values,
            name=key,
            text=[f"{val:.2f} - {key}" for val in y_values],
            line={"color": get_country_color(key, 0.75)},
        )
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
            line["line"] = {"color": get_country_color(key, 0.75)}
            line["fillcolor"] = get_country_color(key, 0.3)
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
        line["line"] = {"color": get_country_color(key, 1.0)}
        line["fillcolor"] = get_country_color(key, 0.3)
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


def get_galaxy(game_id, date):
    # adapted from https://plot.ly/python/network-graphs/
    galaxy = visualization_data.get_galaxy_data(game_id)
    graph = galaxy.get_graph_for_date(date)
    edge_traces = {}
    for edge in graph.edges:
        country = graph.edges[edge]["country"]
        if country not in edge_traces:
            width = 1 if country == visualization_data.GalaxyMapData.UNCLAIMED else 8
            edge_traces[country] = go.Scatter(
                x=[],
                y=[],
                text=[],
                line=go.Line(width=width, color=get_country_color(country)),
                hoverinfo='text',
                mode='lines',
                showlegend=False,
            )
        x0, y0 = graph.nodes[edge[0]]['pos']
        x1, y1 = graph.nodes[edge[1]]['pos']
        edge_traces[country]['x'] += [x0, x1, None]
        edge_traces[country]['y'] += [y0, y1, None]
        edge_traces[country]['text'].append(country)

    node_traces = {}
    for node in graph.nodes:
        country = graph.nodes[node]["country"]
        if country not in node_traces:
            node_size = 10 if country != visualization_data.GalaxyMapData.UNCLAIMED else 4
            node_traces[country] = go.Scatter(
                x=[], y=[],
                text=[],
                mode='markers',
                hoverinfo='text',
                marker=go.Marker(
                    color=[],
                    size=node_size,
                    line=dict(width=0.5)),
                name=country,
            )
        if country == visualization_data.GalaxyMapData.UNCLAIMED:
            color = "rgba(255,255,255,0.5)"
        else:
            color = get_country_color(country)
        node_traces[country]['marker']['color'].append(color)
        x, y = graph.nodes[node]['pos']
        node_traces[country]['x'].append(x)
        node_traces[country]['y'].append(y)
        country_str = f" ({country})" if country != visualization_data.GalaxyMapData.UNCLAIMED else ""
        node_traces[country]['text'].append(f'{graph.nodes[node]["name"]}{country_str}')

    layout = go.Layout(
        xaxis=go.XAxis(
            showgrid=False,
            zeroline=False,
            showticklabels=False
        ),
        yaxis=go.YAxis(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            scaleratio=0.9,
            scaleanchor='x',
        ),
        margin=dict(
            t=0, b=0, l=0, r=0,
        ),
        legend=dict(
            orientation="v",
            x=1.0,
            y=1.0,
        ),
        height=720,
        hovermode='closest',
        plot_bgcolor=GALAXY_BG_COLOR,
        paper_bgcolor=STELLARIS_LIGHT_BG_COLOR,
        font={'color': STELLARIS_FONT_COLOR},
    )

    return dcc.Graph(
        id="galaxy-map",
        figure=go.Figure(
            data=go.Data(list(edge_traces.values()) + list(node_traces.values())),
            layout=layout,
        ),
    )


def get_leader_dicts(session, most_recent_date):
    rulers = []
    scientists = []
    governors = []
    admirals = []
    generals = []
    assigned_ids = set()
    for leader in session.query(models.Leader).order_by(
            models.Leader.is_active.desc(), models.Leader.date_hired
    ).all():
        base_ruler_id = "_".join(leader.leader_name.split()).lower()
        ruler_id = base_ruler_id
        id_offset = 1
        while ruler_id in assigned_ids:
            id_offset += 1
            ruler_id = f"{base_ruler_id}_{id_offset}"
        species = "Unknown"
        if leader.species is not None:
            species = leader.species.species_name

        status = f"active, as of {models.days_to_date(most_recent_date)}"
        if not leader.is_active:
            random.seed(leader.leader_name)
            last_date = leader.last_date + random.randint(0, 30)
            age = (last_date - leader.date_born) // 360
            status = f"dismissed or died around {models.days_to_date(last_date)} (Age {age})"

        leader_dict = dict(
            name=leader.leader_name,
            id=f"{ruler_id}",
            in_game_id=leader.leader_id_in_game,
            birthday=models.days_to_date(leader.date_born),
            date_hired=models.days_to_date(leader.date_hired),
            status=status,
            species=species,
            achievements=[str(a) for a in leader.achievements]
        )
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

    leaders = (rulers
               + scientists
               + governors
               + admirals
               + generals)
    return leaders


def get_war_dicts(session, current_date):
    wars = []
    for war in session.query(models.War).order_by(models.War.start_date_days).all():
        start = models.days_to_date(war.start_date_days)
        end = models.days_to_date(current_date)
        if war.end_date_days:
            end = models.days_to_date(war.end_date_days)

        if (not any(wp.country.first_player_contact_date < current_date for wp in war.participants)
                and not config.Config.show_everything):
            continue

        attackers = [
            f'{wp.country.country_name}: "{wp.war_goal}" war goal' for wp in war.participants
            if wp.is_attacker
        ]
        defenders = [
            f'{wp.country.country_name}: "{wp.war_goal}" war goal' for wp in war.participants
            if not wp.is_attacker
        ]

        combats = sorted([combat for combat in war.combat], key=lambda combat: combat.date)
        war_id = "_".join(war.name.split()).lower()
        wars.append(dict(
            name=war.name,
            id=war_id,
            start=start,
            end=end,
            attackers=attackers,
            defenders=defenders,
            combat=[
                str(combat) for combat in combats
                if combat.attacker_war_exhaustion + combat.defender_war_exhaustion > 0.01
                   or combat.combat_type == models.CombatType.armies
            ],
        ))

    return wars


def get_country_color(country_name: str, alpha: float = 1.0) -> str:
    alpha = min(alpha, 1)
    alpha = max(alpha, 0)
    r, g, b = visualization_data.get_color_vals(country_name)
    r, g, b = r * 255, g * 255, b * 255
    color = f"rgba({r},{g},{b},{alpha})"
    return color


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


def start_server():
    timeline_app.run_server(port=config.CONFIG.port)


if __name__ == '__main__':
    start_server()
