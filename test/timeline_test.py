import pytest
from stellarisdashboard import config, datamodel
from stellarisdashboard.parsing import timeline

def test_official_envoy_event_no_duplicates(tmp_path):
    config.CONFIG.base_output_path = tmp_path
    
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
        
        envoy = datamodel.Leader(
            game=game,
            country=country,
            leader_id_in_game=10,
            leader_class="envoy",
            is_active=True
        )
        official = datamodel.Leader(
            game=game,
            country=country,
            leader_id_in_game=11,
            leader_class="official",
            is_active=True
        )
        session.add_all([envoy, official])
        session.commit()
        
        # 1. First month (day 30) - assignments start
        basic_info_1 = timeline.BasicGameInfo(
            game_id="test_game",
            date_in_days=30,
            player_country_id=1,
            other_players=set(),
            number_of_parsed_saves=1
        )
        
        gamestate_dict_1 = {
            "leaders": {
                10: {
                    "class": "envoy",
                    "location": {
                        "assignment": "galactic_community"
                    }
                },
                11: {
                    "class": "official",
                    "location": {
                        "assignment": "galactic_community"
                    }
                }
            }
        }
        
        dependencies = {
            "country": {1: country},
            "leader": {10: envoy, 11: official}
        }
        
        processor = timeline.EnvoyEventProcessor()
        processor.initialize(
            game=game,
            gamestate_dict=gamestate_dict_1,
            gs=None,
            basic_info=basic_info_1,
            db_session=session,
        )
        processor.extract_data_from_gamestate(dependencies)
        session.commit()
        
        # Verify that both envoy and official got one event created
        events = session.query(datamodel.HistoricalEvent).all()
        assert len(events) == 2
        
        envoy_event = next(e for e in events if e.leader_id == envoy.leader_id)
        official_event = next(e for e in events if e.leader_id == official.leader_id)
        
        assert envoy_event.event_type == datamodel.HistoricalEventType.envoy_community
        assert envoy_event.start_date_days == 30
        assert envoy_event.end_date_days is None
        
        assert official_event.event_type == datamodel.HistoricalEventType.envoy_community
        assert official_event.start_date_days == 30
        assert official_event.end_date_days is None
        
        # 2. Second month (day 60) - same assignments
        basic_info_2 = timeline.BasicGameInfo(
            game_id="test_game",
            date_in_days=60,
            player_country_id=1,
            other_players=set(),
            number_of_parsed_saves=2
        )
        
        processor2 = timeline.EnvoyEventProcessor()
        processor2.initialize(
            game=game,
            gamestate_dict=gamestate_dict_1, # same gamestate dict
            gs=None,
            basic_info=basic_info_2,
            db_session=session,
        )
        processor2.extract_data_from_gamestate(dependencies)
        session.commit()
        
        # Verify that no duplicate events were created, and end_date_days remains None
        events2 = session.query(datamodel.HistoricalEvent).all()
        assert len(events2) == 2
        
        envoy_event = next(e for e in events2 if e.leader_id == envoy.leader_id)
        official_event = next(e for e in events2 if e.leader_id == official.leader_id)
        
        assert envoy_event.start_date_days == 30
        assert envoy_event.end_date_days is None
        assert official_event.start_date_days == 30
        assert official_event.end_date_days is None
        
        # 3. Third month (day 90) - assignment changes for official, envoy becomes idle
        basic_info_3 = timeline.BasicGameInfo(
            game_id="test_game",
            date_in_days=90,
            player_country_id=1,
            other_players=set(),
            number_of_parsed_saves=3
        )
        
        gamestate_dict_3 = {
            "leaders": {
                10: {
                    "class": "envoy",
                    "location": {
                        "assignment": "idle"
                    }
                },
                11: {
                    "class": "official",
                    "location": {
                        "assignment": "federation",
                        "id": 123
                    }
                }
            },
            "federation": {
                123: {
                    "name": "The Galactic Alliance"
                }
            }
        }
        
        processor3 = timeline.EnvoyEventProcessor()
        processor3.initialize(
            game=game,
            gamestate_dict=gamestate_dict_3,
            gs=None,
            basic_info=basic_info_3,
            db_session=session,
        )
        processor3.extract_data_from_gamestate(dependencies)
        session.commit()
        
        # Verify that:
        # - Envoy's old assignment was closed (end_date_days = 89)
        # - Official's old assignment was closed (end_date_days = 89)
        # - Official has a new federation event starting at 90
        # Total events: 3 (1 old envoy event, 1 old official event, 1 new official event)
        all_events = session.query(datamodel.HistoricalEvent).order_by(datamodel.HistoricalEvent.start_date_days).all()
        assert len(all_events) == 3
        
        old_envoy_event = next(e for e in all_events if e.leader_id == envoy.leader_id)
        assert old_envoy_event.end_date_days == 89
        
        official_events = [e for e in all_events if e.leader_id == official.leader_id]
        assert len(official_events) == 2
        
        old_official_event = official_events[0]
        assert old_official_event.event_type == datamodel.HistoricalEventType.envoy_community
        assert old_official_event.start_date_days == 30
        assert old_official_event.end_date_days == 89
        
        new_official_event = official_events[1]
        assert new_official_event.event_type == datamodel.HistoricalEventType.envoy_federation
        assert new_official_event.start_date_days == 90
        assert new_official_event.end_date_days is None
