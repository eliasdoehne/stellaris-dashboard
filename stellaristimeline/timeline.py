import logging
from typing import Dict, Any

from stellaristimeline import models


class TimelineExtractor:
    """ Process data from parsed save file dictionaries and add it to the database. """

    def __init__(self):
        self.gamestate_dict = None
        self.game = None
        self._session = None
        self._player_country = None
        self._current_gamestate = None
        self._player_research_agreements = None
        self._player_sensor_links = None

    def process_gamestate(self, game_name: str, gamestate_dict: Dict[str, Any]):
        self.gamestate_dict = gamestate_dict
        if len(self.gamestate_dict["player"]) != 1:
            logging.warning("Attempted to extract data from multiplayer save!")
            return None
        self._player_country = self.gamestate_dict["player"][0]["country"]
        self._session = models.SessionFactory()
        try:
            self.game = self._session.query(models.Game).filter_by(game_name=game_name).first()
            if self.game is None:
                logging.info(f"Adding new game {game_name} to database.")
                self.game = models.Game(game_name=game_name)
                self._session.add(self.game)
            date_str = gamestate_dict["date"]
            days = models.date_to_days(self.gamestate_dict["date"])
            for gs in reversed(self.game.game_states):
                if days == gs.date:
                    print(f"Gamestate for {self.game.game_name}, date {date_str} exists! Replacing...")
                    self._session.delete(gs)
                    break
                if days > gs.date:
                    break
            logging.info(f"Extracting country data to database.")
            self._process_gamestate()
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise e
        finally:
            self._session.close()
        self._current_gamestate = None
        self.gamestate_dict = None
        self._player_country = None
        self._player_research_agreements = None
        self._player_sensor_links = None
        self._session = None

    def _process_gamestate(self):
        days = models.date_to_days(self.gamestate_dict["date"])
        self._current_gamestate = models.GameState(game=self.game, date=days)
        self._session.add(self._current_gamestate)

        self._extract_player_trade_agreements()
        for country_id, country_data in self.gamestate_dict["country"].items():
            if not isinstance(country_data, dict):
                continue  # can be "none", apparently
            if country_data["type"] != "default":
                continue  # Enclaves, Leviathans, etc ....
            country_state = self._extract_country_info(country_id, country_data)
            self._extract_country_pop_info(country_data, country_state)

    def _extract_country_info(self, country_id, country_data):
        is_player = (country_id == self._player_country)
        if is_player:
            attitude_towards_player = models.Attitude.friendly
        else:
            attitude_towards_player = "unknown"
            ai = country_data.get("ai", {})
            if isinstance(ai, dict):
                attitudes = ai.get("attitude", [])
                for attitude in attitudes:
                    if not isinstance(attitude, dict):
                        continue
                    if attitude["country"] == self._player_country:
                        attitude_towards_player = attitude["attitude"]
                        break
                attitude_towards_player = models.Attitude.__members__.get(attitude_towards_player, models.Attitude.unknown)
        has_research_agreement_with_player = is_player or (country_id in self._player_research_agreements)
        has_sensor_link_with_player = is_player or (country_id in self._player_sensor_links)

        country_state = models.CountryState(
            country_name=country_data["name"],
            game_state=self._current_gamestate,
            military_power=country_data["military_power"],
            fleet_size=country_data["fleet_size"],
            tech_progress=len(country_data["tech_status"]["technology"]),
            exploration_progress=len(country_data["surveyed"]),
            owned_planets=len(country_data["owned_planets"]),
            is_player=is_player,
            has_research_agreement_with_player=has_research_agreement_with_player,
            has_sensor_link_with_player=has_sensor_link_with_player,
            attitude_towards_player=attitude_towards_player,
        )
        self._session.add(country_state)
        return country_state

    def _extract_player_trade_agreements(self):
        self._player_research_agreements = set()
        self._player_sensor_links = set()
        trades = self.gamestate_dict.get("trade_deal", {})
        if not trades:
            return
        for trade_id, trade_deal in trades.items():
            if not isinstance(trade_deal, dict):
                continue
            first = trade_deal.get("first", {})
            second = trade_deal.get("second", {})
            if first.get("country", -1) != self._player_country:
                first, second = second, first  # make it so player is always first party
            if first.get("country", -1) != self._player_country:
                continue  # trade doesn't involve player
            if second.get("research_agreement") == "yes":
                self._player_research_agreements.add(second["country"])
            if second.get("sensor_link") == "yes":
                self._player_sensor_links.add(second["country"])

    def _extract_country_pop_info(self, country_data, country_state):
        demographics = {}
        pop_data = self.gamestate_dict["pop"]
        if country_state.is_player:
            pass
        for planet_id in country_data["owned_planets"]:
            planet_data = self.gamestate_dict["planet"][planet_id]
            for pop_id in planet_data.get("pop", []):
                if pop_id not in pop_data:
                    logging.warning(f"Reference to non-existing pop with id {pop_id} on planet {planet_id}")
                    continue
                pop_species_index = pop_data[pop_id]["species_index"]
                if pop_species_index not in demographics:
                    demographics[pop_species_index] = 0
                if pop_data[pop_id]["growth_state"] == 1:
                    demographics[pop_species_index] += 1
        for pop_species_index, pop_count in demographics.items():
            species_name = self.gamestate_dict["species"][pop_species_index]["name"]
            pop_count = models.PopCount(
                country_state=country_state,
                species_name=species_name,
                pop_count=pop_count,
            )
            self._session.add(pop_count)
