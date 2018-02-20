import logging
import traceback
from typing import Dict, Any

from stellaristimeline import models

logger = logging.getLogger(__name__)


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
            logger.error(traceback.f)
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

    def _extract_country_info(self, country_id, country_dict):
        is_player = (country_id == self._player_country)
        country = self._session.query(models.Country).filter_by(game=self.game, country_name=country_dict["name"]).one_or_none()
        if country is None:
            country = models.Country(is_player=is_player, game=self.game, country_name=country_dict["name"])
            self._session.add(country)
        has_research_agreement_with_player = is_player or (country_id in self._player_research_agreements)
        has_sensor_link_with_player = is_player or (country_id in self._player_sensor_links)
        if is_player:
            attitude_towards_player = models.Attitude.friendly
        else:
            attitude_towards_player = self._extract_ai_attitude_towards_player(country_dict)

        economy_data = self._extract_economy_data(country_dict)

        country_data = models.CountryData(
            country=country,
            game_state=self._current_gamestate,
            military_power=country_dict["military_power"],
            fleet_size=country_dict["fleet_size"],
            tech_progress=len(country_dict["tech_status"]["technology"]),
            exploration_progress=len(country_dict["surveyed"]),
            owned_planets=len(country_dict["owned_planets"]),
            has_research_agreement_with_player=has_research_agreement_with_player,
            has_sensor_link_with_player=has_sensor_link_with_player,
            attitude_towards_player=attitude_towards_player,
            **economy_data,
        )
        self._session.add(country_data)
        self._extract_factions(country_data)
        return country_data

    def _extract_economy_data(self, country_dict):
        economy = {}
        if "budget" in country_dict and country_dict["budget"] != "none" and country_dict["budget"]:
            last_month = country_dict["budget"]["last_month"]
            budget = last_month["values"]
            economy.update(
                energy_spending_army=budget.get("army_maintenance", 0),
                energy_spending_building=budget.get("buildings", 0),
                energy_spending_pop=budget.get("pops", 0),
                energy_spending_ship=budget.get("ship_maintenance", 0),
                energy_spending_station=budget.get("station_maintenance", 0),
                mineral_spending_pop=last_month.get("pop_mineral_maintenance", 0),
                mineral_spending_ship=last_month.get("ship_mineral_maintenances", 0),
            )
        if "modules" in country_dict and "standard_economy_module" in country_dict["modules"] and "last_month" in country_dict["modules"]["standard_economy_module"]:
            economy_module = country_dict["modules"]["standard_economy_module"]["last_month"]
            default = [0, 0, 0]
            economy.update(
                mineral_production=economy_module.get("minerals", default)[1],
                mineral_spending=economy_module.get("minerals", default)[2],
                energy_production=economy_module.get("energy", default)[1],
                energy_spending=economy_module.get("energy", default)[2],
                food_production=economy_module.get("food", default)[1],
                food_spending=economy_module.get("food", default)[2],
                society_research=economy_module.get("society_research", default)[0],
                physics_research=economy_module.get("physics_research", default)[0],
                engineering_research=economy_module.get("engineering_research", default)[0],
            )
        return economy

    def _extract_ai_attitude_towards_player(self, country_data):
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
        return attitude_towards_player

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

    def _extract_factions(self, country_data: models.CountryData):
        for faction_id, faction_data in self.gamestate_dict.get("pop_factions", {}).items():
            if not faction_data or faction_data == "none":
                continue
            faction_name = faction_data["name"]
            faction_country_name = self.gamestate_dict["country"][faction_data["country"]]["name"]
            if faction_country_name != country_data.country.country_name:
                continue
            # If the faction is in the database, get it, otherwise add a new faction
            faction = self._session.query(models.PoliticalFaction).filter_by(faction_name=faction_name, country=country_data.country).one_or_none()
            if faction is None:
                ethics = models.PopEthics.from_str(faction_data["type"])
                if ethics == models.PopEthics.other:
                    print(f"Found faction with unknown Ethics: {faction_data}")
                faction = models.PoliticalFaction(
                    country=country_data.country,
                    faction_name=faction_name,
                    ethics=ethics,
                )
                self._session.add(faction)
            members = len(faction_data.get("members", []))
            self._session.add(models.FactionSupport(
                faction=faction,
                country_data=country_data,
                members=members,
                support=faction_data.get("support", 0),
                happiness=faction_data.get("happiness", 0),
            ))

    def _extract_country_pop_info(self, country_dict: Dict[str, Any], country_data: models.CountryData):
        species_demographics = {}
        pop_data = self.gamestate_dict["pop"]
        for planet_id in country_dict["owned_planets"]:
            planet_data = self.gamestate_dict["planet"][planet_id]
            for pop_id in planet_data.get("pop", []):
                if pop_id not in pop_data:
                    logging.warning(f"Reference to non-existing pop with id {pop_id} on planet {planet_id}")
                    continue
                pop_species_index = pop_data[pop_id]["species_index"]
                if pop_species_index not in species_demographics:
                    species_demographics[pop_species_index] = 0
                if pop_data[pop_id]["growth_state"] == 1:
                    species_demographics[pop_species_index] += 1

        for pop_species_index, pop_count in species_demographics.items():
            species_name = self.gamestate_dict["species"][pop_species_index]["name"]
            species = self._session.query(models.Species).filter_by(game=self.game, species_name=species_name).one_or_none()
            if species is None:
                species = models.Species(game=self.game, species_name=species_name)
                self._session.add(species)

            pop_count = models.PopCount(
                country_data=country_data,
                species=species,
                pop_count=pop_count,
            )
            self._session.add(pop_count)
