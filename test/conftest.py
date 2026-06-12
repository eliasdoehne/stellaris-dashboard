import pytest
from typing import Dict, Any, Optional, Set

from stellarisdashboard import config as dashboard_config
from stellarisdashboard import datamodel
from stellarisdashboard.parsing import timeline


@pytest.fixture(scope="session", autouse=True)
def initialize_config():
    dashboard_config.initialize()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "skip_github_actions: mark tests to only run locally"
    )


class TimelineTestHelper:
    def __init__(self, session, game: datamodel.Game, country: datamodel.Country):
        self.session = session
        self.game = game
        self.country = country

    def add_leader(self, leader_id_in_game: int, leader_class: str) -> datamodel.Leader:
        leader = datamodel.Leader(
            game=self.game,
            country=self.country,
            leader_id_in_game=leader_id_in_game,
            leader_class=leader_class,
            is_active=True
        )
        self.session.add(leader)
        self.session.commit()
        return leader

    def run_processor(
        self,
        processor_class,
        date_in_days: int,
        leaders_dict: Dict[int, Any],
        dependencies: Optional[Dict[str, Any]] = None,
        extra_gamestate: Optional[Dict[str, Any]] = None
    ):
        basic_info = timeline.BasicGameInfo(
            game_id="test_game",
            date_in_days=date_in_days,
            player_country_id=self.country.country_id_in_game,
            other_players=set(),
            number_of_parsed_saves=1
        )
        gamestate_dict = {
            "leaders": leaders_dict
        }
        if extra_gamestate:
            gamestate_dict.update(extra_gamestate)

        if dependencies is None:
            countries = {c.country_id_in_game: c for c in self.session.query(datamodel.Country).all()}
            leaders = {l.leader_id_in_game: l for l in self.session.query(datamodel.Leader).all()}
            dependencies = {
                "country": countries,
                "leader": leaders
            }

        processor = processor_class()
        processor.initialize(
            game=self.game,
            gamestate_dict=gamestate_dict,
            gs=None,
            basic_info=basic_info,
            db_session=self.session,
        )
        processor.extract_data_from_gamestate(dependencies)
        self.session.commit()
        return processor


@pytest.fixture
def timeline_helper(tmp_path):
    dashboard_config.CONFIG.base_output_path = tmp_path
    
    with datamodel.get_db_session("test_game", write=True) as session:
        game = datamodel.Game(game_name="test_game")
        session.add(game)
        session.commit()
        
        country = datamodel.Country(
            game=game,
            country_name="player_empire",
            is_player=True,
            country_id_in_game=1
        )
        session.add(country)
        session.commit()
        
        yield TimelineTestHelper(session, game, country)

