import abc
import collections
import dataclasses
import datetime
import itertools
import json
import logging
import random
import time
from typing import Dict, Any, Set, Iterable, Optional, Union, List, Tuple, Collection

import sqlalchemy

from stellarisdashboard import datamodel, game_info, config
from stellarisdashboard.dashboard_app.visualization_data import clear_cached_country_colors

logger = logging.getLogger(__name__)


def dump_name(name: dict):
    return json.dumps(name, sort_keys=True)


@dataclasses.dataclass
class BasicGameInfo:
    game_id: str
    date_in_days: int
    player_country_id: int
    other_players: Set[int]
    number_of_parsed_saves: int

    @property
    def logger_str(self) -> str:
        return f"{self.game_id} {datamodel.days_to_date(self.date_in_days)}"


class TimelineExtractor:
    def __init__(self):
        self.basic_info: BasicGameInfo = None
        self._session = None
        self._gamestate_dict = None
        self.number_of_parsed_saves = 0
        self._other_players = set()

    def process_gamestate(self, game_id: str, gamestate_dict: Dict[str, Any]):
        self._gamestate_dict = gamestate_dict
        self._read_basic_game_info(game_id)
        logger.info(f"{self.basic_info.logger_str} Processing Gamestate")
        t_start_gs = time.process_time()
        with datamodel.get_db_session(game_id=game_id) as self._session:
            try:
                db_game = self._get_or_add_game_to_db(game_id)
                if self._check_if_gamestate_exists(db_game):
                    logger.info(
                        f"{self.basic_info.logger_str} Gamestate for same date already exists in database. Aborting..."
                    )
                    self._session.rollback()
                    return
                else:
                    self._process_gamestate(db_game)
                logger.info(
                    f"{self.basic_info.logger_str} Processed Gamestate in {time.process_time() - t_start_gs:.3f} s, "
                    f"writing changes to database"
                )
                self._session.commit()
                self.number_of_parsed_saves += 1
            except Exception as e:
                self._session.rollback()
                logger.exception(
                    f"{self.basic_info.logger_str} Rolling back changes to database..."
                )
                if config.CONFIG.debug_mode or isinstance(e, KeyboardInterrupt):
                    raise e

    def _check_if_gamestate_exists(self, db_game):
        existing_dates = {gs.date for gs in db_game.game_states}
        return self.basic_info.date_in_days in existing_dates

    def _process_gamestate(self, db_game):
        db_game_state = datamodel.GameState(
            game=db_game,
            date=self.basic_info.date_in_days,
        )
        self._session.add(db_game_state)
        all_dependencies = {}
        for data_processor in self._data_processors():
            t_start = time.process_time()
            data_processor.initialize(
                db_game,
                self._gamestate_dict,
                db_game_state,
                self.basic_info,
                self._session,
            )

            missing_dependencies = sorted(
                dep
                for dep in data_processor.DEPENDENCIES
                if dep not in all_dependencies
            )
            if missing_dependencies:
                logger.info(
                    f"{self.basic_info.logger_str}   - Could not process {data_processor.ID} due to missing "
                    f"dependencies {', '.join(missing_dependencies)}"
                )
            else:
                logger.info(
                    f"{self.basic_info.logger_str}   - Processing {data_processor.ID}"
                )
                data_processor.extract_data_from_gamestate(
                    {key: all_dependencies[key] for key in data_processor.DEPENDENCIES}
                )
                all_dependencies[data_processor.ID] = data_processor.data()
                self._session.flush()
                logger.info(
                    f"{self.basic_info.logger_str}         done ({time.process_time() - t_start:.3f} s)"
                )

    def _get_or_add_game_to_db(self, game_id: str):
        game = self._session.query(datamodel.Game).filter_by(game_name=game_id).first()
        player_country_id = self.basic_info.player_country_id
        if game is None:
            logger.info(
                f"{self.basic_info.logger_str} Adding new game {game_id} to database."
            )
            if player_country_id is not None:
                player_country_name = dump_name(
                    self._gamestate_dict["country"][player_country_id]["name"]
                )
            else:
                player_country_name = "Observer Mode"
            galaxy_info = self._gamestate_dict["galaxy"]
            game = datamodel.Game(
                game_name=game_id,
                player_country_name=player_country_name,
                db_galaxy_template=galaxy_info.get("template", "Unknown"),
                db_galaxy_shape=galaxy_info.get("shape", "Unknown"),
                db_difficulty=galaxy_info.get("difficulty", "Unknown"),
                db_last_updated=datetime.datetime.now(),
            )

        game.player_country_id = player_country_id
        game.db_last_updated = datetime.datetime.now()
        self._session.add(game)
        return game

    def _read_basic_game_info(self, game_id: str):
        date_str = self._gamestate_dict["date"]
        date_in_days = datamodel.date_to_days(date_str)
        self.basic_info = BasicGameInfo(
            game_id=game_id,
            date_in_days=date_in_days,
            player_country_id=self._identify_player_country(),
            other_players=self._other_players,
            number_of_parsed_saves=self.number_of_parsed_saves,
        )

    def _identify_player_country(self):
        players = self._gamestate_dict.get("player")
        if players:
            if len(players) == 1:
                return players[0]["country"]
            else:
                playercountry = None
                if not config.CONFIG.mp_username:
                    raise ValueError(
                        "Please configure your Multiplayer username in the Dashboard settings for multiplayer games."
                    )
                for player in players:
                    if player["name"] == config.CONFIG.mp_username:
                        playercountry = player["country"]
                    else:
                        self._other_players.add(player["country"])
                if playercountry is None:
                    raise ValueError(
                        f"Could not find player matching Multiplayer username {config.CONFIG.mp_username}"
                    )
                return playercountry
        # observer mode
        return None

    def _data_processors(self) -> Iterable["AbstractGamestateDataProcessor"]:
        yield SystemProcessor()
        yield BypassProcessor()
        yield CountryProcessor()
        yield FleetOwnershipProcessor()
        yield SystemOwnershipProcessor()
        yield DiplomacyDictProcessor()
        yield DiplomaticRelationsProcessor()
        yield SensorLinkProcessor()
        yield CountryDataProcessor()
        yield GalacticMarketProcessor()
        yield InternalMarketProcessor()
        yield SpeciesProcessor()
        yield LeaderProcessor()
        yield PlanetProcessor()
        yield SectorColonyEventProcessor()
        yield PlanetUpdateProcessor()
        yield RulerEventProcessor()
        yield CouncilProcessor()
        yield GovernmentProcessor()
        yield PolicyProcessor()
        yield FactionProcessor()
        yield DiplomacyUpdatesProcessor()
        yield GalacticCommunityProcessor()
        yield ScientistEventProcessor()
        yield EnvoyEventProcessor()
        yield FleetInfoProcessor()
        yield WarProcessor()
        yield TruceProcessor()
        yield PopStatsProcessor()


class AbstractGamestateDataProcessor(abc.ABC):
    ID = "abstract"
    DEPENDENCIES = []

    def __init__(self):
        self._basic_info = None
        self._db_game = None
        self._db_gamestate = None
        self._gamestate_dict = None
        self._session = None

    def initialize(
        self,
        game: datamodel.Game,
        gamestate_dict: Dict[str, Any],
        gs: datamodel.GameState,
        basic_info: BasicGameInfo,
        db_session,
    ):
        self._basic_info = basic_info
        self._db_game = game
        self._db_gamestate = gs
        self._gamestate_dict = gamestate_dict
        self._session = db_session
        self.initialize_data()

    def initialize_data(self):
        pass

    def data(self) -> Any:
        pass

    @abc.abstractmethod
    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        pass

    def _get_or_add_shared_description(self, text: str) -> datamodel.SharedDescription:
        matching_description = (
            self._session.query(datamodel.SharedDescription)
            .filter_by(text=text)
            .one_or_none()
        )
        if matching_description is None:
            matching_description = datamodel.SharedDescription(text=text)
            self._session.add(matching_description)
        return matching_description


class SystemProcessor(AbstractGamestateDataProcessor):
    ID = "systems"
    DEPENDENCIES = []

    def __init__(self):
        super().__init__()
        self.systems_by_ingame_id = None
        self.starbase_systems = None

    def data(self) -> Dict[str, Any]:
        return {
            "systems_by_ingame_id": self.systems_by_ingame_id,
            "starbase_system_map": self.starbase_systems,
        }

    def extract_data_from_gamestate(self, dependencies):
        self.systems_by_ingame_id = {
            s.system_id_in_game: s for s in self._session.query(datamodel.System)
        }
        self.starbase_systems = {}
        for ingame_id, system_data in sorted(
            self._gamestate_dict["galactic_object"].items()
        ):
            if "starbases" in system_data:
                starbases = system_data["starbases"]
                if len(starbases) > 1:
                    logger.debug(f"Found multiple starbases in system {ingame_id}")
                for starbase in starbases:
                    self.starbase_systems[starbase] = ingame_id
            if ingame_id in self.systems_by_ingame_id:
                self._update_system(
                    system_model=self.systems_by_ingame_id[ingame_id],
                    system_data=system_data,
                )
            else:
                system = self._add_system(system_id=ingame_id, system_data=system_data)
                if system is None:
                    logger.info(
                        f"{self._basic_info.logger_str} Could not add or find system with ID {ingame_id} to database."
                    )
                    continue
                self.systems_by_ingame_id[ingame_id] = system

    def _update_system(self, system_model: datamodel.System, system_data: Dict):
        system_name = dump_name(system_data.get("name"))
        if system_name != system_model.name:
            system_model.name = system_name
            self._session.add(system_model)

    def _add_system(
        self, system_id: int, system_data: Dict
    ) -> Optional[datamodel.System]:
        if system_data is None:
            logger.warning(
                f"{self._basic_info.logger_str} Found no data for system with ID {system_id}!"
            )
            return
        system_name = dump_name(system_data.get("name"))
        coordinate_x = system_data.get("coordinate", {}).get("x", 0)
        coordinate_y = system_data.get("coordinate", {}).get("y", 0)
        system_model = datamodel.System(
            game=self._db_game,
            system_id_in_game=system_id,
            star_class=system_data.get("star_class", "Unknown"),
            name=system_name,
            coordinate_x=coordinate_x,
            coordinate_y=coordinate_y,
        )

        for hl_data in system_data.get("hyperlane", []):
            neighbor_id = hl_data.get("to")
            if neighbor_id == system_id:
                continue  # This can happen in Stellaris 2.1
            neighbor_model = (
                self._session.query(datamodel.System)
                .filter_by(system_id_in_game=neighbor_id)
                .one_or_none()
            )
            if neighbor_model is None:
                continue  # assume that the hyperlane will be created when adding the neighbor system to DB later

            self._session.add(
                datamodel.HyperLane(system_one=system_model, system_two=neighbor_model)
            )

        self._session.add(system_model)
        return system_model


class BypassProcessor(AbstractGamestateDataProcessor):
    """Process Wormholes and LGates"""

    ID = "bypass"
    DEPENDENCIES = [SystemProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        systems_dict = dependencies[SystemProcessor.ID]["systems_by_ingame_id"]

        bypasses = self._gamestate_dict.get("bypasses", {})
        if not isinstance(bypasses, dict):
            return

        bypass_systems = {
            sys_id: sys_dict
            for sys_id, sys_dict in self._gamestate_dict["galactic_object"].items()
            if "bypasses" in sys_dict
        }

        # Number of bypasses should be manageable. It would be tricky to update changing networks, so let's delete 'em
        self._session.query(datamodel.Bypass).delete()
        for sys_id, sys_dict in bypass_systems.items():
            if sys_id not in systems_dict:
                continue
            for bypass_id in sys_dict["bypasses"]:
                bypass_dict = bypasses.get(bypass_id)
                if not isinstance(bypass_dict, dict):
                    continue
                bypass_type = bypass_dict.get("type", "unknown")
                connections = bypass_dict.get("connections", [])
                if bypass_type == "lgate":
                    network_id = hash("lgate")
                elif bypass_type == "gateway":
                    network_id = hash(frozenset(connections) | {bypass_id})
                elif bypass_type == "wormhole":
                    network_id = hash(frozenset(connections) | {bypass_id})
                else:
                    continue

                bypass_type_description = self._get_or_add_shared_description(
                    bypass_type
                )
                self._session.add(
                    datamodel.Bypass(
                        system=systems_dict[sys_id],
                        network_id=network_id,
                        db_description=bypass_type_description,
                        is_active=bypass_dict.get("active") != "no",
                    )
                )


class CountryProcessor(AbstractGamestateDataProcessor):
    ID = "country"
    DEPENDENCIES = []

    def __init__(self):
        super().__init__()
        self.countries_by_ingame_id: Dict[int, datamodel.Country] = None

    def initialize_data(self):
        self.countries_by_ingame_id = {}

    def data(self) -> Dict[int, datamodel.Country]:
        return self.countries_by_ingame_id

    def extract_data_from_gamestate(self, dependencies):
        for country_id, country_data_dict in sorted(
            self._gamestate_dict["country"].items()
        ):
            if not isinstance(country_data_dict, dict):
                continue
            country_type = country_data_dict.get("type")
            country_name = dump_name(country_data_dict.get("name", "no name"))
            flag_colors = country_data_dict.get("flag", {}).get("colors", [])
            primary_color = flag_colors[0] if len(flag_colors) >= 1 else "black"
            secondary_color = flag_colors[1] if len(flag_colors) >= 2 else primary_color
            country_model = (
                self._session.query(datamodel.Country)
                .filter_by(game=self._db_game, country_id_in_game=country_id)
                .one_or_none()
            )

            if country_model is None or primary_color != country_model.primary_color or secondary_color != country_model.secondary_color:
                clear_cached_country_colors()

            if country_model is None:
                country_model = datamodel.Country(
                    is_player=(country_id == self._basic_info.player_country_id),
                    country_id_in_game=country_id,
                    is_other_player=country_id in self._basic_info.other_players,
                    game=self._db_game,
                    country_type=country_type,
                    country_name=country_name,
                    primary_color=primary_color,
                    secondary_color=secondary_color,
                )
                if country_id == self._basic_info.player_country_id:
                    country_model.first_player_contact_date = 0
                self._session.add(country_model)
            if (
                country_name != country_model.country_name
                or country_type != country_model.country_type
                or primary_color != country_model.primary_color
                or secondary_color != country_model.secondary_color
            ):
                country_model.country_name = country_name
                country_model.country_type = country_type
                self._session.add(country_model)
            self.countries_by_ingame_id[country_id] = country_model


class FleetOwnershipProcessor(AbstractGamestateDataProcessor):
    ID = "fleet_owner"
    DEPENDENCIES = [CountryProcessor.ID]

    def __init__(self):
        super().__init__()
        self.owner_by_fleet_id: Dict[int, datamodel.Country] = None

    def initialize_data(self):
        self.owner_by_fleet_id = {}

    def data(self) -> Dict[int, Set[datamodel.System]]:
        return self.owner_by_fleet_id

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        for country_id, country_dict in self._gamestate_dict["country"].items():
            country_model = countries_dict.get(country_id)
            if not country_model:
                continue
            fleets_manager = country_dict.get("fleets_manager", {})
            if not isinstance(fleets_manager, dict):
                continue
            owned_fleets = fleets_manager.get("owned_fleets", [])
            if not isinstance(owned_fleets, list):
                continue
            for fleet_dict in owned_fleets:
                fleet_id = fleet_dict.get("fleet")
                self.owner_by_fleet_id[fleet_id] = country_model


class SystemOwnershipProcessor(AbstractGamestateDataProcessor):
    ID = "system_owners"
    DEPENDENCIES = [SystemProcessor.ID, CountryProcessor.ID, FleetOwnershipProcessor.ID]

    def __init__(self):
        super().__init__()
        self.systems_by_owner_country_id: Dict[int, Set[datamodel.System]] = None

    def initialize_data(self):
        self.systems_by_owner_country_id = collections.defaultdict(set)

    def data(self) -> Dict[int, Set[datamodel.System]]:
        return self.systems_by_owner_country_id

    def extract_data_from_gamestate(self, dependencies):
        starbases = self._gamestate_dict.get("starbase_mgr", {}).get("starbases", {})
        if not isinstance(starbases, dict):
            return
        systems_dict = dependencies[SystemProcessor.ID]["systems_by_ingame_id"]
        fleet_owners_dict = dependencies[FleetOwnershipProcessor.ID]
        ship_to_fleet_id_dict = {
            ship_id: ship_dict["fleet"]
            for ship_id, ship_dict in self._gamestate_dict["ships"].items()
            if isinstance(ship_dict, dict)
        }
        starbase_system_map = dependencies[SystemProcessor.ID]["starbase_system_map"]

        starbase_systems = set()

        for starbase_id, starbase_dict in starbases.items():
            if not isinstance(starbase_dict, dict):
                continue
            system_id_in_game = starbase_system_map.get(starbase_id)
            country_model = fleet_owners_dict.get(
                ship_to_fleet_id_dict.get(starbase_dict.get("station"))
            )
            system_model = systems_dict.get(system_id_in_game)

            if country_model is None or system_model is None:
                # This is no longer an unexpected situation because some megastructures count as starbases since Overlord
                logger.debug(
                    f"{self._basic_info.logger_str} Cannot establish ownership for system {system_id_in_game}"
                )
                continue

            starbase_systems.add(system_id_in_game)

            self.systems_by_owner_country_id[country_model.country_id_in_game].add(
                system_model
            )

            if country_model != system_model.country:
                self._update_owner(
                    current_owner=country_model, system_model=system_model
                )

        for system_id, system_model in systems_dict.items():
            if system_id in starbase_systems:
                continue
            if system_model.country is None:
                continue
            self._update_owner(None, system_model)

    def _update_owner(
        self, current_owner: Optional[datamodel.Country], system_model: datamodel.System
    ):
        owner_changed = False
        event_type = None
        target_country = None

        if current_owner is None:
            owner_changed = True
        elif system_model.country is None:
            owner_changed = True
            event_type = datamodel.HistoricalEventType.expanded_to_system
        elif system_model.country != current_owner:
            owner_changed = True
            event_type = datamodel.HistoricalEventType.conquered_system

        if owner_changed:
            system_model.country = current_owner
            self._session.add(system_model)

            ownership = (
                self._session.query(datamodel.SystemOwnership)
                .filter_by(system=system_model)
                .order_by(datamodel.SystemOwnership.end_date_days.desc())
                .first()
            )
            if ownership is not None:
                ownership.end_date_days = self._basic_info.date_in_days - 1
                self._session.add(ownership)

                target_country = ownership.country
                if target_country is not None:
                    is_visible = target_country.has_met_player() or (
                        current_owner is not None and current_owner.has_met_player()
                    )
                    self._session.add(
                        datamodel.HistoricalEvent(
                            event_type=datamodel.HistoricalEventType.lost_system,
                            country=target_country,
                            target_country=current_owner,
                            system=system_model,
                            start_date_days=self._basic_info.date_in_days,
                            event_is_known_to_player=is_visible,
                        )
                    )
            if current_owner is not None:
                self._session.add(
                    datamodel.SystemOwnership(
                        start_date_days=self._basic_info.date_in_days,
                        end_date_days=self._basic_info.date_in_days + 1,
                        country=current_owner,
                        system=system_model,
                    )
                )
                is_visible = current_owner.has_met_player() or (
                    target_country is not None and target_country.has_met_player()
                )
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=event_type,
                        country=current_owner,
                        target_country=target_country,
                        system=system_model,
                        start_date_days=self._basic_info.date_in_days,
                        event_is_known_to_player=is_visible,
                    )
                )


class DiplomacyDictProcessor(AbstractGamestateDataProcessor):
    ID = "diplomacy"
    DEPENDENCIES = [CountryProcessor.ID]

    def __init__(self):
        super().__init__()
        self.diplomacy_dict = None
        self.truce_countries = None

    def initialize_data(self):
        self.diplomacy_dict = {}
        self.truce_countries = collections.defaultdict(set)

    def data(self):
        return dict(diplomacy=self.diplomacy_dict, truce_countries=self.truce_countries)

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        self.diplomacy_dict = {}

        diplo_predicates_by_key = dict(
            rivalries=lambda r: r.get("is_rival") == "yes",
            defensive_pacts=lambda r: r.get("defensive_pact") == "yes",
            federations=lambda r: r.get("alliance") == "yes",
            non_aggression_pacts=lambda r: r.get("non_aggression_pledge") == "yes",
            closed_borders=lambda r: r.get("closed_borders") == "yes",
            communations=lambda r: r.get("communications") == "yes",
            migration_treaties=lambda r: r.get("migration_access") == "yes",
            commercial_pacts=lambda r: r.get("commercial_pact") == "yes",
            neighbors=lambda r: r.get("borders") == "yes",
            research_agreements=lambda r: r.get("research_agreement") == "yes",
            embassies=lambda r: r.get("embassy") == "yes",
        )

        for country_id, country_model in countries_dict.items():
            self.diplomacy_dict[country_id] = dict(
                rivalries=set(),
                defensive_pacts=set(),
                federations=set(),
                non_aggression_pacts=set(),
                closed_borders=set(),
                communations=set(),
                migration_treaties=set(),
                commercial_pacts=set(),
                neighbors=set(),
                research_agreements=set(),
                embassies=set(),
            )
            country_data_dict = self._gamestate_dict["country"][country_id]
            relations_manager = country_data_dict.get("relations_manager", [])

            if not isinstance(relations_manager, dict):
                continue
            relation_list = relations_manager.get("relation", [])
            if not isinstance(relation_list, list):  # if there is only one
                relation_list = [relation_list]
            for relation in relation_list:
                if not isinstance(relation, dict):
                    continue
                target = relation.get("country")

                for key, predicate in diplo_predicates_by_key.items():
                    if predicate(relation):
                        self.diplomacy_dict[country_id][key].add(target)

                if "truce" in relation:
                    self.truce_countries[relation["truce"]].add(country_id)
                    self.truce_countries[relation["truce"]].add(target)


class DiplomaticRelationsProcessor(AbstractGamestateDataProcessor):
    ID = "diplomatic_relations"
    DEPENDENCIES = [CountryProcessor.ID]

    def __init__(self):
        super().__init__()
        self.diplo_relations: Dict[int, Dict[int, datamodel.DiplomaticRelation]] = None

    def data(self):
        return self.diplo_relations

    def extract_data_from_gamestate(self, dependencies):
        countries_dict: Dict[int, datamodel.Country] = dependencies[CountryProcessor.ID]

        self.diplo_relations: Dict[int, Dict[int, datamodel.DiplomaticRelation]] = {}
        all_relations = self._session.query(datamodel.DiplomaticRelation).all()

        for r in all_relations:
            owner_id = r.owner.country_id_in_game
            if owner_id not in self.diplo_relations:
                self.diplo_relations[owner_id] = {}
            target_id = r.target.country_id_in_game
            self.diplo_relations[owner_id][target_id] = r

        for c_id_1, c_model_1 in countries_dict.items():
            if not c_model_1.is_real_country():
                continue
            if c_id_1 not in self.diplo_relations:
                self.diplo_relations[c_id_1] = {}
            for c_id_2, c_model_2 in countries_dict.items():
                if not c_model_2.is_real_country():
                    continue
                elif c_id_1 == c_id_2:
                    continue
                if c_id_2 not in self.diplo_relations[c_id_1]:
                    r = datamodel.DiplomaticRelation(
                        country_id=c_model_1.country_id,
                        target_country_id=c_model_2.country_id,
                    )
                    self.diplo_relations[c_id_1][c_id_2] = r
                    self._session.add(r)


class SensorLinkProcessor(AbstractGamestateDataProcessor):
    ID = "sensor_links"
    DEPENDENCIES = [CountryProcessor.ID]

    def __init__(self):
        super().__init__()
        self.sensor_links = None

    def initialize_data(self):
        self.sensor_links = {}

    def data(self):
        return self.sensor_links

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        self.sensor_links = {country_id: dict() for country_id in countries_dict}
        trades = self._gamestate_dict.get("trade_deal", {})
        if not trades:
            return
        for trade_id, trade_deal in trades.items():
            if not isinstance(trade_deal, dict):
                continue  # could be "none"
            first = trade_deal.get("first", {})
            second = trade_deal.get("second", {})
            self._process_sensor_links(first, second, trade_deal)

    def _process_sensor_links(self, first, second, trade_deal):
        start_date = datamodel.date_to_days(trade_deal.get("date", "2200.01.01"))
        end_date = start_date + 360 * trade_deal.get("length", 0)
        first_country_id = first.get("country")
        second_country_id = second.get("country")
        if (
            first_country_id is not None
            and second_country_id is not None
            and second.get("sensor_link") == "yes"
        ):
            prev_start, prev_end = self.sensor_links[first_country_id].get(
                second_country_id, (float("inf"), -float("inf"))
            )
            self.sensor_links[first_country_id][second_country_id] = (
                min(prev_start, start_date),
                max(prev_end, end_date),
            )


class CountryDataProcessor(AbstractGamestateDataProcessor):
    ID = "country_data"
    DEPENDENCIES = [
        CountryProcessor.ID,
        DiplomacyDictProcessor.ID,
        SensorLinkProcessor.ID,
        SystemOwnershipProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
        self.country_data_dict: Dict[int, datamodel.CountryData] = None

    def initialize_data(self):
        self.country_data_dict = {}

    def data(self):
        return self.country_data_dict

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        sensor_links = dependencies[SensorLinkProcessor.ID]

        diplomacy_dict = dependencies[DiplomacyDictProcessor.ID]["diplomacy"]
        systems_by_country_id = dependencies[SystemOwnershipProcessor.ID]

        for country_id, country_model in countries_dict.items():
            country_data_dict = self._gamestate_dict["country"][country_id]

            has_sensor_link_with_player = (
                country_model.is_player
                or country_id
                in sensor_links.get(self._basic_info.player_country_id, [])
            )
            if country_model.is_player:
                attitude_towards_player = datamodel.Attitude.is_player
            else:
                attitude_towards_player = self._extract_ai_attitude_towards_player(
                    country_id
                )

            has_market_access = "has_market_access" in country_data_dict.get(
                "flags", []
            )

            diplomacy_data = self._get_diplomacy_towards_player(
                diplomacy_dict, country_id
            )

            tech_count = len(
                country_data_dict.get("tech_status", {}).get("technology", [])
            )
            self.country_data_dict[country_id] = country_data = datamodel.CountryData(
                date=self._basic_info.date_in_days,
                country=country_model,
                game_state=self._db_gamestate,
                military_power=country_data_dict.get("military_power", 0),
                tech_power=country_data_dict.get("tech_power", 0),
                fleet_size=country_data_dict.get("fleet_size", 0),
                empire_size=country_data_dict.get("empire_size", 0),
                empire_cohesion=country_data_dict.get("empire_cohesion", 0),
                tech_count=tech_count,
                exploration_progress=len(country_data_dict.get("surveyed", [])),
                owned_planets=len(country_data_dict.get("owned_planets", [])),
                controlled_systems=len(systems_by_country_id.get(country_id, [])),
                victory_rank=country_data_dict.get("victory_rank", 0),
                victory_score=country_data_dict.get("victory_score", 0),
                economy_power=country_data_dict.get("economy_power", 0),
                has_sensor_link_with_player=has_sensor_link_with_player,
                attitude_towards_player=attitude_towards_player,
                has_galactic_market_access=has_market_access,
                # Resource income is calculated below in _extract_country_economy
                net_energy=0.0,
                net_minerals=0.0,
                net_alloys=0.0,
                net_consumer_goods=0.0,
                net_food=0.0,
                net_unity=0.0,
                net_influence=0.0,
                net_physics_research=0.0,
                net_society_research=0.0,
                net_engineering_research=0.0,
                **diplomacy_data,
            )
            self._extract_country_economy(country_data, country_data_dict)

            if country_model.first_player_contact_date is None and diplomacy_data.get(
                "has_communications_with_player"
            ):
                country_model.first_player_contact_date = self._basic_info.date_in_days
                self._session.add(country_model)
            self._session.add(country_data)

    def _get_diplomacy_towards_player(self, diplomacy_dict, country_id):
        new_key_old_key_list = [
            ("has_embassy_with_player", "embassies"),
            ("has_research_agreement_with_player", "research_agreements"),
            ("has_rivalry_with_player", "rivalries"),
            ("has_defensive_pact_with_player", "defensive_pacts"),
            ("has_migration_treaty_with_player", "migration_treaties"),
            ("has_federation_with_player", "federations"),
            ("has_non_aggression_pact_with_player", "non_aggression_pacts"),
            ("has_closed_borders_with_player", "closed_borders"),
            ("has_communications_with_player", "communations"),
            ("has_commercial_pact_with_player", "commercial_pacts"),
            ("is_player_neighbor", "neighbors"),
        ]
        result = {}
        for new, old in new_key_old_key_list:
            result[new] = (
                self._basic_info.player_country_id in diplomacy_dict[country_id][old]
            )
        return result

    def _extract_ai_attitude_towards_player(self, country_id):
        attitude_towards_player = datamodel.Attitude.unknown
        ai = self._gamestate_dict["country"][country_id].get("ai", {})
        if isinstance(ai, dict):
            attitudes = ai.get("attitude", [])
            for attitude in attitudes:
                if not isinstance(attitude, dict):
                    continue
                if attitude.get("country") == self._basic_info.player_country_id:
                    attitude_towards_player = attitude.get("attitude")
                    break
            attitude_towards_player = datamodel.Attitude.__members__.get(
                attitude_towards_player, datamodel.Attitude.unknown
            )
        return attitude_towards_player

    def _check_returned_number(self, item_name, value):
        if not isinstance(value, (float, int)):
            logger.warning(
                f"{self._basic_info.logger_str} {item_name}: Found unexpected type {type(value).__name__} with value {value}."
            )
            if (
                isinstance(value, list)
                and len(value) > 0
                and isinstance(value[0], (float, int))
            ):
                value = value[0]
            else:
                value = 0.0
        return value

    def _extract_country_economy(
        self, country_data: datamodel.CountryData, country_data_dict
    ):
        budget_dict = (
            country_data_dict.get("budget", {})
            .get("current_month", {})
            .get("balance", {})
        )

        country = country_data.country
        for item_name, values in budget_dict.items():
            if item_name == "none":
                continue
            if not values:
                continue
            resources = {}
            for resource in [
                "energy",
                "minerals",
                "alloys",
                "consumer_goods",
                "food",
                "unity",
                "influence",
                "physics_research",
                "society_research",
                "engineering_research",
            ]:
                if resource in values:
                    resources[resource] = self._check_returned_number(
                        item_name, values.get(resource)
                    )
                else:
                    resources[resource] = 0.0

            country_data.net_energy += resources.get("energy")
            country_data.net_minerals += resources.get("minerals")
            country_data.net_alloys += resources.get("alloys")
            country_data.net_consumer_goods += resources.get("consumer_goods")
            country_data.net_food += resources.get("food")
            country_data.net_unity += resources.get("unity")
            country_data.net_influence += resources.get("influence")
            country_data.net_physics_research += resources.get("physics_research")
            country_data.net_society_research += resources.get("society_research")
            country_data.net_engineering_research += resources.get(
                "engineering_research"
            )

            if country.country_id_in_game in self._basic_info.other_players:
                continue
            if country.is_player or config.CONFIG.read_all_countries:
                self._session.add(
                    datamodel.BudgetItem(
                        country_data=country_data,
                        db_budget_item_name=self._get_or_add_shared_description(
                            item_name
                        ),
                        net_energy=resources.get("energy"),
                        net_minerals=resources.get("minerals"),
                        net_food=resources.get("food"),
                        net_alloys=resources.get("alloys"),
                        net_consumer_goods=resources.get("consumer_goods"),
                        net_unity=resources.get("unity"),
                        net_influence=resources.get("influence"),
                        net_volatile_motes=values.get("volatile_motes", 0.0),
                        net_exotic_gases=values.get("exotic_gases", 0.0),
                        net_rare_crystals=values.get("rare_crystals", 0.0),
                        net_living_metal=values.get("living_metal", 0.0),
                        net_zro=values.get("zro", 0.0),
                        net_dark_matter=values.get("dark_matter", 0.0),
                        net_nanites=values.get("nanites", 0.0),
                        net_physics_research=resources.get("physics_research"),
                        net_society_research=resources.get("society_research"),
                        net_engineering_research=resources.get("engineering_research"),
                    )
                )
        self._session.add(country_data)  # update


class SpeciesProcessor(AbstractGamestateDataProcessor):
    ID = "species"
    DEPENDENCIES = []

    def __init__(self):
        super().__init__()

        self._species_by_ingame_id: Dict[int, datamodel.Species] = None
        self._robot_species: Set[int] = None

    def initialize_data(self):
        self._species_by_ingame_id = {}
        self._robot_species = set()

    def data(self):
        return self._species_by_ingame_id, self._robot_species

    def extract_data_from_gamestate(self, dependencies):
        for species_ingame_id, species_dict in sorted(
            self._gamestate_dict.get("species_db", {}).items()
        ):
            species_model = self._get_or_add_species(species_ingame_id, species_dict)
            self._species_by_ingame_id[species_ingame_id] = species_model
            if species_dict.get("class") == "ROBOT":
                self._robot_species.add(species_ingame_id)

    def _get_or_add_species(self, species_id_in_game: int, species_data: Dict):
        species_name = dump_name(species_data.get("name", "Unnamed Species"))
        species = (
            self._session.query(datamodel.Species)
            .filter_by(game=self._db_game, species_id_in_game=species_id_in_game)
            .one_or_none()
        )
        if species is None:
            species = datamodel.Species(
                game=self._db_game,
                species_name=species_name,
                species_class=species_data.get("class", "Unknown Class"),
                species_id_in_game=species_id_in_game,
                parent_species_id_in_game=species_data.get("base", -1),
                home_planet_id=species_data.get("home_planet", -1),
            )
            self._session.add(species)
            traits_dict = species_data.get("traits", {})
            if isinstance(traits_dict, dict):
                trait_list = traits_dict.get("trait", [])
                if not isinstance(trait_list, list):
                    trait_list = [trait_list]
                for trait in trait_list:
                    self._session.add(
                        datamodel.SpeciesTrait(
                            db_name=self._get_or_add_shared_description(trait),
                            species=species,
                        )
                    )
        return species


class LeaderProcessor(AbstractGamestateDataProcessor):
    ID = "leader"
    DEPENDENCIES = [CountryProcessor.ID, SpeciesProcessor.ID]

    def __init__(self):
        super().__init__()
        self.leader_model_by_ingame_id: Dict[int, datamodel.Leader] = None
        self._species_dict: Dict[int, datamodel.Species] = None
        self._random_instance = random.Random()

    def initialize_data(self):
        self.leader_model_by_ingame_id = {}
        self._random_instance.seed(self._basic_info.game_id)

    def data(self):
        return self.leader_model_by_ingame_id

    def extract_data_from_gamestate(self, dependencies):
        countries = dependencies[CountryProcessor.ID]
        self._species_dict, _ = dependencies[SpeciesProcessor.ID]

        db_active_leaders = {}
        db_inactive_leaders = {}
        for leader in self._session.query(datamodel.Leader):
            if leader.is_active:
                db_active_leaders[leader.leader_id_in_game] = leader
            else:
                db_inactive_leaders[leader.leader_id_in_game] = leader

        self._check_known_leaders(db_active_leaders)
        self._check_new_leaders(countries, db_active_leaders)

        self.leader_model_by_ingame_id.update(db_inactive_leaders)

    def _check_known_leaders(self, db_active_leaders: Dict[int, datamodel.Leader]):
        gs_active_leaders = self._gamestate_dict["leaders"]
        for ingame_id, leader in db_active_leaders.items():
            if gs_active_leaders.get(ingame_id, "none") == "none":
                leader.is_active = False
                leader.last_date = self._basic_info.date_in_days
            else:
                leader_dict = gs_active_leaders[ingame_id]
                self._update_leader_attributes(leader=leader, leader_dict=leader_dict)
            if not leader.is_active:
                country_data = leader.country.get_most_recent_data()
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.leader_died,
                        country=leader.country,
                        leader=leader,
                        start_date_days=self._basic_info.date_in_days,
                        event_is_known_to_player=(
                            country_data is not None
                            and country_data.attitude_towards_player.reveals_economy_info()
                        ),
                    )
                )
                self._session.add(leader)

    def _check_new_leaders(
        self,
        countries: Dict[int, datamodel.Country],
        db_active_leaders: Dict[int, datamodel.Leader],
    ):
        gs_leaders = self._gamestate_dict.get("leaders")

        for country_id, country_model in countries.items():
            country_data_dict = self._gamestate_dict["country"].get(country_id, {})
            if not isinstance(country_data_dict, dict):
                logger.error(f"Could not find country with ID {country_id}")
                continue
            owned_leaders = country_data_dict.get("owned_leaders", [])
            if not isinstance(owned_leaders, list):  # if there is only one
                owned_leaders = [owned_leaders]

            for leader_id in owned_leaders:
                leader_dict = gs_leaders.get(leader_id)
                if not isinstance(leader_dict, dict):
                    continue
                leader = db_active_leaders.get(leader_id)
                if leader is None:
                    leader = self._add_new_leader(country_model, leader_id, leader_dict)
                if leader is None:
                    logger.info("Failed to add leader %d, %s", leader_id, leader_dict)
                    continue
                self.leader_model_by_ingame_id[leader_id] = leader

    def _add_new_leader(
        self, country_model: datamodel.Country, leader_id: int, leader_dict: Dict
    ) -> Optional[datamodel.Leader]:
        date_hired = min(
            self._basic_info.date_in_days,
            datamodel.date_to_days(leader_dict.get("date", "10000.01.01")),
            datamodel.date_to_days(leader_dict.get("start", "10000.01.01")),
            datamodel.date_to_days(leader_dict.get("date_added", "10000.01.01")),
        )
        date_born = (
            date_hired
            - 360 * leader_dict.get("age", 0.0)
            + self._random_instance.randint(-15, 15)
        )
        subclass, leader_traits = self._get_leader_traits(leader_dict)
        leader = datamodel.Leader(
            country=country_model,
            leader_id_in_game=leader_id,
            game=self._db_game,
            last_level=leader_dict.get("level", 0),
            date_hired=date_hired,
            date_born=date_born,
            is_active=True,
            subclass=subclass,
            leader_traits=leader_traits,
        )
        self._update_leader_attributes(
            leader=leader, leader_dict=leader_dict
        )  # sets additional attributes
        country_data = country_model.get_most_recent_data()
        event = datamodel.HistoricalEvent(
            event_type=datamodel.HistoricalEventType.leader_recruited,
            country=country_model,
            leader=leader,
            start_date_days=date_hired,
            end_date_days=self._basic_info.date_in_days,
            event_is_known_to_player=country_data is not None
            and country_data.attitude_towards_player.reveals_economy_info(),
        )
        self._session.add(event)
        return leader

    def get_leader_name(self, leader_dict):
        name_dict = leader_dict.get("name", {})
        # Look for "first_name" then "full_names" (3.6+)
        first_name = dump_name(
            name_dict.get("first_name", name_dict.get("full_names", "Unknown Leader"))
        )
        last_name = dump_name(name_dict.get("second_name", ""))
        return first_name, last_name

    def _update_leader_attributes(self, leader: datamodel.Leader, leader_dict):
        if "pre_ruler_class" in leader_dict:
            leader_class = leader_dict.get("pre_ruler_class", "unknown class")
        else:
            leader_class = leader_dict.get("class", "unknown class")
        leader_gender = leader_dict.get("gender", "other")
        first_name, second_name = self.get_leader_name(leader_dict)
        level = leader_dict.get("level", -1)
        species_id = leader_dict.get("species", -1)
        leader_species = self._species_dict.get(species_id)
        subclass, leader_traits = self._get_leader_traits(leader_dict)
        if leader_species is None:
            logger.warning(
                f"{self._basic_info.logger_str} Invalid species ID {species_id} for leader {leader_dict}"
            )
        if (
            leader.first_name != first_name
            or leader.second_name != second_name
            or leader.leader_class != leader_class
            or leader.subclass != subclass
            or leader.gender != leader_gender
            or leader.species != leader_species
            or leader.leader_traits != leader_traits
        ):
            hist_event_kwargs = dict(
                country=leader.country,
                start_date_days=self._basic_info.date_in_days,
                leader=leader,
                event_is_known_to_player=leader.country.is_player,
            )
            if leader.last_level != level:
                self._session.add(
                    datamodel.HistoricalEvent(
                        **hist_event_kwargs,
                        event_type=datamodel.HistoricalEventType.level_up,
                        db_description=self._get_or_add_shared_description(str(level)),
                    )
                )
            if leader.leader_traits != leader_traits:
                gained_traits, lost_traits = self._get_gained_lost_traits(
                    old_traits=leader.leader_traits, new_traits=leader_traits
                )
                for event_type, trait in itertools.chain(
                    zip(
                        itertools.repeat(datamodel.HistoricalEventType.lost_trait),
                        lost_traits,
                    ),
                    zip(
                        itertools.repeat(datamodel.HistoricalEventType.gained_trait),
                        gained_traits,
                    ),
                ):
                    self._session.add(
                        datamodel.HistoricalEvent(
                            **hist_event_kwargs,
                            event_type=event_type,
                            db_description=self._get_or_add_shared_description(trait),
                        )
                    )

            leader.last_level = level
            leader.first_name = first_name
            leader.second_name = second_name
            leader.leader_class = leader_class
            leader.subclass = subclass
            leader.gender = leader_gender
            leader.species = leader_species
            leader.leader_traits = leader_traits
            self._session.add(leader)

    def _get_leader_traits(self, leader_dict) -> (str, str):
        leader_traits = leader_dict.get("traits", [])
        if not isinstance(leader_traits, list):
            leader_traits = [leader_traits]
        subclass = ""
        for trait in leader_traits:
            if trait.startswith("subclass"):
                subclass = trait
                break

        traits = "|".join(
            t for t in sorted(leader_traits) if not t.startswith("subclass")
        )
        return subclass, traits

    def _get_gained_lost_traits(self, old_traits: str, new_traits: str):
        old_traits = set(old_traits.split("|"))
        new_traits = set(new_traits.split("|"))
        lost_traits = old_traits - new_traits
        gained_traits = new_traits - old_traits

        def strip_level(t: str) -> str:
            return t.rstrip("_0123456789")

        # Only consider a trait "lost" if it is not replaced by a direct upgrade, e.g.
        # trait_ruler_charismatic -> trait_ruler_charismatic_2 is not considered a lost trait
        lost_traits = {
            t
            for t in lost_traits
            if all(strip_level(nt) != strip_level(t) for nt in new_traits)
        }

        return gained_traits, lost_traits


class PlanetProcessor(AbstractGamestateDataProcessor):
    ID = "planet_models"
    DEPENDENCIES = [SystemProcessor.ID]

    def __init__(self):
        super().__init__()
        self.planets_by_ingame_id = None
        self._systems_dict = None
        self._countries_dict = None
        self._leaders_dict = None

    def data(self) -> Any:
        return self.planets_by_ingame_id

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        self.planets_by_ingame_id = {
            p.planet_id_in_game: p for p in self._session.query(datamodel.Planet)
        }
        systems_by_id = dependencies[SystemProcessor.ID]["systems_by_ingame_id"]

        for system_id, system_dict in sorted(
            self._gamestate_dict["galactic_object"].items()
        ):
            planets = system_dict.get("planet", [])
            if isinstance(planets, int):
                planets = [planets]
            for ingame_id in planets:
                planet_dict = self._gamestate_dict["planets"]["planet"].get(ingame_id)
                if not isinstance(planet_dict, dict):
                    continue

                if ingame_id not in self.planets_by_ingame_id:
                    system_model = systems_by_id.get(system_id)
                    self.planets_by_ingame_id[ingame_id] = self._add_planet_model(
                        planet_dict=planet_dict,
                        planet_id=ingame_id,
                        system_model=system_model,
                    )

                planet_model = self.planets_by_ingame_id[ingame_id]
                was_updated = False
                was_updated |= self._update_countable_planet_attributes(
                    planet_model=planet_model,
                    entity_attribute_name="districts",
                    entity_hash_attribute="districts_hash",
                    current_entities=planet_dict.get("district", []),
                    db_entity_factory=datamodel.PlanetDistrict,
                )
                was_updated |= self._update_countable_planet_attributes(
                    planet_model=planet_model,
                    entity_attribute_name="buildings",
                    entity_hash_attribute="buildings_hash",
                    current_entities=self._get_buildings(planet_dict),
                    db_entity_factory=datamodel.PlanetBuilding,
                )
                was_updated |= self._update_countable_planet_attributes(
                    planet_model=planet_model,
                    entity_attribute_name="deposits",
                    entity_hash_attribute="deposits_hash",
                    current_entities=self._get_deposits(planet_dict),
                    db_entity_factory=datamodel.PlanetDeposit,
                )
                was_updated |= self._update_planet_modifiers(
                    planet_model=planet_model,
                    planet_dict=planet_dict,
                )
                if was_updated:
                    self._session.add(planet_model)

    def _get_buildings(self, planet_dict):
        building_ids = planet_dict.get("buildings", {})
        buildings_dict = self._gamestate_dict.get("buildings", {})

        buildings = []
        for b_id in building_ids:
            building = buildings_dict.get(b_id, "Unknown building")
            if not isinstance(building, dict):
                continue
            buildings.append(building.get("type", "Unknown type"))
        return buildings

    def _add_planet_model(
        self, system_model: datamodel.System, planet_id: int, planet_dict: Dict
    ) -> datamodel.Planet:
        planet_class = planet_dict.get("planet_class")
        planet_name = dump_name(planet_dict.get("name"))
        colonize_date = planet_dict.get("colonize_date")
        if colonize_date:
            colonize_date = datamodel.date_to_days(colonize_date)
        planet_model = datamodel.Planet(
            planet_name=planet_name,
            planet_id_in_game=planet_id,
            system=system_model,
            planet_class=planet_class,
            colonized_date=colonize_date,
        )
        self._session.add(planet_model)
        return planet_model

    def _update_countable_planet_attributes(
        self,
        planet_model: datamodel.Planet,
        entity_attribute_name: str,
        entity_hash_attribute: str,
        current_entities: Union[str, List[str]],
        db_entity_factory,
    ) -> bool:
        """
        Update any of the planets' entities that are represented by counting a single value,
        e.g. districts can be represented by storing how often "district_generator" occurs in
        the planet dictionary.

        :param planet_model: datamodel.Planet instance
        :param entity_attribute_name: Name of the attribute which should be updated
        :param entity_hash_attribute:
        :param current_entities: List containing the individual instances of the entity,
                                 e.g. ['district_generator', 'district_generator', 'district_city']
        :param db_entity_factory:
        :return: True, if the planet model was updated, False otherwise
        """
        if isinstance(current_entities, str):
            current_entities = [current_entities]
        if not isinstance(current_entities, list):
            logger.warning(
                f"{self._basic_info.logger_str}: Expected str or list, got {current_entities} while updating "
                f"{entity_attribute_name}"
            )
            return False

        current_entity_counter = collections.Counter(current_entities)

        entities_changed = self._check_and_update_hash(
            planet_model, current_entity_counter, entity_hash_attribute
        )
        if not entities_changed:
            return False

        entities = getattr(planet_model, entity_attribute_name)
        for entity_model in entities:
            text = entity_model.db_description.text
            if current_entity_counter[text] != entity_model.count:
                entity_model.count = current_entity_counter[text]
                self._session.add(entity_model)
            del current_entity_counter[text]

        for entity, count in current_entity_counter.items():
            db_description = self._get_or_add_shared_description(entity)
            self._session.add(
                db_entity_factory(
                    db_description=db_description,
                    planet=planet_model,
                    count=count,
                )
            )
        return True

    def _update_planet_modifiers(
        self, planet_model: datamodel.Planet, planet_dict: Dict
    ) -> bool:
        """Modifiers are represented differently, hence a new function."""
        current_modifiers = dict(_all_planetary_modifiers(planet_dict))

        modifiers_changed = self._check_and_update_hash(
            planet_model, current_modifiers, "modifiers_hash"
        )
        if not modifiers_changed:
            return False

        for db_modifier in planet_model.modifiers:
            assert isinstance(db_modifier, datamodel.PlanetModifier)
            expiration = db_modifier.expiry_date
            modifier_text = db_modifier.db_description.text
            if modifier_text not in current_modifiers:
                self._session.delete(db_modifier)
                self._session.refresh(planet_model)
            else:
                if expiration != current_modifiers[modifier_text]:
                    db_modifier.expiry_date = expiration
                    self._session.add(db_modifier)
                del current_modifiers[modifier_text]

        for modifier, expiration in current_modifiers.items():
            db_description = self._get_or_add_shared_description(modifier)
            self._session.add(
                datamodel.PlanetModifier(
                    planet=planet_model,
                    expiry_date=expiration,
                    db_description=db_description,
                )
            )
        return True

    def _get_deposits(self, planet_dict):
        result = []
        game_deposits = self._gamestate_dict.get("deposit", {})
        for deposit in planet_dict.get("deposits", []):
            d_dict = game_deposits.get(deposit)
            if isinstance(d_dict, dict) and "type" in d_dict:
                result.append(d_dict.get("type", "deposit_unknown"))
        return result

    def _check_and_update_hash(
        self, planet_model: datamodel.Planet, entity_dict, hash_attribute: str
    ) -> bool:
        current_hash = hash(frozenset(entity_dict.items()))
        if current_hash == getattr(planet_model, hash_attribute):
            return False

        setattr(planet_model, hash_attribute, current_hash)
        self._session.add(planet_model)
        return True


class SectorColonyEventProcessor(AbstractGamestateDataProcessor):
    ID = "sectors_colonies"
    DEPENDENCIES = [
        SystemProcessor.ID,
        SystemOwnershipProcessor.ID,
        PlanetProcessor.ID,
        CountryProcessor.ID,
        LeaderProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
        self._planets_dict = None
        self._systems_dict = None
        self._countries_dict = None
        self._leaders_dict = None
        self._systems_by_owner = None

    def extract_data_from_gamestate(self, dependencies):
        self._planets_dict = dependencies[PlanetProcessor.ID]
        self._countries_dict = dependencies[CountryProcessor.ID]
        self._systems_dict = dependencies[SystemProcessor.ID]["systems_by_ingame_id"]
        self._leaders_dict = dependencies[LeaderProcessor.ID]
        self._systems_by_owner = dependencies[SystemOwnershipProcessor.ID]

        sectors_dict = self._gamestate_dict.get("sectors")
        if not isinstance(sectors_dict, dict):
            return

        for country_id, country_model in self._countries_dict.items():
            country_dict = self._gamestate_dict["country"][country_id]
            country_sector_dict = country_dict.get("sectors")
            if not isinstance(country_sector_dict, dict):
                continue
            country_sectors = country_sector_dict.get("owned", [])
            unprocessed_systems = set(
                s.system_id_in_game for s in self._systems_by_owner.get(country_id, [])
            )

            # processing all colonies by sector allows reading the responsible sector governor
            for sector_id in country_sectors:
                sector_info = sectors_dict.get(sector_id)
                if not isinstance(sector_info, dict):
                    continue
                sector_description = self._get_or_add_shared_description(
                    text=dump_name(sector_info.get("name", "Unnamed"))
                )
                governor_model = self._leaders_dict.get(sector_info.get("governor"))

                for system_id in sector_info.get("systems", []):
                    self._history_add_planetary_events_within_sector(
                        country_model, system_id, governor_model
                    )
                    if system_id in unprocessed_systems:
                        unprocessed_systems.remove(system_id)

                sector_capital = self._planets_dict.get(
                    sector_info.get("local_capital")
                )
                if governor_model is not None and sector_capital is not None:
                    self._history_add_or_update_governor_sector_events(
                        country_model,
                        sector_capital,
                        governor_model,
                        sector_description,
                    )

            for system_id in unprocessed_systems:
                self._history_add_planetary_events_within_sector(
                    country_model, system_id, None
                )

    def _history_add_planetary_events_within_sector(
        self,
        country_model: datamodel.Country,
        system_id: int,
        governor: Optional[datamodel.Leader],
    ):
        system_model = self._systems_dict.get(system_id)
        if system_model is None:
            logger.info(
                f"{self._basic_info.logger_str}     Could not find system with in-game id {system_id}"
            )
            return

        system_dict = self._gamestate_dict["galactic_object"].get(system_id, {})

        planets = system_dict.get("planet", [])
        if not isinstance(planets, list):
            planets = [planets]
        for planet_id in planets:
            planet_dict = self._gamestate_dict["planets"]["planet"].get(planet_id)
            if not isinstance(planet_dict, dict):
                continue

            planet_class = planet_dict.get("planet_class")
            is_colonizable = (
                game_info.is_colonizable_planet(planet_class)
                or "colonize_date" in planet_dict
            )
            is_destroyed = game_info.is_destroyed_planet(planet_class)
            is_terraformable = is_colonizable or any(
                m == "terraforming_candidate"
                for (m, _) in _all_planetary_modifiers(planet_dict)
            )

            planet_model = self._planets_dict.get(planet_id)
            if planet_model is None:
                continue
            if is_colonizable:
                self._history_add_or_update_colonization_events(
                    country_model, system_model, planet_model, planet_dict, governor
                )
            if is_terraformable:
                self._history_add_or_update_terraforming_events(
                    country_model, system_model, planet_model, planet_dict, governor
                )
            if is_destroyed and planet_class != planet_model.planet_class:
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.planet_destroyed,
                        country=country_model,
                        start_date_days=self._basic_info.date_in_days,
                        planet=planet_model,
                        system=system_model,
                        event_is_known_to_player=country_model.has_met_player(),
                    )
                )

    def _history_add_or_update_colonization_events(
        self,
        country_model: datamodel.Country,
        system_model: datamodel.System,
        planet_model: datamodel.Planet,
        planet_dict,
        governor: datamodel.Leader,
    ):
        if planet_dict.get("is_under_colonization") == "yes":
            # Colonization still in progress
            colonization_completed = False
        elif "colonize_date" in planet_dict:
            # colonization is finished
            colonization_completed = True
        else:
            # not colonized at all
            return

        colonization_end_date = planet_dict.get("colonize_date")
        if (
            not isinstance(colonization_end_date, str)
            or colonization_end_date == "none"
        ):
            end_date_days = self._basic_info.date_in_days
        else:
            end_date_days = datamodel.date_to_days(colonization_end_date)

        if planet_model.colonized_date is not None:
            # abort early if the planet is already added and known to be fully colonized
            return
        elif colonization_completed:
            # set the planet's colonization flag and allow updating the event one last time
            planet_model.colonized_date = colonization_end_date
            self._session.add(planet_model)
        event = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(
                event_type=datamodel.HistoricalEventType.colonization,
                planet=planet_model,
            )
            .one_or_none()
        )
        if event is None:
            event = datamodel.HistoricalEvent(
                event_type=datamodel.HistoricalEventType.colonization,
                leader=governor,
                country=country_model,
                start_date_days=min(self._basic_info.date_in_days, end_date_days),
                end_date_days=end_date_days,
                planet=planet_model,
                system=system_model,
                event_is_known_to_player=country_model.has_met_player(),
            )
        else:
            event.end_date_days = end_date_days
        self._session.add(event)

    def _history_add_or_update_terraforming_events(
        self,
        country_model: datamodel.Country,
        system_model: datamodel.System,
        planet_model: datamodel.Planet,
        planet_dict,
        governor: datamodel.Leader,
    ):
        terraform_dict = planet_dict.get("terraform_process")
        if not isinstance(terraform_dict, dict):
            return

        current_pc = planet_dict.get("planet_class")
        target_pc = terraform_dict.get("planet_class")
        if not isinstance(target_pc, str):
            logger.info(
                f"{self._basic_info.logger_str} Unexpected target planet class for terraforming of "
                f"{planet_model.planet_name}: From {planet_model.planet_class} to {target_pc}"  # TODO RENDER?
            )
            return
        text = f"{current_pc},{target_pc}"
        matching_description = self._get_or_add_shared_description(text)
        matching_event = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(
                event_type=datamodel.HistoricalEventType.terraforming,
                db_description=matching_description,
                system=system_model,
                planet=planet_model,
            )
            .order_by(datamodel.HistoricalEvent.start_date_days.desc())
            .first()
        )
        if (
            matching_event is None
            or matching_event.end_date_days < self._basic_info.date_in_days - 5 * 360
        ):
            matching_event = datamodel.HistoricalEvent(
                event_type=datamodel.HistoricalEventType.terraforming,
                country=country_model,
                system=planet_model.system,
                planet=planet_model,
                leader=governor,
                start_date_days=self._basic_info.date_in_days,
                end_date_days=self._basic_info.date_in_days,
                db_description=matching_description,
                event_is_known_to_player=country_model.has_met_player(),
            )
        else:
            matching_event.end_date_days = self._basic_info.date_in_days - 1
        self._session.add(matching_event)

    def _history_add_or_update_governor_sector_events(
        self,
        country_model,
        sector_capital: datamodel.Planet,
        governor: datamodel.Leader,
        sector_description: datamodel.SharedDescription,
    ):
        # check if governor was ruling same sector before => update date and return
        event = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(
                event_type=datamodel.HistoricalEventType.governed_sector,
                db_description=sector_description,
            )
            .order_by(datamodel.HistoricalEvent.end_date_days.desc())
            .first()
        )
        if (
            event is not None
            and event.leader == governor
            and event.end_date_days > self._basic_info.date_in_days - 5 * 360
        ):  # if the governor ruled this sector less than 5 years ago, re-use the event...
            event.end_date_days = self._basic_info.date_in_days - 1
        else:
            country_data = country_model.get_most_recent_data()
            event = datamodel.HistoricalEvent(
                event_type=datamodel.HistoricalEventType.governed_sector,
                leader=governor,
                country=country_model,
                db_description=sector_description,
                start_date_days=self._basic_info.date_in_days,
                end_date_days=self._basic_info.date_in_days,
                event_is_known_to_player=country_data is not None
                and country_data.attitude_towards_player.reveals_economy_info(),
            )

        if event.planet is None and sector_capital is not None:
            event.planet = sector_capital
            event.system = sector_capital.system
        self._session.add(event)


class PlanetUpdateProcessor(AbstractGamestateDataProcessor):
    ID = "planet_updates"
    DEPENDENCIES = [PlanetProcessor.ID, SectorColonyEventProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        planet_models = dependencies[PlanetProcessor.ID]
        for ingame_id, planet_model in planet_models.items():
            planet_dict = self._gamestate_dict["planets"]["planet"].get(ingame_id, {})
            if not isinstance(planet_dict, dict):
                continue
            self._update_planet_model(planet_dict, planet_model)

    def _update_planet_model(self, planet_dict: Dict, planet_model: datamodel.Planet):
        planet_class = planet_dict.get("planet_class")
        planet_name = dump_name(planet_dict.get("name"))
        if planet_model.planet_name != planet_name:
            planet_model.planet_name = planet_name
            self._session.add(planet_model)
        if planet_model.planet_class != planet_class:
            planet_model.planet_class = planet_class
            self._session.add(planet_model)


class RulerEventProcessor(AbstractGamestateDataProcessor):
    ID = "ruler"
    DEPENDENCIES = [
        CountryProcessor.ID,
        LeaderProcessor.ID,
        PlanetProcessor.ID,
        PlanetProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
        self.ruler_by_country_id: Dict[int, datamodel.Leader] = None
        self._planet_by_ingame_id: Dict[int, datamodel.Planet] = None

    def initialize_data(self):
        self.ruler_by_country_id = {}

    def data(self) -> Dict[int, Optional[datamodel.Leader]]:
        return self.ruler_by_country_id

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        leader_by_ingame_id = dependencies.get(LeaderProcessor.ID)
        self._planet_by_ingame_id = dependencies.get(PlanetProcessor.ID)

        for country_id, country_model in countries_dict.items():
            country_dict = self._gamestate_dict["country"][country_id]
            if not isinstance(country_dict, dict):
                return None
            ruler_id = country_dict.get("ruler")
            if ruler_id is None and country_model.is_real_country():
                logger.info(
                    f"{self._basic_info.logger_str}         Country {country_dict['name']} has no ruler ID"
                )
            ruler = leader_by_ingame_id.get(ruler_id)

            self.ruler_by_country_id[country_id] = ruler
            if ruler is not None:
                capital_planet = self._history_add_or_update_capital(
                    country_model, ruler, country_dict
                )
                self._update_ruler(ruler, country_model, capital_planet)
            self._extract_tradition_events(ruler, country_model, country_dict)
            self._extract_ascension_events(ruler, country_model, country_dict)
            self._extract_edict_events(ruler, country_model, country_dict)

    def _history_add_or_update_capital(
        self, country_model: datamodel.Country, ruler: datamodel.Leader, country_dict
    ) -> Optional[datamodel.Planet]:
        capital_id = country_dict.get("capital")
        if not isinstance(capital_id, int):
            return
        capital = self._planet_by_ingame_id.get(capital_id)
        if capital != country_model.capital:
            country_model.capital = capital
            self._session.add(country_model)
            if capital is not None:
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.capital_relocation,
                        country=country_model,
                        leader=ruler,
                        start_date_days=self._basic_info.date_in_days,
                        planet=capital,
                        system=capital.system,
                        event_is_known_to_player=country_model.has_met_player(),
                    )
                )

    def _update_ruler(
        self,
        current_ruler: datamodel.Leader,
        country_model: datamodel.Country,
        capital_planet: datamodel.Planet,
    ):
        previous_ruler = country_model.ruler
        if current_ruler == previous_ruler:
            return  # no change

        country_model.ruler = current_ruler
        self._session.add(country_model)

        if previous_ruler is not None:
            previous_ruler_event = (
                self._session.query(datamodel.HistoricalEvent)
                .filter_by(
                    event_type=datamodel.HistoricalEventType.ruled_empire,
                    country=country_model,
                    leader=previous_ruler,
                )
                .order_by(datamodel.HistoricalEvent.start_date_days.desc())
                .first()
            )
            if previous_ruler_event is not None:
                previous_ruler_event.end_date_days = self._basic_info.date_in_days - 1
                previous_ruler_event.is_known_to_player = country_model.has_met_player()
                self._session.add(previous_ruler_event)
        if current_ruler is not None:
            new_ruler_event = datamodel.HistoricalEvent(
                event_type=datamodel.HistoricalEventType.ruled_empire,
                country=country_model,
                leader=current_ruler,
                start_date_days=self._basic_info.date_in_days,
                planet=capital_planet,
                system=capital_planet.system if capital_planet else None,
                event_is_known_to_player=country_model.has_met_player(),
            )
            self._session.add(new_ruler_event)

    def _extract_tradition_events(
        self, ruler: datamodel.Leader, country_model: datamodel.Country, country_dict
    ):
        known_traditions = {t.db_description.text for t in country_model.traditions}
        for tradition in country_dict.get("traditions", []):
            if tradition not in known_traditions:
                matching_description = self._get_or_add_shared_description(
                    text=tradition
                )
                self._session.add(
                    datamodel.Tradition(
                        country=country_model,
                        db_description=matching_description,
                    )
                )
                country_data = country_model.get_most_recent_data()
                self._session.add(
                    datamodel.HistoricalEvent(
                        leader=ruler,
                        country=country_model,
                        event_type=datamodel.HistoricalEventType.tradition,
                        start_date_days=self._basic_info.date_in_days,
                        db_description=matching_description,
                        event_is_known_to_player=country_data is not None
                        and country_data.attitude_towards_player.reveals_economy_info(),
                    )
                )

    def _extract_ascension_events(
        self, ruler: datamodel.Leader, country_model: datamodel.Country, country_dict
    ):
        known_aps = {t.db_description.text for t in country_model.ascension_perks}
        for ascension_perk in country_dict.get("ascension_perks", []):
            if ascension_perk not in known_aps:
                matching_description = self._get_or_add_shared_description(
                    text=ascension_perk
                )
                self._session.add(
                    datamodel.AscensionPerk(
                        country=country_model,
                        db_description=matching_description,
                    )
                )
                self._session.add(
                    datamodel.HistoricalEvent(
                        leader=ruler,
                        country=country_model,
                        event_type=datamodel.HistoricalEventType.ascension_perk,
                        start_date_days=self._basic_info.date_in_days,
                        db_description=matching_description,
                        event_is_known_to_player=country_model.has_met_player(),
                    )
                )

    def _extract_edict_events(
        self, ruler: datamodel.Leader, country_model: datamodel.Country, country_dict
    ):
        edict_list = country_dict.get("edicts", [])
        if not isinstance(edict_list, list):
            edict_list = [edict_list]
        for edict in edict_list:
            if not isinstance(edict, dict):
                continue
            expiry_date = edict.get("date")
            if (
                not expiry_date
                or expiry_date == "1.01.01"
                or edict.get("perpetual") == "yes"
            ):
                expiry_date = None
            else:
                expiry_date = datamodel.date_to_days(expiry_date)
            description = self._get_or_add_shared_description(text=edict.get("edict"))
            matching_event = (
                self._session.query(datamodel.HistoricalEvent)
                .filter_by(
                    event_type=datamodel.HistoricalEventType.edict,
                    country=country_model,
                    db_description=description,
                    end_date_days=expiry_date,
                )
                .one_or_none()
            )
            if matching_event is None:
                country_data = country_model.get_most_recent_data()
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.edict,
                        country=country_model,
                        leader=ruler,
                        db_description=description,
                        start_date_days=self._basic_info.date_in_days,
                        end_date_days=expiry_date,
                        event_is_known_to_player=country_data is not None
                        and country_data.attitude_towards_player.reveals_economy_info(),
                    )
                )


class CouncilProcessor(AbstractGamestateDataProcessor):
    ID = "council"
    DEPENDENCIES = [RulerEventProcessor.ID, LeaderProcessor.ID, CountryProcessor.ID]

    def __init__(self):
        super().__init__()
        self.planets_by_ingame_id = None

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries_by_id = dependencies[CountryProcessor.ID]
        leaders_by_id = dependencies[LeaderProcessor.ID]
        rulers_by_id = dependencies[RulerEventProcessor.ID]

        self._update_council_positions(countries_by_id, leaders_by_id)
        self._update_council_agenda(countries_by_id, rulers_by_id)

    def _update_council_positions(self, countries_by_id, leaders_by_id):
        if "council_positions" not in self._gamestate_dict:
          return
        for cp_id, council_position in sorted(
            self._gamestate_dict["council_positions"]["council_positions"].items()
        ):
            if not isinstance(council_position, dict):
                continue
            country_model = countries_by_id.get(council_position.get("country"))
            leader_model = leaders_by_id.get(council_position.get("leader"))
            councilor_type = council_position.get("type", "unknown councilor type")
            if not all([country_model, leader_model, councilor_type]):
                logger.debug(f"No councilor assigned: %s", council_position)
                continue
            desc = self._get_or_add_shared_description(councilor_type)

            previous_event = (
                self._session.query(datamodel.HistoricalEvent)
                .filter_by(
                    event_type=datamodel.HistoricalEventType.councilor,
                    country=country_model,
                    db_description=desc,
                )
                .order_by(datamodel.HistoricalEvent.start_date_days.desc())
                .first()
            )
            add_new_event = True
            if previous_event is not None:
                previous_event.event_is_known_to_player = country_model.has_met_player()
                if previous_event.leader_id != leader_model.leader_id:
                    previous_event.end_date_days = self._basic_info.date_in_days - 1
                else:
                    add_new_event = False
                self._session.add(previous_event)

            if add_new_event:
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.councilor,
                        country=country_model,
                        leader=leader_model,
                        db_description=desc,
                        start_date_days=self._basic_info.date_in_days,
                        event_is_known_to_player=country_model.has_met_player(),
                    )
                )

    def _update_council_agenda(self, countries_by_id, rulers_by_id):
        for country_id, country_model in countries_by_id.items():
            gov_dict = self._gamestate_dict["country"][country_id].get("government")
            if not isinstance(gov_dict, dict):
                continue
            ruler = rulers_by_id.get(country_id)

            # 'agenda_finding_the_voice'
            in_progress_agenda = gov_dict.get("council_agenda")

            # [{'council_agenda': 'agenda_gestalt_boost_scientist', 'start_date': '2349.01.01'}]
            agenda_cooldowns = {
                a.get("council_agenda"): a
                for a in gov_dict.get("council_agenda_cooldowns", [])
                if isinstance(a, dict) and "council_agenda" in a
            }

            unresolved_db_agenda = (
                self._session.query(datamodel.CouncilAgenda)
                .filter_by(country=country_model, is_resolved=False)
                .one_or_none()
            )
            dbagenda = (
                unresolved_db_agenda
                if unresolved_db_agenda is None
                else unresolved_db_agenda.db_name.text
            )
            last_prep_event: Optional[datamodel.HistoricalEvent] = (
                self._session.query(datamodel.HistoricalEvent)
                .filter_by(
                    country=country_model,
                    event_type=datamodel.HistoricalEventType.agenda_preparation,
                )
                .order_by(datamodel.HistoricalEvent.start_date_days.desc())
                .first()
            )
            if unresolved_db_agenda is None:
                if in_progress_agenda is not None:
                    self._session.add(
                        datamodel.CouncilAgenda(
                            country=country_model,
                            start_date=self._basic_info.date_in_days,
                            is_resolved=False,
                            db_name=self._get_or_add_shared_description(
                                in_progress_agenda
                            ),
                        )
                    )
                    self._session.add(
                        datamodel.HistoricalEvent(
                            event_type=datamodel.HistoricalEventType.agenda_preparation,
                            country=country_model,
                            leader=ruler,
                            db_description=self._get_or_add_shared_description(
                                in_progress_agenda
                            ),
                            start_date_days=self._basic_info.date_in_days,
                            event_is_known_to_player=country_model.has_met_player(),
                        )
                    )
            else:
                a_key = unresolved_db_agenda.db_name.text
                if a_key in agenda_cooldowns:
                    cooldown_date = datamodel.date_to_days(
                        agenda_cooldowns[a_key]["start_date"]
                    )
                    unresolved_db_agenda.cooldown_date = cooldown_date
                    unresolved_db_agenda.launch_date = self._basic_info.date_in_days
                    unresolved_db_agenda.is_resolved = True
                    self._session.add(unresolved_db_agenda)

                    if last_prep_event is not None:
                        last_prep_event.end_date_days = self._basic_info.date_in_days
                        self._session.add(last_prep_event)
                    self._session.add(
                        datamodel.HistoricalEvent(
                            event_type=datamodel.HistoricalEventType.agenda_launch,
                            country=country_model,
                            leader=ruler,
                            db_description=self._get_or_add_shared_description(a_key),
                            start_date_days=self._basic_info.date_in_days,
                            event_is_known_to_player=country_model.has_met_player(),
                        )
                    )
                elif a_key != in_progress_agenda:
                    # agenda changed without launch
                    unresolved_db_agenda.is_resolved = True
                    self._session.add(unresolved_db_agenda)
                    if last_prep_event is not None:
                        last_prep_event.end_date_days = self._basic_info.date_in_days
                        self._session.add(last_prep_event)
                    if in_progress_agenda is not None:
                        self._session.add(
                            datamodel.CouncilAgenda(
                                country=country_model,
                                start_date=self._basic_info.date_in_days,
                                is_resolved=False,
                                db_name=self._get_or_add_shared_description(
                                    in_progress_agenda
                                ),
                            )
                        )
                        self._session.add(
                            datamodel.HistoricalEvent(
                                event_type=datamodel.HistoricalEventType.agenda_preparation,
                                country=country_model,
                                leader=ruler,
                                db_description=self._get_or_add_shared_description(
                                    in_progress_agenda
                                ),
                                start_date_days=self._basic_info.date_in_days,
                                event_is_known_to_player=country_model.has_met_player(),
                            )
                        )


class GovernmentProcessor(AbstractGamestateDataProcessor):
    ID = "government"
    DEPENDENCIES = [CountryProcessor.ID, RulerEventProcessor.ID]

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        rulers_dict = dependencies[RulerEventProcessor.ID]

        for country_id, country_model in countries_dict.items():
            country_dict = self._gamestate_dict["country"][country_id]
            gov_name = dump_name(country_dict.get("name", "Unnamed Country"))
            ethics_list = country_dict.get("ethos", {}).get("ethic", [])
            if not isinstance(ethics_list, list):
                ethics_list = [ethics_list]
            ethics = set(ethics_list)

            gov_dict = country_dict.get("government", {})
            civics_list = gov_dict.get("civics", [])
            if not isinstance(civics_list, list):
                civics_list = [civics_list]
            civics = set(civics_list)
            authority = gov_dict.get("authority", "other")
            gov_type = gov_dict.get("type", "other")
            gov_was_reformed = False

            prev_gov = (
                self._session.query(datamodel.Government)
                .filter(
                    datamodel.Government.start_date_days
                    <= self._basic_info.date_in_days,
                )
                .filter_by(country=country_model)
                .order_by(datamodel.Government.start_date_days.desc())
                .first()
            )

            if prev_gov is not None:
                prev_gov.end_date_days = self._basic_info.date_in_days - 1
                self._session.add(prev_gov)
                previous_ethics = [
                    prev_gov.ethics_1,
                    prev_gov.ethics_2,
                    prev_gov.ethics_3,
                    prev_gov.ethics_4,
                    prev_gov.ethics_5,
                ]
                previous_ethics = set(previous_ethics) - {None}
                previous_civics = [
                    prev_gov.civic_1,
                    prev_gov.civic_2,
                    prev_gov.civic_3,
                    prev_gov.civic_4,
                    prev_gov.civic_5,
                ]
                previous_civics = set(previous_civics) - {None}
                gov_was_reformed = (
                    (ethics != previous_ethics)
                    or (civics != previous_civics)
                    or (gov_name != prev_gov.gov_name)
                    or (gov_type != prev_gov.gov_type)
                )
                # nothing has changed...
                if not gov_was_reformed:
                    continue

            ethics = dict(zip([f"ethics_{i}" for i in range(1, 6)], sorted(ethics)))
            civics = dict(zip([f"civic_{i}" for i in range(1, 6)], sorted(civics)))

            gov = datamodel.Government(
                country=country_model,
                start_date_days=self._basic_info.date_in_days - 1,
                end_date_days=self._basic_info.date_in_days + 1,
                gov_name=gov_name,
                gov_type=gov_type,
                authority=authority,
                personality=country_dict.get("personality", "unknown_personality"),
                **ethics,
                **civics,
            )
            self._session.add(gov)
            if gov_was_reformed:
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.government_reform,
                        country=country_model,
                        leader=rulers_dict.get(country_id),
                        start_date_days=self._basic_info.date_in_days,
                        end_date_days=self._basic_info.date_in_days,
                        event_is_known_to_player=country_model.has_met_player(),
                    )
                )


class PolicyProcessor(AbstractGamestateDataProcessor):
    ID = "policy"
    DEPENDENCIES = [CountryProcessor.ID, RulerEventProcessor.ID]

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        rulers_dict = dependencies[RulerEventProcessor.ID]

        for country_id, country_model in countries_dict.items():
            current_stance_per_policy = self._get_current_policies(country_id)

            previous_policy_by_name = self._load_previous_policies(country_model)

            for policy_name, (
                current_selected,
                date,
            ) in current_stance_per_policy.items():
                policy_date_days = (
                    datamodel.date_to_days(date)
                    if date
                    else self._basic_info.date_in_days
                )
                add_new_policy = False
                event_type = None
                event_description = None
                if policy_name not in previous_policy_by_name:
                    add_new_policy = True
                    event_type = datamodel.HistoricalEventType.new_policy
                    event_description = f"{policy_name}|{current_selected}"
                else:
                    previous_policy = previous_policy_by_name[policy_name]
                    previous_selected = previous_policy.selected.text
                    if previous_selected != current_selected:
                        add_new_policy = True
                        previous_policy.is_active = False
                        self._session.add(previous_policy)
                        event_type = datamodel.HistoricalEventType.changed_policy
                        event_description = (
                            f"{policy_name}|{previous_selected}|{current_selected}"
                        )
                if add_new_policy:
                    self._session.add(
                        datamodel.Policy(
                            country_model=country_model,
                            policy_date=policy_date_days,
                            is_active=True,
                            policy_name=self._get_or_add_shared_description(
                                policy_name
                            ),
                            selected=self._get_or_add_shared_description(
                                current_selected
                            ),
                        )
                    )
                if event_type and event_description:
                    self._session.add(
                        datamodel.HistoricalEvent(
                            event_type=event_type,
                            country=country_model,
                            leader=rulers_dict.get(country_id),
                            start_date_days=policy_date_days,
                            db_description=self._get_or_add_shared_description(
                                event_description
                            ),
                        )
                    )

    def _get_current_policies(self, country_id) -> dict[str, (str, str)]:
        country_gs_dict = self._gamestate_dict["country"][country_id]
        current_policies = country_gs_dict.get("active_policies")
        if not isinstance(current_policies, list):
            current_policies = []
        current_stance_per_policy = {
            p.get("policy"): (p.get("selected"), p.get("date"))
            for p in current_policies
        }
        return current_stance_per_policy

    def _load_previous_policies(self, country_model):
        previous_policy_by_name: dict[str, datamodel.Policy] = {
            p.policy_name.text: p
            for p in self._session.query(datamodel.Policy)
            .filter_by(
                country_model=country_model,
                is_active=True,
            )
            .all()
        }
        return previous_policy_by_name


class FactionProcessor(AbstractGamestateDataProcessor):
    ID = "faction"
    DEPENDENCIES = [CountryProcessor.ID, LeaderProcessor.ID]

    # Some constants to represent special pseudo-factions, to categorize pops that are unaffiliated for some reason
    NO_FACTION = "No faction"
    SLAVE_FACTION_NAME = "No faction (enslaved)"
    PURGE_FACTION_NAME = "No faction (purge)"
    NON_SENTIENT_ROBOT_FACTION_NAME = "No faction (non-sentient robot)"
    NO_FACTION_ID = -1
    SLAVE_FACTION_ID = -2
    PURGE_FACTION_ID = -3
    NON_SENTIENT_ROBOT_FACTION_ID = -4

    NO_FACTION_POP_ETHICS = {
        NO_FACTION: "no ethics",
        SLAVE_FACTION_NAME: "no ethics (enslaved)",
        PURGE_FACTION_NAME: "no ethics (purge)",
        NON_SENTIENT_ROBOT_FACTION_NAME: "no ethics (robot)",
    }

    NO_FACTION_ID_MAP = {
        NO_FACTION: NO_FACTION_ID,
        SLAVE_FACTION_NAME: SLAVE_FACTION_ID,
        PURGE_FACTION_NAME: PURGE_FACTION_ID,
        NON_SENTIENT_ROBOT_FACTION_NAME: NON_SENTIENT_ROBOT_FACTION_ID,
    }

    def __init__(self):
        super().__init__()
        self.faction_by_ingame_id: Dict[int, datamodel.Leader] = None
        self._leaders_dict = None

    def initialize_data(self):
        self.faction_by_ingame_id = {}

    def data(self):
        return self.faction_by_ingame_id

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        self._leaders_dict = dependencies[LeaderProcessor.ID]

        for faction_id, faction_dict in sorted(
            self._gamestate_dict.get("pop_factions", {}).items()
        ):
            if not faction_dict or not isinstance(faction_dict, dict):
                continue
            country_model = countries_dict.get(faction_dict.get("country"))
            if country_model is None:
                continue
            faction_name = dump_name(faction_dict.get("name", "Unnamed faction"))
            # If the faction is in the database, get it, otherwise add a new faction
            faction_model = self._get_or_add_faction(
                faction_id_in_game=faction_id,
                faction_name=faction_name,
                country_model=country_model,
                faction_type=faction_dict.get("type"),
            )
            self.faction_by_ingame_id[faction_id] = faction_model
            self._history_add_or_update_faction_leader_event(
                country_model, faction_model, faction_dict
            )

        for country_id, country_model in countries_dict.items():
            for faction_name, faction_id in self.NO_FACTION_ID_MAP.items():
                self.faction_by_ingame_id[faction_id] = self._get_or_add_faction(
                    faction_id_in_game=faction_id,
                    faction_name=faction_name,
                    country_model=country_model,
                    faction_type=FactionProcessor.NO_FACTION_POP_ETHICS[faction_name],
                )

    def _get_or_add_faction(
        self,
        faction_id_in_game: int,
        faction_name: str,
        country_model: datamodel.Country,
        faction_type: str,
    ):
        faction = (
            self._session.query(datamodel.PoliticalFaction)
            .filter_by(faction_id_in_game=faction_id_in_game, country=country_model)
            .one_or_none()
        )
        if faction is None:
            faction = datamodel.PoliticalFaction(
                country=country_model,
                faction_name=faction_name,
                faction_id_in_game=faction_id_in_game,
                db_faction_type=self._get_or_add_shared_description(faction_type),
            )
            self._session.add(faction)
            if faction_id_in_game not in FactionProcessor.NO_FACTION_ID_MAP.values():
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.new_faction,
                        country=country_model,
                        faction=faction,
                        start_date_days=self._basic_info.date_in_days,
                        end_date_days=self._basic_info.date_in_days,
                        event_is_known_to_player=country_model.has_met_player(),
                    )
                )
        return faction

    def _history_add_or_update_faction_leader_event(
        self,
        country_model: datamodel.Country,
        faction_model: datamodel.PoliticalFaction,
        faction_dict,
    ):
        faction_leader_id = faction_dict.get("leader", -1)
        leader = self._leaders_dict.get(faction_leader_id)
        if leader is None:
            logger.debug(
                f"{self._basic_info.logger_str}     Could not find faction leader matching leader id "
                f"{faction_leader_id} for {country_model.country_name}"
            )
            logger.debug(f"{self._basic_info.logger_str}     {faction_dict}")
            return
        matching_event = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(
                country=country_model,
                leader=leader,
                event_type=datamodel.HistoricalEventType.faction_leader,
                faction=faction_model,
            )
            .one_or_none()
        )
        is_known = country_model.has_met_player()
        if matching_event is None:
            matching_event = datamodel.HistoricalEvent(
                country=country_model,
                leader=leader,
                event_type=datamodel.HistoricalEventType.faction_leader,
                faction=faction_model,
                start_date_days=self._basic_info.date_in_days,
                end_date_days=self._basic_info.date_in_days,
                event_is_known_to_player=is_known,
            )
        else:
            matching_event.is_known_to_player = is_known
            matching_event.end_date_days = self._basic_info.date_in_days - 1
        self._session.add(matching_event)


class DiplomacyUpdatesProcessor(AbstractGamestateDataProcessor):
    ID = "diplomacy_events"
    DEPENDENCIES = [
        DiplomacyDictProcessor.ID,
        DiplomaticRelationsProcessor.ID,
        CountryProcessor.ID,
        RulerEventProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
        self._diplo_dict = None
        self._country_dict = None
        self._ruler_dict = None
        self._outgoing_relations = None

    def extract_data_from_gamestate(self, dependencies):
        self._diplo_dict = dependencies[DiplomacyDictProcessor.ID]["diplomacy"]
        self._country_dict = dependencies[CountryProcessor.ID]
        self._ruler_dict = dependencies[RulerEventProcessor.ID]
        self._outgoing_relations = dependencies[DiplomaticRelationsProcessor.ID]

        diplo_relations = [
            (
                datamodel.HistoricalEventType.sent_rivalry,
                datamodel.HistoricalEventType.received_rivalry,
                "rivalries",
            ),
            (
                datamodel.HistoricalEventType.closed_borders,
                datamodel.HistoricalEventType.received_closed_borders,
                "closed_borders",
            ),
            (datamodel.HistoricalEventType.defensive_pact, None, "defensive_pacts"),
            (datamodel.HistoricalEventType.formed_federation, None, "federations"),
            (
                datamodel.HistoricalEventType.non_aggression_pact,
                None,
                "non_aggression_pacts",
            ),
            (datamodel.HistoricalEventType.first_contact, None, "communations"),
            (datamodel.HistoricalEventType.commercial_pact, None, "commercial_pacts"),
            (
                datamodel.HistoricalEventType.research_agreement,
                None,
                "research_agreements",
            ),
            (
                datamodel.HistoricalEventType.migration_treaty,
                None,
                "migration_treaties",
            ),
            (datamodel.HistoricalEventType.embassy, None, "embassies"),
        ]

        for country_id, country_model in self._country_dict.items():
            if not country_model.is_real_country():
                continue

            for target_country_id, relation in self._outgoing_relations[
                country_id
            ].items():
                target_country_model = self._country_dict.get(target_country_id)
                if (
                    target_country_model is None
                    or not target_country_model.is_real_country()
                ):
                    continue

                is_known_to_player = (
                    country_model.has_met_player()
                    and target_country_model.has_met_player()
                )
                for event_type, reverse_event_type, diplo_dict_key in diplo_relations:
                    country_tuples = [
                        (
                            event_type,
                            country_model,
                            target_country_model,
                            self._ruler_dict.get(country_id),
                        ),
                    ]
                    if reverse_event_type is not None:
                        country_tuples.append(
                            (
                                reverse_event_type,
                                target_country_model,
                                country_model,
                                self._ruler_dict.get(target_country_id),
                            )
                        )

                    is_now_active = (
                        target_country_id
                        in self._diplo_dict[country_id][diplo_dict_key]
                    )
                    was_active = relation.is_active(diplo_dict_key)

                    if is_now_active == was_active:  # no change
                        continue
                    else:  # update the relation
                        relation.toggle(diplo_dict_key)

                    if is_now_active:  # Create new historical event entry
                        for (et, c_model, tc_model, c_ruler) in country_tuples:
                            matching_event = datamodel.HistoricalEvent(
                                event_type=et,
                                country=c_model,
                                target_country=tc_model,
                                leader=c_ruler,
                                start_date_days=self._basic_info.date_in_days,
                                event_is_known_to_player=is_known_to_player,
                                db_description=self._get_event_description(
                                    et, c_model, tc_model
                                ),
                            )
                            self._session.add(matching_event)
                    elif was_active:  # Set end date of existing historical event entry
                        for (et, c_model, tc_model, _) in country_tuples:
                            matching_event = self._query_event(
                                event_type=et, country=c_model, target_country=tc_model
                            )
                            if matching_event is not None:
                                matching_event.end_date_days = (
                                    self._basic_info.date_in_days - 1
                                )
                                matching_event.event_is_known_to_player = (
                                    is_known_to_player
                                )
                                self._session.add(matching_event)
                            else:
                                logger.warning(
                                    f"Could not find event matching {relation}, {diplo_dict_key}"
                                )

    def _get_event_description(self, event_type, country_model, target_country_model):
        if country_model is None or target_country_model is None:
            return None
        if event_type == datamodel.HistoricalEventType.formed_federation:
            federations = self._gamestate_dict.get("federation", {})
            if not isinstance(federations, dict):
                return None
            for f_id, fed_dict in federations.items():
                if not isinstance(fed_dict, dict):
                    continue
                members = fed_dict.get("members", [])
                if not isinstance(members, list):
                    continue
                if (
                    country_model.country_id_in_game in members
                    and target_country_model.country_id_in_game in members
                ):
                    return self._get_or_add_shared_description(
                        dump_name(fed_dict.get("name", "Unnamed Federation"))
                    )
        return None

    def _query_event(
        self,
        event_type: datamodel.HistoricalEventType,
        country: datamodel.Country,
        target_country: datamodel.Country,
    ) -> Optional[datamodel.HistoricalEvent]:
        return (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(
                event_type=event_type,
                country=country,
                target_country=target_country,
            )
            .order_by(datamodel.HistoricalEvent.start_date_days.desc())
            .first()
        )


class GalacticMarketProcessor(AbstractGamestateDataProcessor):
    ID = "galactic_market"
    DEPENDENCIES = []

    def extract_data_from_gamestate(self, dependencies):
        market = self._gamestate_dict.get("market", {})
        resource_list = market.get("galactic_market_resources", [])
        fluctuations = market.get("fluctuations", [])
        bought_by_country = market.get("resources_bought", {}).get("amount")
        sold_by_country = market.get("resources_sold", {}).get("amount")

        if not all(
            [
                market,
                resource_list,
                fluctuations,
                bought_by_country,
                sold_by_country,
            ]
        ):
            logger.info(
                f"{self._basic_info.logger_str}         Missing or invalid Galactic Market data, skipping..."
            )
            return

        total_bought = [
            sum(bought[i] for bought in bought_by_country)
            for i in range(len(resource_list))
        ]
        total_sold = [
            sum(sold[i] for sold in sold_by_country) for i in range(len(resource_list))
        ]
        for i, (availability, fluctuation, bought, sold) in enumerate(
            zip(resource_list, fluctuations, total_bought, total_sold)
        ):
            self._session.add(
                datamodel.GalacticMarketResource(
                    game_state=self._db_gamestate,
                    resource_index=i,
                    availability=availability,
                    fluctuation=fluctuation,
                    resources_bought=bought,
                    resources_sold=sold,
                )
            )


class InternalMarketProcessor(AbstractGamestateDataProcessor):
    ID = "internal_market"
    DEPENDENCIES = [CountryDataProcessor.ID]

    def extract_data_from_gamestate(self, dependencies):
        country_data_dict = dependencies[CountryDataProcessor.ID]

        market = self._gamestate_dict.get("market", {})
        fluctuation_dict = market.get("internal_market_fluctuations", {})
        market_countries = fluctuation_dict.get("country", [])
        if isinstance(market_countries, int):
            market_countries = [market_countries]
        fluctuation_resources = fluctuation_dict.get("resources", [])

        if not all(
            [
                fluctuation_dict,
                market_countries,
                fluctuation_resources,
                self._basic_info.player_country_id in market_countries,
            ]
        ):
            logger.info(
                f"{self._basic_info.logger_str}         Missing or invalid Internal Market data, skipping..."
            )
            return

        for country_id, product_fluctuation in zip(
            market_countries, fluctuation_resources
        ):
            if (
                not isinstance(product_fluctuation, dict)
                or country_id not in country_data_dict
            ):
                continue
            if (
                not config.CONFIG.read_all_countries
                and country_id != self._basic_info.player_country_id
            ):
                continue
            for name, value in product_fluctuation.items():
                self._session.add(
                    datamodel.InternalMarketResource(
                        resource_name=self._get_or_add_shared_description(name),
                        country_data=country_data_dict[country_id],
                        fluctuation=value,
                    )
                )


class GalacticCommunityProcessor(AbstractGamestateDataProcessor):
    ID = "galactic_community"
    DEPENDENCIES = [CountryProcessor.ID, RulerEventProcessor.ID]

    def __init__(self):
        super().__init__()
        self._ruler_dict = None
        self._countries_dict = None

    def extract_data_from_gamestate(self, dependencies):
        self._ruler_dict = dependencies[RulerEventProcessor.ID]
        self._countries_dict = dependencies[CountryProcessor.ID]

        community_dict = self._gamestate_dict.get("galactic_community")
        if not isinstance(community_dict, dict):
            return
        self._update_community_members(community_dict)
        self._update_council_members(community_dict)

    def _update_community_members(self, community_dict):
        members = community_dict.get("members", [])
        if not isinstance(members, list):
            return
        non_members = set(self._countries_dict.keys()) - set(members)
        matching_event_types = (
            datamodel.HistoricalEventType.joined_galactic_community,
            datamodel.HistoricalEventType.left_galactic_community,
        )
        new_event_type = datamodel.HistoricalEventType.joined_galactic_community
        for c_id in members:
            self._update_membership_events(c_id, matching_event_types, new_event_type)

        new_event_type = datamodel.HistoricalEventType.left_galactic_community
        for c_id in non_members:
            self._update_membership_events(c_id, matching_event_types, new_event_type)

    def _update_council_members(self, community_dict):
        members = community_dict.get("council", [])
        if not isinstance(members, list):
            return
        non_members = set(self._countries_dict.keys()) - set(members)
        matching_event_types = (
            datamodel.HistoricalEventType.joined_galactic_council,
            datamodel.HistoricalEventType.left_galactic_council,
        )
        new_event_type = datamodel.HistoricalEventType.joined_galactic_council
        for c_id in members:
            self._update_membership_events(c_id, matching_event_types, new_event_type)

        new_event_type = datamodel.HistoricalEventType.left_galactic_council
        for c_id in non_members:
            self._update_membership_events(c_id, matching_event_types, new_event_type)

    def _update_membership_events(self, c_id, matching_event_types, new_event_type):
        country = self._countries_dict.get(c_id)
        if not country:
            logger.warning(
                f"{self._basic_info.logger_str} Could not find country with ID {c_id}"
            )
            return
        event_is_known = country.has_met_player()
        previous_event = (
            self._session.query(datamodel.HistoricalEvent)
            .filter(
                datamodel.HistoricalEvent.event_type.in_(matching_event_types),
                datamodel.HistoricalEvent.country == country,
                datamodel.HistoricalEvent.end_date_days.is_(None),
            )
            .order_by(datamodel.HistoricalEvent.start_date_days.desc())
            .first()
        )
        if previous_event is None and new_event_type in (
            datamodel.HistoricalEventType.left_galactic_community,
            datamodel.HistoricalEventType.left_galactic_council,
        ):
            return  # can't leave if it never joined
        elif previous_event is not None and previous_event.event_type == new_event_type:
            return  # don't add event if last event is the same
        else:
            if previous_event is not None:
                previous_event.end_date_days = self._basic_info.date_in_days
                previous_event.event_is_known_to_player = event_is_known
                self._session.add(previous_event)
            self._session.add(
                datamodel.HistoricalEvent(
                    event_type=new_event_type,
                    leader=self._ruler_dict.get(c_id),
                    country=country,
                    start_date_days=self._basic_info.date_in_days,
                    event_is_known_to_player=event_is_known,
                )
            )


class ScientistEventProcessor(AbstractGamestateDataProcessor):
    ID = "scientist_events"
    DEPENDENCIES = [CountryProcessor.ID, LeaderProcessor.ID]

    def __init__(self):
        super().__init__()
        self._leader_dict = None

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        self._leader_dict = dependencies[LeaderProcessor.ID]
        for country_id, country_model in countries_dict.items():
            self._history_add_tech_events(
                country_model, self._gamestate_dict["country"][country_id]
            )

    def _history_add_tech_events(self, country_model: datamodel.Country, country_dict):
        in_progress_techs = {}
        completed_techs = {}
        for t in country_model.technologies:
            tech_id = t.db_description.text
            if t.is_completed:
                completed_techs[tech_id] = t
            else:
                in_progress_techs[tech_id] = t

        tech_status_dict = country_dict.get("tech_status")
        if not isinstance(tech_status_dict, dict):
            return
        for tech_type in ["physics", "society", "engineering"]:
            progress_dict = tech_status_dict.get(f"{tech_type}_queue")
            if progress_dict and isinstance(progress_dict, list):
                progress_dict = progress_dict[0]
            if not isinstance(progress_dict, dict):
                continue

            tech_name = progress_dict.get("technology")
            if not isinstance(tech_name, str):
                continue

            level = progress_dict.get("level", 1)
            if level > 1:
                tech_name = f"{tech_name}_level_{level}"

            if tech_name in completed_techs:
                continue

            if tech_name in in_progress_techs:
                del in_progress_techs[tech_name]
            else:
                matching_description = self._get_or_add_shared_description(
                    text=tech_name
                )
                self._session.add(
                    datamodel.Technology(
                        country=country_model,
                        db_description=matching_description,
                        is_completed=False,
                    )
                )
                date_str = progress_dict.get("date")
                start_date = (
                    datamodel.date_to_days(date_str)
                    if date_str
                    else self._basic_info.date_in_days
                )
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.researched_technology,
                        country=country_model,
                        start_date_days=start_date,
                        db_description=matching_description,
                        event_is_known_to_player=country_model.has_met_player(),
                    )
                )

        technologies = tech_status_dict.get("technology", [])
        levels = tech_status_dict.get("level", [])
        if isinstance(technologies, list) and isinstance(levels, list):
            for tech_name, level in zip(technologies, levels):
                if level > 1:
                    tech_name = f"{tech_name}_level_{level}"
                if tech_name in in_progress_techs:
                    matching_event = self._get_matching_historical_event(
                        country_model, tech_name
                    )
                    matching_event.end_date_days = self._basic_info.date_in_days - 1
                    self._session.add(matching_event)
                    tech_model = in_progress_techs[tech_name]
                    tech_model.is_completed = True
                    self._session.add(tech_model)

    def _get_matching_historical_event(self, country_model, tech_name):
        matching_description = self._get_or_add_shared_description(text=tech_name)
        matching_event = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(
                event_type=datamodel.HistoricalEventType.researched_technology,
                country=country_model,
                db_description=matching_description,
            )
            .one_or_none()
        )
        return matching_event


class EnvoyEventProcessor(AbstractGamestateDataProcessor):
    ID = "envoy_events"
    DEPENDENCIES = [CountryProcessor.ID, LeaderProcessor.ID]

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        leaders = dependencies[LeaderProcessor.ID]

        for envoy_id_ingame, raw_leader_dict in sorted(
            self._gamestate_dict["leaders"].items()
        ):
            if not isinstance(raw_leader_dict, dict):
                continue
            if raw_leader_dict.get("class") != "envoy":
                continue
            if envoy_id_ingame not in leaders:
                continue
            envoy = leaders[envoy_id_ingame]
            country = envoy.country
            target_country = None
            location = raw_leader_dict.get("location", {})
            assignment = location.get("assignment", "idle")
            description = None
            if assignment == "improve_relations":
                event_type = datamodel.HistoricalEventType.envoy_improving_relations
                target_country = countries_dict.get(location.get("id"))
            elif assignment == "harm_relations":
                event_type = datamodel.HistoricalEventType.envoy_harming_relations
                target_country = countries_dict.get(location.get("id"))
            elif assignment == "galactic_community":
                event_type = datamodel.HistoricalEventType.envoy_community
            elif assignment == "federation":
                event_type = datamodel.HistoricalEventType.envoy_federation
                federations = self._gamestate_dict.get("federation", {})
                if isinstance(federations, dict):
                    federation_name = dump_name(
                        federations.get(location.get("id"), {}).get(
                            "name", "Unknown Federation"
                        )
                    )
                    description = self._get_or_add_shared_description(federation_name)
            else:
                event_type = None

            event_is_known = country.has_met_player()
            if target_country is not None:
                event_is_known &= target_country.has_met_player()

            previous_assignment = self._previous_assignment(envoy)

            assignment_is_the_same = False
            if previous_assignment is not None:
                if (
                    previous_assignment.event_type == event_type
                    and previous_assignment.target_country == target_country
                ):
                    assignment_is_the_same = True
                else:
                    previous_assignment.end_date_days = (
                        self._basic_info.date_in_days - 1
                    )
                    self._session.add(previous_assignment)

            if not assignment_is_the_same and event_type is not None:
                # print(f"{assignment_is_the_same=} {event_type=} {country.country_id} "
                #       f"{previous_assignment.event_type} {previous_assignment.target_country_id}")
                new_assignment_event = datamodel.HistoricalEvent(
                    start_date_days=self._basic_info.date_in_days,
                    country=country,
                    leader=envoy,
                    event_type=event_type,
                    event_is_known_to_player=event_is_known,
                    target_country=target_country,
                    db_description=description,
                )
                self._session.add(new_assignment_event)

    def _previous_assignment(
        self, envoy: datamodel.Leader
    ) -> Optional[datamodel.HistoricalEvent]:
        return (
            self._session.query(datamodel.HistoricalEvent)
            .filter(datamodel.HistoricalEvent.end_date_days.is_(None))
            .filter_by(leader=envoy)
            .order_by(datamodel.HistoricalEvent.start_date_days.desc())
            .first()
        )


class FleetInfoProcessor(AbstractGamestateDataProcessor):
    ID = "fleets"
    DEPENDENCIES = [
        LeaderProcessor.ID,
        CountryDataProcessor.ID,
        FleetOwnershipProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
        self._new_fleet_commands = None
        self._fleet_compos = None
        self._leaders = None
        self._country_datas = None
        self._fleet_owners = None

    def initialize_data(self):
        self._new_fleet_commands = {}
        self._fleet_compos = {}

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        self._leaders = dependencies[LeaderProcessor.ID]
        self._country_datas = dependencies[CountryDataProcessor.ID]
        self._fleet_owners = dependencies[FleetOwnershipProcessor.ID]

        for fleet_id, fleet_dict in sorted(self._gamestate_dict["fleet"].items()):
            if not isinstance(fleet_dict, dict):
                continue
            country = self._fleet_owners.get(fleet_id)
            if country is None:
                continue

            ships = fleet_dict.get("ships", [])
            name = dump_name(fleet_dict.get("name", "Unnamed Fleet"))

            for ship_id in ships:
                ship_dict = self._gamestate_dict["ships"].get(ship_id)
                if not isinstance(ship_dict, dict):
                    continue

                self._check_ship_command(fleet_id, name, ship_dict)
                self._count_ship_for_fleet_composition(
                    country.country_id_in_game, ship_dict
                )

        self._store_fleet_composition()
        self._update_leader_fleet_commands()

    def _check_ship_command(self, fleet_id, fleet_name, ship_dict):
        leader_id = ship_dict.get("leader")
        if leader_id is not None:
            fleet_model = (
                self._session.query(datamodel.Fleet)
                .filter_by(fleet_id_in_game=fleet_id)
                .one_or_none()
            )
            if fleet_model is None:
                fleet_model = datamodel.Fleet(
                    name=fleet_name,
                    fleet_id_in_game=fleet_id,
                    is_civilian_fleet=self._get_ship_class(ship_dict) == "science",
                )
                self._session.add(fleet_model)
            elif fleet_model.name != fleet_name:
                fleet_model.name = fleet_name
                self._session.add(fleet_model)
            self._new_fleet_commands[leader_id] = fleet_model

    def _count_ship_for_fleet_composition(self, owner_id, ship_dict):
        ship_class = self._get_ship_class(ship_dict)
        if isinstance(ship_class, str):
            if owner_id not in self._fleet_compos:
                self._fleet_compos[owner_id] = dict(
                    ship_count_corvette=0,
                    ship_count_destroyer=0,
                    ship_count_cruiser=0,
                    ship_count_battleship=0,
                    ship_count_titan=0,
                    ship_count_colossus=0,
                )
            if ship_class == "corvette":
                self._fleet_compos[owner_id]["ship_count_corvette"] += 1
            elif ship_class == "destroyer":
                self._fleet_compos[owner_id]["ship_count_destroyer"] += 1
            elif ship_class == "cruiser":
                self._fleet_compos[owner_id]["ship_count_cruiser"] += 1
            elif ship_class == "battleship":
                self._fleet_compos[owner_id]["ship_count_battleship"] += 1
            elif ship_class == "titan":
                self._fleet_compos[owner_id]["ship_count_titan"] += 1
            elif ship_class == "colossus":
                self._fleet_compos[owner_id]["ship_count_colossus"] += 1

    def _get_ship_class(self, ship_dict):
        design_dict = self._gamestate_dict["ship_design"].get(
            ship_dict.get("ship_design"), {}
        )
        ship_class = design_dict.get("ship_size")
        return ship_class

    def _store_fleet_composition(self):
        for cid, composition in self._fleet_compos.items():
            country_data = self._country_datas[cid]
            for key, value in composition.items():
                setattr(country_data, key, value)
            self._session.add(country_data)

    def _update_leader_fleet_commands(self):
        for leader_id, leader_model in self._leaders.items():
            new_fleet_command = self._new_fleet_commands.get(leader_id)
            if new_fleet_command == leader_model.fleet_command:
                continue

            previous_event = (
                self._session.query(datamodel.HistoricalEvent)
                .filter_by(
                    event_type=datamodel.HistoricalEventType.fleet_command,
                    leader=leader_model,
                )
                .order_by(datamodel.HistoricalEvent.start_date_days.desc())
                .first()
            )
            if previous_event is not None:
                if previous_event.fleet == new_fleet_command:
                    continue
                previous_event.end_date_days = self._basic_info.date_in_days - 1
                self._session.add(previous_event)

            if leader_id in self._new_fleet_commands:
                country = leader_model.country
                cd = self._country_datas.get(country.country_id_in_game)
                event_is_known = (
                    country.is_player
                    or cd is not None
                    and (new_fleet_command.is_civilian_fleet and cd.show_tech_info())
                    or cd is not None
                    and (
                        not new_fleet_command.is_civilian_fleet
                        and cd.show_military_info()
                    )
                )
                self._session.add(
                    datamodel.HistoricalEvent(
                        start_date_days=self._basic_info.date_in_days,
                        country=country,
                        leader=leader_model,
                        fleet=self._new_fleet_commands[leader_id],
                        event_type=datamodel.HistoricalEventType.fleet_command,
                        event_is_known_to_player=event_is_known,
                    )
                )
            elif leader_model.fleet_command is not None:
                leader_model.fleet_command = None
            self._session.add(leader_model)


class WarProcessor(AbstractGamestateDataProcessor):
    ID = "wars"
    DEPENDENCIES = [
        RulerEventProcessor.ID,
        CountryProcessor.ID,
        SystemProcessor.ID,
        PlanetProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
        self._ruler_dict = None
        self._countries_dict = None
        self._system_models_dict = None
        self._planet_models_dict = None
        self.active_wars: Dict[int, datamodel.War] = None

    def initialize_data(self):
        self.active_wars = {}

    def data(self) -> Any:
        return dict(active_wars=self.active_wars)

    def extract_data_from_gamestate(self, dependencies):
        self._ruler_dict = dependencies[RulerEventProcessor.ID]
        self._countries_dict = dependencies[CountryProcessor.ID]
        self._system_models_dict = dependencies[SystemProcessor.ID][
            "systems_by_ingame_id"
        ]
        self._planet_models_dict = dependencies[PlanetProcessor.ID]

        wars_dict = self._gamestate_dict.get("war", {})
        if not isinstance(wars_dict, dict):
            return
        for war_id, war_dict in wars_dict.items():
            war_model = self._update_war(war_id, war_dict)
            if war_model is None:
                continue
            self.active_wars[war_id] = war_model
            self.update_war_participants(war_dict, war_model)
            self._extract_combat_victories(war_dict, war_model)

    def _update_war(self, war_id: int, war_dict):
        if not isinstance(war_dict, dict):
            return
        war_model = (
            self._session.query(datamodel.War)
            .order_by(datamodel.War.start_date_days.desc())
            .filter_by(war_id_in_game=war_id)
            .first()
        )
        if war_model is None:
            start_date_days = datamodel.date_to_days(war_dict.get("start_date"))
            war_model = datamodel.War(
                war_id_in_game=war_id,
                game=self._db_game,
                start_date_days=start_date_days,
                end_date_days=self._basic_info.date_in_days,
                outcome=datamodel.WarOutcome.in_progress,
            )
        elif war_model.outcome != datamodel.WarOutcome.in_progress:
            # skip already finished wars
            return
        war_model.attacker_war_exhaustion = war_dict.get("attacker_war_exhaustion", 0.0)
        war_model.defender_war_exhaustion = war_dict.get("defender_war_exhaustion", 0.0)
        war_model.end_date_days = self._basic_info.date_in_days - 1
        self._session.add(war_model)
        return war_model

    def update_war_participants(self, war_dict, war_model):
        war_goal_attacker = war_dict.get("attacker_war_goal", {}).get("type")
        war_goal_defender = war_dict.get("defender_war_goal", {})
        if isinstance(war_goal_defender, dict):
            war_goal_defender = war_goal_defender.get("type")
        elif not war_goal_defender or war_goal_defender == "none":
            war_goal_defender = None
        attackers = {p["country"] for p in war_dict["attackers"]}
        for war_party_info in itertools.chain(
            war_dict.get("attackers", []), war_dict.get("defenders", [])
        ):
            if not isinstance(war_party_info, dict):
                continue  # just in case
            country_id_ingame = war_party_info.get("country")
            db_country = self._countries_dict.get(country_id_ingame)
            if db_country is None:
                logger.warning(
                    f"{self._basic_info.logger_str}     Could not find country matching war participant {war_party_info}"
                )
                continue

            call_type = war_party_info.get("call_type", "unknown")
            caller = None
            if war_party_info.get("caller") in self._countries_dict:
                caller = self._countries_dict[war_party_info.get("caller")]

            is_attacker = country_id_ingame in attackers

            war_participant = (
                self._session.query(datamodel.WarParticipant)
                .filter_by(war=war_model, country=db_country)
                .one_or_none()
            )
            if war_participant is None:
                war_goal = war_goal_attacker if is_attacker else war_goal_defender
                war_participant = datamodel.WarParticipant(
                    war=war_model,
                    war_goal=war_goal,
                    country=db_country,
                    caller_country=caller,
                    call_type=call_type,
                    is_attacker=is_attacker,
                )
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.war,
                        country=war_participant.country,
                        target_country=war_participant.caller_country,
                        leader=self._ruler_dict.get(country_id_ingame),
                        start_date_days=self._basic_info.date_in_days,
                        end_date_days=self._basic_info.date_in_days,
                        war=war_model,
                        event_is_known_to_player=war_participant.country.has_met_player(),
                        db_description=self._get_or_add_shared_description(call_type),
                    )
                )
            if war_participant.war_goal is None:
                war_participant.war_goal = war_goal_defender
            self._session.add(war_participant)

    def _extract_combat_victories(self, war_dict, war: datamodel.War):
        battles = war_dict.get("battles", [])
        if not isinstance(battles, list):
            battles = [battles]
        for battle_dict in battles:
            if not isinstance(battle_dict, dict):
                continue
            battle_attackers = battle_dict.get("attackers")
            battle_defenders = battle_dict.get("defenders")
            if not battle_attackers or not battle_defenders:
                continue
            if battle_dict.get("attacker_victory") not in {"yes", "no"}:
                continue
            attacker_victory = battle_dict.get("attacker_victory") == "yes"

            planet_model = self._planet_models_dict.get(battle_dict.get("planet"))
            if planet_model is None:
                system_id_in_game = battle_dict.get("system")
                system = self._system_models_dict.get(system_id_in_game)
                if system is None:
                    continue
            else:
                system = planet_model.system

            combat_type = datamodel.CombatType.__members__.get(
                battle_dict.get("type"), datamodel.CombatType.other
            )

            date_str = battle_dict.get("date")
            date_in_days = datamodel.date_to_days(date_str)
            if date_in_days < 0:
                date_in_days = self._basic_info.date_in_days

            attacker_exhaustion = battle_dict.get("attacker_war_exhaustion", 0.0)
            defender_exhaustion = battle_dict.get("defender_war_exhaustion", 0.0)
            if (
                defender_exhaustion + attacker_exhaustion <= 0.001
                and combat_type != datamodel.CombatType.armies
            ):
                continue
            combat = (
                self._session.query(datamodel.Combat)
                .filter_by(
                    war=war,
                    system=system if system is not None else planet_model.system,
                    planet=planet_model,
                    combat_type=combat_type,
                    attacker_victory=attacker_victory,
                    attacker_war_exhaustion=attacker_exhaustion,
                    defender_war_exhaustion=defender_exhaustion,
                )
                .order_by(datamodel.Combat.date.desc())
                .first()
            )

            if combat is not None:
                continue

            combat = datamodel.Combat(
                war=war,
                date=date_in_days,
                attacker_war_exhaustion=attacker_exhaustion,
                defender_war_exhaustion=defender_exhaustion,
                system=system,
                planet=planet_model,
                combat_type=combat_type,
                attacker_victory=attacker_victory,
            )
            self._session.add(combat)

            is_known_to_player = False
            for country_id in itertools.chain(battle_attackers, battle_defenders):
                db_country = self._countries_dict.get(country_id)
                if db_country is None:
                    logger.warning(
                        f"{self._basic_info.logger_str}     Could not find country with ID {country_id} "
                        f"when processing battle {battle_dict}"
                    )
                    continue
                is_known_to_player |= db_country.has_met_player()
                war_participant = (
                    self._session.query(datamodel.WarParticipant)
                    .filter_by(war=war, country=db_country)
                    .one_or_none()
                )
                if war_participant is None:
                    logger.info(
                        f"{self._basic_info.logger_str}     Could not find War participant matching country "
                        f"{db_country.country_name} and war {war.name}."
                    )
                    continue
                self._session.add(
                    datamodel.CombatParticipant(
                        combat=combat,
                        war_participant=war_participant,
                        is_attacker=country_id in battle_attackers,
                    )
                )

            event_type = (
                datamodel.HistoricalEventType.army_combat
                if combat_type == datamodel.CombatType.armies
                else datamodel.HistoricalEventType.fleet_combat
            )
            self._session.add(
                datamodel.HistoricalEvent(
                    event_type=event_type,
                    combat=combat,
                    system=system,
                    planet=planet_model,
                    war=war,
                    start_date_days=date_in_days,
                    event_is_known_to_player=is_known_to_player,
                )
            )


class TruceProcessor(AbstractGamestateDataProcessor):
    ID = "truces"
    DEPENDENCIES = [
        CountryProcessor.ID,
        RulerEventProcessor.ID,
        DiplomacyDictProcessor.ID,
        WarProcessor.ID,
    ]

    def __init__(self):
        self._ruler_dict = None

    def extract_data_from_gamestate(self, dependencies):
        self._ruler_dict = dependencies[RulerEventProcessor.ID]
        diplo_truces = dependencies[DiplomacyDictProcessor.ID]["truce_countries"]
        wars_dict = dependencies[WarProcessor.ID]["active_wars"]

        truces_dict = self._gamestate_dict.get("truce", {})
        if not isinstance(truces_dict, dict):
            return

        unresolved_wars: List[datamodel.War] = (
            self._session.query(datamodel.War)
            .where(
                sqlalchemy.and_(
                    # we don't care about already finished wars
                    datamodel.War.outcome == datamodel.WarOutcome.in_progress,
                    # and we don't care about wars that are still in the current save file
                    datamodel.War.war_id_in_game.not_in(list(wars_dict)),
                )
            )
            .all()
        )
        war_by_participant_countries = {
            frozenset(wp.country.country_id_in_game for wp in w.participants): w
            for w in unresolved_wars
        }

        resolved = set()
        #  resolve wars based on truces...
        for truce_id, countries in diplo_truces.items():
            truce_info = truces_dict.get(truce_id)
            if not isinstance(truce_info, dict):
                continue
            truce_type = truce_info.get("truce_type", "other")
            if truce_type != "war":
                continue  # truce is due to diplomatic agreements or similar, ignore

            countries_frozen = frozenset(countries)
            if countries_frozen in war_by_participant_countries:
                matching_war = war_by_participant_countries[countries_frozen]
                resolved.add(countries_frozen)
            else:
                continue

            end_date = truce_info.get("start_date")  # start of truce => end of war
            if (
                isinstance(end_date, str)
                and end_date is not None
                and end_date != "none"
            ):
                matching_war.end_date_days = datamodel.date_to_days(end_date)
            else:
                matching_war.end_date_days = self._basic_info.date_in_days - 1
            matching_war.outcome = datamodel.WarOutcome.truce
            self._history_add_peace_events(matching_war)
            self._session.add(matching_war)

        # resolve the wars that are no longer in the save file and do not match any truce:
        for countries, war in war_by_participant_countries.items():
            if countries in resolved:
                continue
            war.outcome = datamodel.WarOutcome.resolution_unknown
            self._session.add(war)
            self._history_add_peace_events(war)

    def _history_add_peace_events(self, war: datamodel.War):
        for wp in war.participants:
            matching_event = (
                self._session.query(datamodel.HistoricalEvent)
                .filter_by(
                    event_type=datamodel.HistoricalEventType.peace,
                    country=wp.country,
                    war=war,
                )
                .one_or_none()
            )
            if matching_event is None:
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.peace,
                        war=war,
                        country=wp.country,
                        leader=self._ruler_dict.get(wp.country.country_id_in_game),
                        start_date_days=war.end_date_days,
                        event_is_known_to_player=wp.country.has_met_player(),
                    )
                )


class PopStatsProcessor(AbstractGamestateDataProcessor):
    ID = "pop_stats"
    DEPENDENCIES = [
        CountryProcessor.ID,
        SpeciesProcessor.ID,
        FactionProcessor.ID,
        CountryDataProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
        self.country_by_planet_id = None

    def initialize_data(self):
        self._initialize_planet_owner_dict()

    def extract_data_from_gamestate(self, dependencies):
        def init_dict():
            return dict(pop_count=0, crime=0, happiness=0, power=0)

        countries_dict = dependencies[CountryProcessor.ID]
        country_data_dict = dependencies[CountryDataProcessor.ID]
        species_dict, robot_species = dependencies[SpeciesProcessor.ID]
        faction_by_ingame_id = dependencies[FactionProcessor.ID]

        for country_id_in_game, country_model in countries_dict.items():
            if not config.CONFIG.read_all_countries and not country_model.is_player:
                continue
            if country_id_in_game in self._basic_info.other_players:
                continue
            country_data = country_data_dict[country_id_in_game]
            stats_by_species = {}
            stats_by_faction = {}
            stats_by_job = {}
            stats_by_stratum = {}
            stats_by_ethos = {}
            stats_by_planet = {}

            for pop_dict in self._gamestate_dict["pop"].values():
                if not isinstance(pop_dict, dict):
                    continue
                planet_id = pop_dict.get("planet")
                planet_country_id_in_game = self.country_by_planet_id.get(planet_id)
                if planet_country_id_in_game != country_id_in_game:
                    continue

                species_id = pop_dict.get("species")
                job = pop_dict.get("job", "unemployed")
                stratum = pop_dict.get("category", "unknown stratum")
                faction_id = pop_dict.get("pop_faction")

                if faction_id is None:
                    if stratum == "slave":
                        faction_id = FactionProcessor.SLAVE_FACTION_ID
                    elif species_id in robot_species:
                        faction_id = FactionProcessor.NON_SENTIENT_ROBOT_FACTION_ID
                    elif stratum == "purge":
                        faction_id = FactionProcessor.PURGE_FACTION_ID
                    else:
                        faction_id = FactionProcessor.NO_FACTION_ID

                ethos = pop_dict.get("ethos", {}).get("ethic")
                if not isinstance(ethos, str):
                    ethos = "ethic_no_ethos"

                crime = pop_dict.get("crime", 0.0)
                happiness = pop_dict.get("happiness", 0.0)
                power = pop_dict.get("power", 0.0)

                if species_id not in stats_by_species:
                    stats_by_species[species_id] = init_dict()
                if faction_id not in stats_by_faction:
                    stats_by_faction[faction_id] = init_dict()
                if job not in stats_by_job:
                    stats_by_job[job] = init_dict()
                if stratum not in stats_by_stratum:
                    stats_by_stratum[stratum] = init_dict()
                if ethos not in stats_by_ethos:
                    stats_by_ethos[ethos] = init_dict()
                if planet_id not in stats_by_planet:
                    stats_by_planet[planet_id] = init_dict()

                stats_by_species[species_id]["pop_count"] += 1
                stats_by_faction[faction_id]["pop_count"] += 1
                stats_by_job[job]["pop_count"] += 1
                stats_by_stratum[stratum]["pop_count"] += 1
                stats_by_ethos[ethos]["pop_count"] += 1
                stats_by_planet[planet_id]["pop_count"] += 1

                stats_by_species[species_id]["crime"] += crime
                stats_by_faction[faction_id]["crime"] += crime
                stats_by_job[job]["crime"] += crime
                stats_by_stratum[stratum]["crime"] += crime
                stats_by_ethos[ethos]["crime"] += crime
                stats_by_planet[planet_id]["crime"] += crime

                stats_by_species[species_id]["happiness"] += happiness
                stats_by_faction[faction_id]["happiness"] += happiness
                stats_by_job[job]["happiness"] += happiness
                stats_by_stratum[stratum]["happiness"] += happiness
                stats_by_ethos[ethos]["happiness"] += happiness
                stats_by_planet[planet_id]["happiness"] += happiness

                stats_by_species[species_id]["power"] += power
                stats_by_faction[faction_id]["power"] += power
                stats_by_job[job]["power"] += power
                stats_by_stratum[stratum]["power"] += power
                stats_by_ethos[ethos]["power"] += power
                stats_by_planet[planet_id]["power"] += power

            for species_id, stats in stats_by_species.items():
                if stats["pop_count"] == 0:
                    continue
                if species_id is None or species_id not in species_dict:
                    continue
                stats["crime"] /= stats["pop_count"]
                stats["happiness"] /= stats["pop_count"]
                stats["power"] /= stats["pop_count"]

                species = species_dict[species_id]
                self._session.add(
                    datamodel.PopStatsBySpecies(
                        country_data=country_data,
                        species=species,
                        **stats,
                    )
                )

            gamestate_dict_factions = self._gamestate_dict.get("pop_factions")
            if not isinstance(gamestate_dict_factions, dict):
                gamestate_dict_factions = {}
            for faction_id, stats in stats_by_faction.items():
                if stats["pop_count"] == 0:
                    continue

                faction = faction_by_ingame_id.get(faction_id)
                if faction is None:
                    continue

                faction_dict = gamestate_dict_factions.get(faction_id, {})
                if not isinstance(faction_dict, dict):
                    faction_dict = {}

                stats["crime"] /= stats["pop_count"]
                stats["happiness"] /= stats["pop_count"]
                stats["power"] /= stats["pop_count"]
                stats["faction_approval"] = faction_dict.get("faction_approval", 0.0)
                stats["support"] = faction_dict.get("support", 0.0)

                self._session.add(
                    datamodel.PopStatsByFaction(
                        country_data=country_data,
                        faction=faction,
                        **stats,
                    )
                )

            for planet_id, stats in stats_by_planet.items():
                if stats["pop_count"] == 0:
                    continue
                stats["crime"] /= stats["pop_count"]
                stats["happiness"] /= stats["pop_count"]
                stats["power"] /= stats["pop_count"]

                planet_dict = self._gamestate_dict["planets"]["planet"].get(planet_id)
                if not isinstance(planet_dict, dict):
                    continue

                stats["migration"] = planet_dict.get("migration", 0.0)
                stats["free_amenities"] = planet_dict.get("free_amenities", 0.0)
                stats["free_housing"] = planet_dict.get("free_housing", 0.0)
                stats["stability"] = planet_dict.get("stability", 0.0)

                planet = (
                    self._session.query(datamodel.Planet)
                    .filter_by(planet_id_in_game=planet_id)
                    .one_or_none()
                )
                if planet is None:
                    logger.warning(
                        f"{self._basic_info.logger_str}     Could not find planet with ID {planet_id}!"
                    )
                    continue
                self._session.add(
                    datamodel.PlanetStats(
                        country_data=country_data,
                        planet=planet,
                        **stats,
                    )
                )

            for job, stats in stats_by_job.items():
                if stats["pop_count"] == 0:
                    continue
                stats["crime"] /= stats["pop_count"]
                stats["happiness"] /= stats["pop_count"]
                stats["power"] /= stats["pop_count"]

                job = self._get_or_add_shared_description(job)
                self._session.add(
                    datamodel.PopStatsByJob(
                        country_data=country_data,
                        db_job_description=job,
                        **stats,
                    )
                )

            for stratum, stats in stats_by_stratum.items():
                if stats["pop_count"] == 0:
                    continue
                stats["crime"] /= stats["pop_count"]
                stats["happiness"] /= stats["pop_count"]
                stats["power"] /= stats["pop_count"]

                stratum = self._get_or_add_shared_description(stratum)
                self._session.add(
                    datamodel.PopStatsByStratum(
                        country_data=country_data,
                        db_stratum_description=stratum,
                        **stats,
                    )
                )

            for ethos, stats in stats_by_ethos.items():
                if stats["pop_count"] == 0:
                    continue
                stats["crime"] /= stats["pop_count"]
                stats["happiness"] /= stats["pop_count"]
                stats["power"] /= stats["pop_count"]

                ethos = self._get_or_add_shared_description(ethos)
                self._session.add(
                    datamodel.PopStatsByEthos(
                        country_data=country_data,
                        db_ethos_description=ethos,
                        **stats,
                    )
                )

    def _initialize_planet_owner_dict(self):
        self.country_by_planet_id = {}
        for country_id, country_dict in sorted(self._gamestate_dict["country"].items()):
            if not isinstance(country_dict, dict):
                continue
            for planet_id in country_dict.get("owned_planets", []):
                self.country_by_planet_id[planet_id] = country_id


def _all_planetary_modifiers(planet_dict) -> Iterable[Tuple[str, int]]:
    modifiers = planet_dict.get("timed_modifier", [])
    if not isinstance(modifiers, list):
        modifiers = [modifiers]
    for m in modifiers:
        if not isinstance(m, dict):
            continue
        modifier = m.get("modifier", "no modifier")
        duration = m.get("days")
        if duration == -1 or not isinstance(duration, int):
            duration = None
        yield modifier, duration

    planet_modifiers = planet_dict.get("planet_modifier", [])
    if not isinstance(planet_modifiers, list):
        planet_modifiers = [planet_modifiers]
    for pm in planet_modifiers:
        if pm is not None:
            yield pm, None
