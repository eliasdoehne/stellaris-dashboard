import abc
import dataclasses
import datetime
import itertools
import logging
import random
import time
from typing import Dict, Any, Union, Set, Iterable, Optional

from stellarisdashboard import models, game_info, config

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class BasicGameInfo:
    game_id: str
    date_in_days: int
    player_country_id: int

    @property
    def logger_str(self) -> str:
        return f"{self.game_id} {models.days_to_date(self.date_in_days)}"


class TimelineExtractor:
    SUPPORTED_COUNTRY_TYPES = {"default", "fallen_empire", "awakened_fallen_empire"}

    def __init__(self):
        self.basic_info: BasicGameInfo = None
        self._session = None
        self._gamestate_dict = None

    def process_gamestate(self, game_id: str, gamestate_dict: Dict[str, Any]):
        self._gamestate_dict = gamestate_dict
        self._read_basic_game_info(game_id)
        logger.info(f"{self.basic_info.logger_str} Processing Gamestate")
        t_start_gs = time.clock()
        with models.get_db_session(game_id) as self._session:
            try:
                self._process_gamestate(game_id)
                logger.info(f"{self.basic_info.logger_str} Processed Gamestate in {time.clock() - t_start_gs:.3f} s, writing changes to database")
                self._session.commit()
            except Exception as e:
                self._session.rollback()
                logger.exception(f"{self.basic_info.logger_str} Rolling back changes to database...")
                if config.CONFIG.debug_mode or isinstance(e, KeyboardInterrupt):
                    raise e

    def _process_gamestate(self, game_id):
        db_game = self._get_or_add_game_to_db(game_id)
        existing_dates = {gs.date for gs in db_game.game_states}
        if self.basic_info.date_in_days in existing_dates:
            logger.info(f"{self.basic_info.logger_str} Gamestate for same date already exists in database. Aborting...")
            self._session.rollback()
        else:
            db_game_state = models.GameState(
                game=db_game,
                date=self.basic_info.date_in_days,
            )
            self._session.add(db_game_state)
            all_dependencies = {}
            for data_processor in self._data_processors():
                t_start = time.clock()
                data_processor.initialize(db_game, self._gamestate_dict, db_game_state, self.basic_info, self._session)

                missing_dependencies = sorted(dep for dep in data_processor.DEPENDENCIES if dep not in all_dependencies)
                if missing_dependencies:
                    logger.info(f"{self.basic_info.logger_str}   - Could not process {data_processor.ID} due to missing dependencies {', '.join(missing_dependencies)}")
                else:
                    logger.info(f"{self.basic_info.logger_str}   - Processing {data_processor.ID}")
                    data_processor.extract_data_from_gamestate(
                        {key: all_dependencies[key] for key in data_processor.DEPENDENCIES}
                    )
                    all_dependencies[data_processor.ID] = data_processor.data()
                    logger.info(f"{self.basic_info.logger_str}         done ({time.clock() - t_start:.3f} s)")

    def _get_or_add_game_to_db(self, game_id: str):
        game = self._session.query(models.Game).filter_by(game_name=game_id).first()
        if game is None:
            logger.info(f"{self.basic_info.logger_str} Adding new game {game_id} to database.")
            player_country_name = self._gamestate_dict["country"][self.basic_info.player_country_id]["name"]
            galaxy_info = self._gamestate_dict["galaxy"]
            game = models.Game(
                game_name=game_id,
                player_country_name=player_country_name,
                db_galaxy_template=galaxy_info.get("template", "Unknown"),
                db_galaxy_shape=galaxy_info.get("shape", "Unknown"),
                db_difficulty=galaxy_info.get("difficulty", "Unknown"),
                db_last_updated=datetime.datetime.now(),
            )
        game.db_last_updated = datetime.datetime.now()
        self._session.add(game)
        return game

    def _read_basic_game_info(self, game_id: str):
        player_country_id = self._identify_player_country()
        date_str = self._gamestate_dict["date"]
        date_in_days = models.date_to_days(date_str)
        self.basic_info = BasicGameInfo(
            game_id=game_id,
            date_in_days=date_in_days,
            player_country_id=player_country_id,
        )

    def _identify_player_country(self):
        players = self._gamestate_dict.get("player")
        if players:
            if len({player["country"] for player in players}) != 1:
                raise ValueError(f"{self.basic_info.logger_str} Player country is ambiguous!")
            return players[0]["country"]
        else:
            return 0

    def _data_processors(self) -> Iterable["AbstractGamestateDataProcessor"]:
        yield SystemProcessor()
        yield CountryProcessor()
        yield SystemOwnershipProcessor()
        yield DiplomacyProcessor()
        yield SensorLinkProcessor()
        yield CountryDataProcessor()
        yield SpeciesProcessor()
        yield LeaderProcessor()
        yield PlanetModelProcessor()
        yield SectorColonyEventProcessor()
        yield PlanetUpdateProcessor()
        yield RulerEventProcessor()
        yield GovernmentProcessor()
        yield FactionProcessor()
        yield DiplomacyHistoricalEventProcessor()
        yield ScientistEventProcessor()
        yield WarProcessor()
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

    def initialize(self, game: models.Game,
                   gamestate_dict: Dict[str, Any],
                   gs: models.GameState,
                   basic_info: BasicGameInfo,
                   db_session):
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

    def _get_or_add_shared_description(self, text: str) -> models.SharedDescription:
        matching_description = self._session.query(models.SharedDescription).filter_by(
            text=text,
        ).one_or_none()
        if matching_description is None:
            matching_description = models.SharedDescription(text=text)
            self._session.add(matching_description)
        return matching_description


class SystemProcessor(AbstractGamestateDataProcessor):
    ID = "systems"
    DEPENDENCIES = []

    def __init__(self):
        super().__init__()
        self.systems_by_ingame_id = None

    def data(self) -> Dict[int, models.System]:
        return self.systems_by_ingame_id

    def extract_data_from_gamestate(self, dependencies):
        self.systems_by_ingame_id = {s.system_id_in_game: s
                                     for s in self._session.query(models.System)}
        for ingame_id, system_data in self._gamestate_dict["galactic_object"].items():
            if ingame_id in self.systems_by_ingame_id:
                self._update_system(system_model=self.systems_by_ingame_id[ingame_id],
                                    system_data=system_data)
            else:
                system = self._add_system(system_id=ingame_id, system_data=system_data)
                if system is None:
                    logger.info(f"{self._basic_info.logger_str} Could not add or find system with ID {ingame_id} to database.")
                    continue
                self.systems_by_ingame_id[ingame_id] = system

    def _update_system(self, system_model: models.System, system_data: Dict):
        system_name = system_data.get("name")
        if system_name != system_model.name:
            system_model.name = system_name
            self._session.add(system_model)

    def _add_system(self, system_id: int, system_data: Dict) -> Optional[models.System]:
        if system_data is None:
            logger.warning(f"{self._basic_info.logger_str} Found no data for system with ID {system_id}!")
            return
        system_name = system_data.get("name")
        coordinate_x = system_data.get("coordinate", {}).get("x", 0)
        coordinate_y = system_data.get("coordinate", {}).get("y", 0)
        system_model = models.System(
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
            neighbor_model = self._session.query(models.System).filter_by(
                system_id_in_game=neighbor_id
            ).one_or_none()
            if neighbor_model is None:
                continue  # assume that the hyperlane will be created when adding the neighbor system to DB later

            self._session.add(models.HyperLane(
                system_one=system_model,
                system_two=neighbor_model,
            ))

        self._session.add(system_model)
        return system_model


class CountryProcessor(AbstractGamestateDataProcessor):
    ID = "country"
    DEPENDENCIES = []

    def __init__(self):
        super().__init__()
        self.countries_by_ingame_id: Dict[int, models.Country] = None

    def initialize_data(self):
        self.countries_by_ingame_id = {}

    def data(self) -> Dict[int, models.Country]:
        return self.countries_by_ingame_id

    def extract_data_from_gamestate(self, dependencies):
        for country_id, country_data_dict in self._gamestate_dict["country"].items():
            if not isinstance(country_data_dict, dict):
                continue
            country_type = country_data_dict.get("type")
            country_name = country_data_dict.get("name", "no name")
            country_model = self._session.query(models.Country).filter_by(
                game=self._db_game,
                country_id_in_game=country_id
            ).one_or_none()
            if country_model is None:
                country_model = models.Country(
                    is_player=(country_id == self._basic_info.player_country_id),
                    country_id_in_game=country_id,
                    game=self._db_game,
                    country_type=country_type,
                    country_name=country_name
                )
                if country_id == self._basic_info.player_country_id:
                    country_model.first_player_contact_date = 0
                self._session.add(country_model)
            if country_name != country_model.country_name or country_type != country_model.country_type:
                country_model.country_name = country_name
                country_model.country_type = country_type
                self._session.add(country_model)
            self.countries_by_ingame_id[country_id] = country_model


class SystemOwnershipProcessor(AbstractGamestateDataProcessor):
    ID = "system_ownership"
    DEPENDENCIES = [SystemProcessor.ID, CountryProcessor.ID]

    def __init__(self):
        super().__init__()
        self.systems_by_owner_country_id: Dict[int, Set[models.System]] = None

    def initialize_data(self):
        self.systems_by_owner_country_id = {}

    def data(self) -> Dict[int, Set[models.System]]:
        return self.systems_by_owner_country_id

    def extract_data_from_gamestate(self, dependencies):
        starbases = self._gamestate_dict.get("starbases", {})
        countries_dict = dependencies[CountryProcessor.ID]
        systems_dict = dependencies[SystemProcessor.ID]

        starbase_systems = set()
        for starbase_dict in starbases.values():
            if not isinstance(starbase_dict, dict):
                continue
            system_id_in_game = starbase_dict.get("system")
            country_id_in_game = starbase_dict.get("owner")
            system_model = systems_dict.get(system_id_in_game)
            country_model = countries_dict.get(country_id_in_game)

            if country_model is None or system_model is None:
                logger.warning(f"{self._basic_info.logger_str} Cannot establish ownership for system {system_id_in_game} and country {country_id_in_game}")
                continue

            starbase_systems.add(system_id_in_game)

            if country_id_in_game not in self.systems_by_owner_country_id:
                self.systems_by_owner_country_id[country_id_in_game] = set()
            self.systems_by_owner_country_id[country_id_in_game].add(system_model)

            if country_model != system_model.country:
                self._update_ownership(current_owner_country=country_model,
                                       system_model=system_model)

        for system_id, system_model in systems_dict.items():
            if system_id in starbase_systems:
                continue
            if system_model.country is None:
                continue
            self._update_ownership(None, system_model)

    def _update_ownership(self, current_owner_country: models.Country, system_model: models.System):
        owner_changed = False
        event_type = None
        target_country = None

        if current_owner_country is None:
            owner_changed = True
            current_owner_country = None
            event_type = models.HistoricalEventType.lost_system
            self._session.add(models.HistoricalEvent(
                event_type=models.HistoricalEventType.lost_system,
                country=system_model.country,
                system=system_model,
                start_date_days=self._basic_info.date_in_days,
                event_is_known_to_player=system_model.country.has_met_player(),
            ))

        elif system_model.country is None:
            owner_changed = True
            event_type = models.HistoricalEventType.expanded_to_system
            target_country = None

        elif system_model.country != current_owner_country:
            owner_changed = True
            event_type = models.HistoricalEventType.gained_system

        if owner_changed:
            system_model.country = current_owner_country
            self._session.add(system_model)

            ownership = self._session.query(models.SystemOwnership).filter_by(
                system=system_model
            ).order_by(models.SystemOwnership.end_date_days.desc()).first()
            if ownership is not None:
                ownership.end_date_days = self._basic_info.date_in_days - 1
                self._session.add(ownership)

                target_country = ownership.country
                if target_country is not None:
                    is_visible = target_country.has_met_player() or (current_owner_country is not None
                                                                     and current_owner_country.has_met_player())
                    self._session.add(models.HistoricalEvent(
                        event_type=models.HistoricalEventType.lost_system,
                        country=target_country,
                        target_country=current_owner_country,
                        system=system_model,
                        start_date_days=self._basic_info.date_in_days,
                        event_is_known_to_player=is_visible,
                    ))
            if current_owner_country is not None:
                self._session.add(models.SystemOwnership(
                    start_date_days=self._basic_info.date_in_days,
                    end_date_days=self._basic_info.date_in_days + 1,
                    country=current_owner_country,
                    system=system_model,
                ))
                self._session.add(models.HistoricalEvent(
                    event_type=event_type,
                    country=current_owner_country,
                    target_country=target_country,
                    system=system_model,
                    start_date_days=self._basic_info.date_in_days,
                    event_is_known_to_player=current_owner_country.has_met_player()
                                             or (target_country is not None and target_country.has_met_player()),
                ))


class DiplomacyProcessor(AbstractGamestateDataProcessor):
    ID = "diplomacy"
    DEPENDENCIES = [CountryProcessor.ID]

    def __init__(self):
        super().__init__()
        self.diplomacy_dict = None

    def initialize_data(self):
        self.diplomacy_dict = {}

    def data(self):
        return self.diplomacy_dict

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
        start_date = models.date_to_days(trade_deal.get("date", "2200.01.01"))
        end_date = start_date + 360 * trade_deal.get("length", 0)
        first_country_id = first["country"]
        second_country_id = second["country"]
        if second.get("sensor_link") == "yes":
            prev_start, prev_end = self.sensor_links[first_country_id].get(second_country_id, (float("inf"), -float("inf")))
            self.sensor_links[first_country_id][second_country_id] = (min(prev_start, start_date),
                                                                      max(prev_end, end_date))


class CountryDataProcessor(AbstractGamestateDataProcessor):
    ID = "country_data"
    DEPENDENCIES = [CountryProcessor.ID, DiplomacyProcessor.ID, SensorLinkProcessor.ID, SystemOwnershipProcessor.ID]

    def __init__(self):
        super().__init__()
        self.country_data_dict: Dict[int, models.CountryData] = None

    def initialize_data(self):
        self.country_data_dict = {}

    def data(self):
        return self.country_data_dict

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        sensor_links = dependencies[SensorLinkProcessor.ID]

        diplomacy_dict = dependencies[DiplomacyProcessor.ID]
        systems_by_country_id = dependencies[SystemOwnershipProcessor.ID]

        for country_id, country_model in countries_dict.items():
            country_data_dict = self._gamestate_dict["country"][country_id]

            has_sensor_link_with_player = (country_model.is_player
                                           or country_id in sensor_links[self._basic_info.player_country_id])
            if country_model.is_player:
                attitude_towards_player = models.Attitude.is_player
            else:
                attitude_towards_player = self._extract_ai_attitude_towards_player(country_id)

            diplomacy_data = self._get_diplomacy_towards_player(diplomacy_dict, country_id)

            tech_count = len(country_data_dict.get("tech_status", {}).get("technology", []))
            self.country_data_dict[country_id] = country_data = models.CountryData(
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

            if country_model.first_player_contact_date is None and diplomacy_data.get("has_communications_with_player"):
                country_model.first_player_contact_date = self._basic_info.date_in_days
                self._session.add(country_model)
            self._session.add(country_data)

    def _get_diplomacy_towards_player(self, diplomacy_dict, country_id):
        new_key_old_key_list = [
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
            result[new] = self._basic_info.player_country_id in diplomacy_dict[country_id][old]
        return result

    def _extract_ai_attitude_towards_player(self, country_id):
        attitude_towards_player = models.Attitude.unknown
        ai = self._gamestate_dict["country"][country_id].get("ai", {})
        if isinstance(ai, dict):
            attitudes = ai.get("attitude", [])
            for attitude in attitudes:
                if not isinstance(attitude, dict):
                    continue
                if attitude.get("country") == self._basic_info.player_country_id:
                    attitude_towards_player = attitude["attitude"]
                    break
            attitude_towards_player = models.Attitude.__members__.get(attitude_towards_player, models.Attitude.unknown)
        return attitude_towards_player

    def _extract_country_economy(self, country_data: models.CountryData, country_data_dict):
        budget_dict = country_data_dict.get("budget", {}).get("current_month", {}).get("balance", {})

        for item_name, values in budget_dict.items():
            if item_name == "none":
                continue
            if not values:
                continue
            energy = values.get("energy", 0.0)
            minerals = values.get("minerals", 0.0)
            alloys = values.get("alloys", 0.0)
            consumer_goods = values.get("consumer_goods", 0.0)
            food = values.get("food", 0.0)
            unity = values.get("unity", 0.0)
            influence = values.get("influence", 0.0)
            physics = values.get("physics_research", 0.0)
            society = values.get("society_research", 0.0)
            engineering = values.get("engineering_research", 0.0)

            country_data.net_energy += energy
            country_data.net_minerals += minerals
            country_data.net_alloys += alloys
            country_data.net_consumer_goods += consumer_goods
            country_data.net_food += food
            country_data.net_unity += unity
            country_data.net_influence += influence
            country_data.net_physics_research += physics
            country_data.net_society_research += society
            country_data.net_engineering_research += engineering

            if country_data.country.is_player:  # TODO add CONFIG setting to enable for non-player countries
                description = self._get_or_add_shared_description(item_name)
                self._session.add(models.BudgetItem(
                    country_data=country_data,
                    db_budget_item_name=description,
                    net_energy=energy,
                    net_minerals=minerals,
                    net_food=food,
                    net_alloys=alloys,
                    net_consumer_goods=consumer_goods,
                    net_unity=unity,
                    net_influence=influence,
                    net_volatile_motes=values.get("volatile_motes", 0.0),
                    net_exotic_gases=values.get("exotic_gases", 0.0),
                    net_rare_crystals=values.get("rare_crystals", 0.0),
                    net_living_metal=values.get("living_metal", 0.0),
                    net_zro=values.get("zro", 0.0),
                    net_dark_matter=values.get("dark_matter", 0.0),
                    net_nanites=values.get("nanites", 0.0),
                    net_physics_research=physics,
                    net_society_research=society,
                    net_engineering_research=engineering,
                ))
        self._session.add(country_data)  # update


class SpeciesProcessor(AbstractGamestateDataProcessor):
    ID = "species"
    DEPENDENCIES = []

    def __init__(self):
        super().__init__()

        self._species_by_ingame_id: Dict[int, models.Species] = None
        self._robot_species: Set[int] = None

    def initialize_data(self):
        self._species_by_ingame_id = {}
        self._robot_species = set()

    def data(self):
        return self._species_by_ingame_id, self._robot_species

    def extract_data_from_gamestate(self, dependencies):
        for species_index, species_dict in enumerate(self._gamestate_dict.get("species", [])):
            species_model = self._get_or_add_species(species_index)
            self._species_by_ingame_id[species_index] = species_model
            if species_dict.get("class") == "ROBOT":
                self._robot_species.add(species_index)

    def _get_or_add_species(self, species_id_in_game: int):
        species_data = self._gamestate_dict["species"][species_id_in_game]
        species_name = species_data.get("name", "Unnamed Species")
        species = self._session.query(models.Species).filter_by(
            game=self._db_game, species_id_in_game=species_id_in_game
        ).one_or_none()
        if species is None:
            species = models.Species(
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
                    self._session.add(models.SpeciesTrait(
                        db_name=self._get_or_add_shared_description(trait),
                        species=species,
                    ))
        return species


class LeaderProcessor(AbstractGamestateDataProcessor):
    ID = "leader"
    DEPENDENCIES = [CountryProcessor.ID, SpeciesProcessor.ID]

    def __init__(self):
        super().__init__()
        self.leader_model_by_ingame_id: Dict[int, models.Leader] = None
        self._species_dict: Dict[int, models.Species] = None
        self._random_instance = random.Random()

    def initialize_data(self):
        self.leader_model_by_ingame_id = {}
        self._random_instance.seed(self._basic_info.game_id)

    def data(self):
        return self.leader_model_by_ingame_id

    def extract_data_from_gamestate(self, dependencies):
        countries = dependencies[CountryProcessor.ID]
        self._species_dict, _ = dependencies[SpeciesProcessor.ID]

        db_active_leaders = {leader.leader_id_in_game: leader
                             for leader in self._session.query(models.Leader).filter_by(is_active=True)}

        self._check_known_leaders(db_active_leaders)
        self._check_new_leaders(countries, db_active_leaders)

    def _check_known_leaders(self, db_active_leaders: Dict[int, models.Leader]):
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
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.leader_died,
                    country=leader.country,
                    leader=leader,
                    start_date_days=self._basic_info.date_in_days,
                    event_is_known_to_player=(country_data is not None
                                              and country_data.attitude_towards_player.reveals_economy_info()),
                ))
                self._session.add(leader)

    def _check_new_leaders(self, countries: Dict[int, models.Country], db_active_leaders: Dict[int, models.Leader]):
        gs_leaders = self._gamestate_dict.get("leaders")

        for country_id, country_model in countries.items():
            country_data_dict = self._gamestate_dict["country"][country_id]
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
                    logging.info("Failed to add leader %d, %s", leader_id, leader_dict)
                    continue
                self.leader_model_by_ingame_id[leader_id] = leader

    def _add_new_leader(self, country_model: models.Country, leader_id: int, leader_dict: Dict) -> Optional[models.Leader]:
        date_hired = min(
            self._basic_info.date_in_days,
            models.date_to_days(leader_dict.get("date", "10000.01.01")),
            models.date_to_days(leader_dict.get("start", "10000.01.01")),
            models.date_to_days(leader_dict.get("date_added", "10000.01.01")),
        )
        date_born = date_hired - 360 * leader_dict.get("age", 0.0) + self._random_instance.randint(-15, 15)
        leader = models.Leader(
            country=country_model,
            leader_id_in_game=leader_id,
            game=self._db_game,
            last_level=leader_dict.get("level", 0),
            date_hired=date_hired,
            date_born=date_born,
            is_active=True,
        )
        self._update_leader_attributes(leader=leader, leader_dict=leader_dict)  # sets additional attributes
        country_data = country_model.get_most_recent_data()
        event = models.HistoricalEvent(
            event_type=models.HistoricalEventType.leader_recruited,
            country=country_model,
            leader=leader,
            start_date_days=date_hired,
            end_date_days=self._basic_info.date_in_days,
            event_is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_economy_info(),
        )
        self._session.add(event)
        return leader

    def get_leader_name(self, leader_dict):
        first_name = leader_dict['name']['first_name']
        last_name = leader_dict['name'].get('second_name', "")
        leader_name = f"{first_name} {last_name}".strip()
        return leader_name

    def _update_leader_attributes(self, leader: models.Leader, leader_dict):
        if "pre_ruler_class" in leader_dict:
            leader_class = leader_dict.get("pre_ruler_class", "Unknown class")
        else:
            leader_class = leader_dict.get("class", "Unknown class")
        leader_gender = leader_dict.get("gender", "Other")
        leader_agenda = leader_dict.get("agenda", "Unknown")
        leader_name = self.get_leader_name(leader_dict)
        level = leader_dict.get("level", -1)
        species_id = leader_dict.get("species_index", -1)
        leader_species = self._species_dict.get(species_id)
        if leader_species is None:
            logger.warning(f"{self._basic_info.logger_str} Invalid species index for leader {leader_dict}")
        if (leader.leader_name != leader_name
                or leader.leader_class != leader_class
                or leader.gender != leader_gender
                or leader.leader_agenda != leader_agenda
                or leader.species.species_id_in_game != leader_species.species_id_in_game):
            if leader.last_level != level:
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.level_up,
                    country=leader.country,
                    start_date_days=self._basic_info.date_in_days,
                    leader=leader,
                    event_is_known_to_player=leader.country.is_player,
                    db_description=self._get_or_add_shared_description(str(level)),
                ))
            leader.last_level = level
            leader.leader_name = leader_name
            leader.leader_class = leader_class
            leader.gender = leader_gender
            leader.leader_agenda = leader_agenda
            leader.species = leader_species
            self._session.add(leader)


class PlanetModelProcessor(AbstractGamestateDataProcessor):
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
        self.planets_by_ingame_id = {p.planet_id_in_game: p for p in self._session.query(models.Planet)}
        systems_by_id = dependencies[SystemProcessor.ID]

        for system_id, system_dict in self._gamestate_dict["galactic_object"].items():
            planets = system_dict.get("planet", [])
            if isinstance(planets, int):
                planets = [planets]
            for ingame_id in planets:
                if ingame_id in self.planets_by_ingame_id:
                    continue
                system_model = systems_by_id.get(system_id)
                self.planets_by_ingame_id[ingame_id] = self._add_planet_model(
                    planet_id=ingame_id,
                    system_model=system_model
                )

    def _add_planet_model(self, system_model: models.System,
                          planet_id: int) -> models.Planet:
        planet_dict = self._gamestate_dict["planets"]["planet"].get(planet_id)
        planet_class = planet_dict.get("planet_class")
        planet_name = planet_dict.get("name")
        colonize_date = planet_dict.get("colonize_date")
        if colonize_date:
            colonize_date = models.date_to_days(colonize_date)
        planet_model = models.Planet(
            planet_name=planet_name,
            planet_id_in_game=planet_id,
            system=system_model,
            planet_class=planet_class,
            colonized_date=colonize_date,
        )
        self._session.add(planet_model)
        return planet_model


class SectorColonyEventProcessor(AbstractGamestateDataProcessor):
    ID = "sectors_colonies"
    DEPENDENCIES = [SystemProcessor.ID, PlanetModelProcessor.ID, CountryProcessor.ID, LeaderProcessor.ID]

    def __init__(self):
        super().__init__()
        self._planets_dict = None
        self._systems_dict = None
        self._countries_dict = None
        self._leaders_dict = None

    def extract_data_from_gamestate(self, dependencies):
        self._planets_dict = dependencies[PlanetModelProcessor.ID]
        self._countries_dict = dependencies[CountryProcessor.ID]
        self._systems_dict = dependencies[SystemProcessor.ID]
        self._leaders_dict = dependencies[LeaderProcessor.ID]

        for country_id, country_model in self._countries_dict.items():
            country_dict = self._gamestate_dict["country"][country_id]
            country_sectors = country_dict.get("sectors", {}).get("owned", [])
            # processing all colonies by sector allows reading the responsible sector governor
            for sector_id in country_sectors:
                sector_info = self._gamestate_dict["sectors"].get(sector_id)
                if not isinstance(sector_info, dict):
                    continue
                sector_description = self._get_or_add_shared_description(text=(sector_info.get("name", "Unnamed")))

                governor_model = self._leaders_dict.get(sector_info.get("governor"))

                self._history_add_planetary_events_within_sector(country_model, sector_info, governor_model)

                sector_capital = self._session.query(models.Planet).filter_by(
                    planet_id_in_game=sector_info.get("local_capital")
                ).one_or_none()
                if governor_model is not None and sector_capital is not None:
                    self._history_add_or_update_governor_sector_events(country_model, sector_capital, governor_model, sector_description)

    def _history_add_planetary_events_within_sector(self, country_model: models.Country, sector_dict, governor: models.Leader):
        for system_id in sector_dict.get("systems", []):
            system_model = self._systems_dict.get(system_id)
            if system_model is None:
                logger.info(f"{self._basic_info.logger_str}     Could not find system with in-game id {system_id}")
                continue

            system_dict = self._gamestate_dict["galactic_object"].get(system_id, {})

            planets = system_dict.get("planet", [])
            if not isinstance(planets, list):
                planets = [planets]
            for planet_id in planets:
                planet_dict = self._gamestate_dict["planets"]["planet"].get(planet_id)
                if not isinstance(planet_dict, dict):
                    continue

                planet_class = planet_dict.get("planet_class")
                is_colonizable = game_info.is_colonizable_planet(planet_class) or "colonize_date" in planet_dict
                is_destroyed = game_info.is_destroyed_planet(planet_class)
                is_terraformable = is_colonizable or any(m == "terraforming_candidate" for (m, _) in self._all_planetary_modifiers(planet_dict))

                planet_model = self._planets_dict.get(planet_id)
                if planet_model is None:
                    continue
                if is_colonizable:
                    self._history_add_or_update_colonization_events(country_model, system_model, planet_model, planet_dict, governor)
                if is_terraformable:
                    self._history_add_or_update_terraforming_events(country_model, system_model, planet_model, planet_dict, governor)
                if is_destroyed and planet_class != planet_model.planet_class:
                    self._session.add(models.HistoricalEvent(
                        event_type=models.HistoricalEventType.planet_destroyed,
                        country=country_model,
                        start_date_days=min(self._basic_info.date_in_days),
                        planet=planet_model,
                        system=system_model,
                        event_is_known_to_player=country_model.has_met_player(),
                    ))

    def _history_add_or_update_colonization_events(self, country_model: models.Country,
                                                   system_model: models.System,
                                                   planet_model: models.Planet,
                                                   planet_dict,
                                                   governor: models.Leader):
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
        if not isinstance(colonization_end_date, str) or colonization_end_date == "none":
            end_date_days = self._basic_info.date_in_days
        else:
            end_date_days = models.date_to_days(colonization_end_date)

        if planet_model.colonized_date is not None:
            # abort early if the planet is already added and known to be fully colonized
            return
        elif colonization_completed:
            # set the planet's colonization flag and allow updating the event one last time
            planet_model.colonized_date = colonization_end_date
            self._session.add(planet_model)
        event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.colonization,
            planet=planet_model,
        ).one_or_none()
        if event is None:
            event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.colonization,
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

    def _history_add_or_update_terraforming_events(self, country_model: models.Country,
                                                   system_model: models.System,
                                                   planet_model: models.Planet,
                                                   planet_dict,
                                                   governor: models.Leader):
        terraform_dict = planet_dict.get("terraform_process")
        if not isinstance(terraform_dict, dict):
            return

        current_pc = planet_dict.get("planet_class")
        target_pc = terraform_dict.get("planet_class")
        if not isinstance(target_pc, str):
            logger.info(f"{self._basic_info.logger_str} Unexpected target planet class for terraforming of {planet_model.planet_name}: From {planet_model.planet_class} to {target_pc}")
            return
        text = f"{current_pc},{target_pc}"
        matching_description = self._get_or_add_shared_description(text)
        matching_event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.terraforming,
            db_description=matching_description,
            system=system_model,
            planet=planet_model,
        ).order_by(models.HistoricalEvent.start_date_days.desc()).first()
        if matching_event is None or matching_event.end_date_days < self._basic_info.date_in_days - 5 * 360:
            matching_event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.terraforming,
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
            matching_event.end_date_days = self._basic_info.date_in_days
        self._session.add(matching_event)

    def _all_planetary_modifiers(self, planet_dict):
        modifiers = planet_dict.get("timed_modifiers", [])
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

    def _history_add_or_update_governor_sector_events(self, country_model,
                                                      sector_capital: models.Planet,
                                                      governor: models.Leader,
                                                      sector_description: models.SharedDescription):
        # check if governor was ruling same sector before => update date and return
        event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.governed_sector,
            db_description=sector_description,
        ).order_by(models.HistoricalEvent.end_date_days.desc()).first()
        if (event is not None
                and event.leader == governor
                and event.end_date_days > self._basic_info.date_in_days - 5 * 360):  # if the governor ruled this sector less than 5 years ago, re-use the event...
            event.end_date_days = self._basic_info.date_in_days
        else:
            country_data = country_model.get_most_recent_data()
            event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.governed_sector,
                leader=governor,
                country=country_model,
                db_description=sector_description,
                start_date_days=self._basic_info.date_in_days,
                end_date_days=self._basic_info.date_in_days,
                event_is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_economy_info(),
            )

        if event.planet is None and sector_capital is not None:
            event.planet = sector_capital
            event.system = sector_capital.system
        self._session.add(event)


class PlanetUpdateProcessor(AbstractGamestateDataProcessor):
    ID = "planet_updates"
    DEPENDENCIES = [PlanetModelProcessor.ID, SectorColonyEventProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        planet_models = dependencies[PlanetModelProcessor.ID]
        for ingame_id, planet_model in planet_models.items():
            planet_dict = self._gamestate_dict["planets"]["planet"].get(ingame_id, {})
            self._update_planet_model(planet_dict, planet_model)

    def _update_planet_model(self, planet_dict: Dict,
                             planet_model: models.Planet):
        planet_class = planet_dict.get("planet_class")
        planet_name = planet_dict.get("name")
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
        PlanetModelProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
        self.ruler_by_country_id: Dict[int, models.Leader] = None
        self.planet_by_ingame_id: Dict[int, models.Planet] = None

    def initialize_data(self):
        self.ruler_by_country_id = {}

    def data(self) -> Dict[int, Optional[models.Leader]]:
        return self.ruler_by_country_id

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        leader_by_ingame_id = dependencies.get(LeaderProcessor.ID)
        self.planet_by_ingame_id = dependencies.get(PlanetModelProcessor.ID)

        for country_id, country_model in countries_dict.items():
            country_dict = self._gamestate_dict["country"][country_id]
            if not isinstance(country_dict, dict):
                return None
            ruler_id = country_dict.get("ruler")
            if ruler_id is None and country_model.country_type in TimelineExtractor.SUPPORTED_COUNTRY_TYPES:
                logger.info(f"{self._basic_info.logger_str}         Country {country_dict['name']} has no ruler ID")
            ruler = leader_by_ingame_id.get(ruler_id)

            self.ruler_by_country_id[country_id] = ruler
            if ruler is not None:
                capital_planet = self._history_add_or_update_capital(country_model, ruler, country_dict)
                self._history_add_or_update_ruler(ruler, country_model, capital_planet)
            self._history_extract_tradition_events(ruler, country_model, country_dict)
            self._history_extract_ascension_events(ruler, country_model, country_dict)
            self._history_extract_edict_events(ruler, country_model, country_dict)

    def _history_add_or_update_capital(self, country_model: models.Country,
                                       ruler: models.Leader,
                                       country_dict) -> Optional[models.Planet]:
        capital_id = country_dict.get("capital")
        if not isinstance(capital_id, int):
            return
        capital = self.planet_by_ingame_id.get(capital_id)
        if capital is None:
            return
        capital_event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.capital_relocation,
            country=country_model,
        ).order_by(
            models.HistoricalEvent.start_date_days.desc()
        ).first()
        if (capital_event is None
                or (capital_event.planet is None and capital is not None)
                or capital_event.planet.planet_id != capital.planet_id):
            self._session.add(models.HistoricalEvent(
                event_type=models.HistoricalEventType.capital_relocation,
                country=country_model,
                leader=ruler,
                start_date_days=self._basic_info.date_in_days,
                planet=capital,
                system=capital.system if capital else None,
                event_is_known_to_player=country_model.has_met_player(),
            ))
        return capital

    def _history_add_or_update_ruler(self, ruler: models.Leader, country_model: models.Country, capital_planet: models.Planet):
        most_recent_ruler_event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.ruled_empire,
            country=country_model,
            leader=ruler,
        ).order_by(
            models.HistoricalEvent.start_date_days.desc()
        ).first()
        capital_system = capital_planet.system if capital_planet else None
        if most_recent_ruler_event is None:
            start_date = self._basic_info.date_in_days
            if start_date < 100:
                start_date = 0
            most_recent_ruler_event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.ruled_empire,
                country=country_model,
                leader=ruler,
                start_date_days=start_date,
                planet=capital_planet,
                system=capital_system,
                end_date_days=self._basic_info.date_in_days,
                event_is_known_to_player=country_model.has_met_player(),
            )
        else:
            most_recent_ruler_event.end_date_days = self._basic_info.date_in_days - 1
            most_recent_ruler_event.is_known_to_player = country_model.has_met_player()
        self._session.add(most_recent_ruler_event)

    def _history_extract_tradition_events(self, ruler: models.Leader, country_model: models.Country, country_dict):
        for tradition in country_dict.get("traditions", []):
            matching_description = self._get_or_add_shared_description(text=tradition)
            matching_event = self._session.query(models.HistoricalEvent).filter_by(
                country=country_model,
                event_type=models.HistoricalEventType.tradition,
                db_description=matching_description,
            ).one_or_none()
            if matching_event is None:
                country_data = country_model.get_most_recent_data()
                self._session.add(models.HistoricalEvent(
                    leader=ruler,
                    country=country_model,
                    event_type=models.HistoricalEventType.tradition,
                    start_date_days=self._basic_info.date_in_days,
                    end_date_days=self._basic_info.date_in_days,
                    db_description=matching_description,
                    event_is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_economy_info(),
                ))

    def _history_extract_ascension_events(self, ruler: models.Leader, country_model: models.Country, country_dict):
        for ascension_perk in country_dict.get("ascension_perks", []):
            matching_description = self._get_or_add_shared_description(text=ascension_perk)
            matching_event = self._session.query(models.HistoricalEvent).filter_by(
                country=country_model,
                event_type=models.HistoricalEventType.ascension_perk,
                db_description=matching_description,
            ).one_or_none()
            if matching_event is None:
                self._session.add(models.HistoricalEvent(
                    leader=ruler,
                    country=country_model,
                    event_type=models.HistoricalEventType.ascension_perk,
                    start_date_days=self._basic_info.date_in_days,
                    end_date_days=self._basic_info.date_in_days,
                    db_description=matching_description,
                    event_is_known_to_player=country_model.has_met_player(),
                ))

    def _history_extract_edict_events(self, ruler: models.Leader, country_model: models.Country, country_dict):
        edict_list = country_dict.get("edicts", [])
        if not isinstance(edict_list, list):
            edict_list = [edict_list]
        for edict in edict_list:
            if not isinstance(edict, dict):
                continue
            expiry_date = models.date_to_days(edict.get("date"))
            description = self._get_or_add_shared_description(text=(edict.get("edict")))
            matching_event = self._session.query(
                models.HistoricalEvent
            ).filter_by(
                event_type=models.HistoricalEventType.edict,
                country=country_model,
                db_description=description,
                end_date_days=expiry_date,
            ).one_or_none()
            if matching_event is None:
                country_data = country_model.get_most_recent_data()
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.edict,
                    country=country_model,
                    leader=ruler,
                    db_description=description,
                    start_date_days=self._basic_info.date_in_days,
                    end_date_days=expiry_date,
                    event_is_known_to_player=country_data is not None and country_data.attitude_towards_player.reveals_economy_info(),
                ))


class GovernmentProcessor(AbstractGamestateDataProcessor):
    ID = "government"
    DEPENDENCIES = [CountryProcessor.ID, RulerEventProcessor.ID]

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        rulers_dict = dependencies[RulerEventProcessor.ID]

        for country_id, country_model in countries_dict.items():
            country_dict = self._gamestate_dict["country"][country_id]
            gov_name = country_dict.get("name", "Unnamed Country")
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

            prev_gov = self._session.query(models.Government).filter(
                models.Government.start_date_days <= self._basic_info.date_in_days,
            ).filter_by(
                country=country_model,
            ).order_by(
                models.Government.start_date_days.desc()
            ).first()

            if prev_gov is not None:
                prev_gov.end_date_days = self._basic_info.date_in_days - 1
                self._session.add(prev_gov)
                previous_ethics = [prev_gov.ethics_1, prev_gov.ethics_2, prev_gov.ethics_3, prev_gov.ethics_4, prev_gov.ethics_5]
                previous_ethics = set(previous_ethics) - {None}
                previous_civics = [prev_gov.civic_1, prev_gov.civic_2, prev_gov.civic_3, prev_gov.civic_4, prev_gov.civic_5]
                previous_civics = set(previous_civics) - {None}
                gov_was_reformed = ((ethics != previous_ethics)
                                    or (civics != previous_civics)
                                    or (gov_name != prev_gov.gov_name)
                                    or (gov_type != prev_gov.gov_type))
                # nothing has changed...
                if not gov_was_reformed:
                    continue

            ethics = dict(zip([f"ethics_{i}" for i in range(1, 6)], sorted(ethics)))
            civics = dict(zip([f"civic_{i}" for i in range(1, 6)], sorted(civics)))

            gov = models.Government(
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
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.government_reform,
                    country=country_model,
                    leader=rulers_dict.get(country_id),
                    start_date_days=self._basic_info.date_in_days,
                    end_date_days=self._basic_info.date_in_days,
                    event_is_known_to_player=country_model.has_met_player(),
                ))


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
        self.faction_by_ingame_id: Dict[int, models.Leader] = None
        self._leaders_dict = None

    def initialize_data(self):
        self.faction_by_ingame_id = {}

    def data(self):
        return self.faction_by_ingame_id

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        self._leaders_dict = dependencies[LeaderProcessor.ID]

        for faction_id, faction_dict in self._gamestate_dict.get("pop_factions", {}).items():
            if not faction_dict or not isinstance(faction_dict, dict):
                continue
            country_model = countries_dict.get(faction_dict.get("country"))
            if country_model is None:
                continue
            faction_name = faction_dict.get("name", "Unnamed faction")
            # If the faction is in the database, get it, otherwise add a new faction
            faction_model = self._get_or_add_faction(
                faction_id_in_game=faction_id,
                faction_name=faction_name,
                country_model=country_model,
                faction_type=faction_dict.get("type"),
            )
            self._history_add_or_update_faction_leader_event(country_model, faction_model, faction_dict)

        for country_id, country_model in countries_dict.items():
            for faction_name, faction_id in self.NO_FACTION_ID_MAP.items():
                self.faction_by_ingame_id[faction_id] = self._get_or_add_faction(
                    faction_id_in_game=faction_id,
                    faction_name=faction_name,
                    country_model=country_model,
                    faction_type=FactionProcessor.NO_FACTION_POP_ETHICS[faction_name],
                )

    def _get_or_add_faction(self, faction_id_in_game: int,
                            faction_name: str,
                            country_model: models.Country,
                            faction_type: str):
        faction = self._session.query(models.PoliticalFaction).filter_by(
            faction_id_in_game=faction_id_in_game,
            country=country_model,
        ).one_or_none()
        if faction is None:
            faction = models.PoliticalFaction(
                country=country_model,
                faction_name=faction_name,
                faction_id_in_game=faction_id_in_game,
                db_faction_type=self._get_or_add_shared_description(faction_type),
            )
            self._session.add(faction)
            if faction_id_in_game not in FactionProcessor.NO_FACTION_ID_MAP.values():
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.new_faction,
                    country=country_model,
                    faction=faction,
                    start_date_days=self._basic_info.date_in_days,
                    end_date_days=self._basic_info.date_in_days,
                    event_is_known_to_player=country_model.has_met_player(),
                ))
        return faction

    def _history_add_or_update_faction_leader_event(self, country_model: models.Country,
                                                    faction_model: models.PoliticalFaction,
                                                    faction_dict):
        faction_leader_id = faction_dict.get("leader", -1)
        leader = self._leaders_dict.get(faction_leader_id)
        if leader is None:
            logger.debug(f"{self._basic_info.logger_str}     Could not find faction leader matching leader id {faction_leader_id} for {country_model.country_name}")
            logger.debug(f"{self._basic_info.logger_str}     {faction_dict}")
            return
        matching_event = self._session.query(models.HistoricalEvent).filter_by(
            country=country_model,
            leader=leader,
            event_type=models.HistoricalEventType.faction_leader,
            faction=faction_model,
        ).one_or_none()
        is_known = country_model.has_met_player()
        if matching_event is None:
            matching_event = models.HistoricalEvent(
                country=country_model,
                leader=leader,
                event_type=models.HistoricalEventType.faction_leader,
                faction=faction_model,
                start_date_days=self._basic_info.date_in_days,
                end_date_days=self._basic_info.date_in_days,
                event_is_known_to_player=is_known,
            )
        else:
            matching_event.is_known_to_player = is_known
            matching_event.end_date_days = self._basic_info.date_in_days
        self._session.add(matching_event)


class DiplomacyHistoricalEventProcessor(AbstractGamestateDataProcessor):
    ID = "diplomacy_events"
    DEPENDENCIES = [DiplomacyProcessor.ID, CountryProcessor.ID, RulerEventProcessor.ID]

    def extract_data_from_gamestate(self, dependencies):
        diplo_dict = dependencies[DiplomacyProcessor.ID]
        country_dict = dependencies[CountryProcessor.ID]
        ruler_dict = dependencies[RulerEventProcessor.ID]

        for country_id, country_model in country_dict.items():
            if country_model.country_type not in TimelineExtractor.SUPPORTED_COUNTRY_TYPES:
                continue
            diplo_relations = [
                (
                    models.HistoricalEventType.sent_rivalry,
                    models.HistoricalEventType.received_rivalry,
                    "rivalries",
                ),
                (
                    models.HistoricalEventType.closed_borders,
                    models.HistoricalEventType.received_closed_borders,
                    "closed_borders",
                ),
                (
                    models.HistoricalEventType.defensive_pact,
                    models.HistoricalEventType.defensive_pact,
                    "defensive_pacts",
                ),
                (
                    models.HistoricalEventType.formed_federation,
                    models.HistoricalEventType.formed_federation,
                    "federations",
                ),
                (
                    models.HistoricalEventType.non_aggression_pact,
                    models.HistoricalEventType.non_aggression_pact,
                    "non_aggression_pacts",
                ),
                (
                    models.HistoricalEventType.first_contact,
                    models.HistoricalEventType.first_contact,
                    "communations",
                ),
                (
                    models.HistoricalEventType.commercial_pact,
                    models.HistoricalEventType.commercial_pact,
                    "commercial_pacts",
                ),
                (
                    models.HistoricalEventType.research_agreement,
                    models.HistoricalEventType.research_agreement,
                    "research_agreements",
                ),
                (
                    models.HistoricalEventType.migration_treaty,
                    models.HistoricalEventType.migration_treaty,
                    "migration_treaties",
                ),
            ]
            for event_type, reverse_event_type, diplo_dict_key in diplo_relations:
                for target_country_id in diplo_dict[country_id][diplo_dict_key]:
                    target_country_model = country_dict.get(target_country_id)
                    if target_country_model.country_type not in TimelineExtractor.SUPPORTED_COUNTRY_TYPES:
                        continue
                    if target_country_model is None:
                        continue
                    is_known_to_player = (country_model.is_player or target_country_model.is_player
                                          or (country_model.has_met_player() and target_country_model.has_met_player()))
                    country_tuples = [
                        (event_type, country_model, target_country_model, ruler_dict.get(country_id)),
                        (reverse_event_type, target_country_model, country_model, ruler_dict.get(target_country_id))
                    ]
                    for (et, c_model, tc_model, c_ruler) in country_tuples:
                        matching_event = self._session.query(models.HistoricalEvent).filter_by(
                            event_type=et,
                            country=c_model,
                            target_country=tc_model,
                        ).order_by(models.HistoricalEvent.start_date_days.desc()).first()

                        if matching_event is None or matching_event.end_date_days < self._basic_info.date_in_days - 9 * 360:
                            matching_event = models.HistoricalEvent(
                                event_type=et,
                                country=c_model,
                                target_country=tc_model,
                                leader=c_ruler,
                                start_date_days=self._basic_info.date_in_days,
                                end_date_days=self._basic_info.date_in_days,
                                event_is_known_to_player=is_known_to_player,
                            )
                        else:
                            matching_event.end_date_days = self._basic_info.date_in_days
                            matching_event.is_known_to_player = is_known_to_player
                        self._session.add(matching_event)


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
            self._history_add_tech_events(country_model, self._gamestate_dict["country"][country_id])

    def _history_add_tech_events(self, country_model: models.Country, country_dict):
        tech_status_dict = country_dict.get("tech_status")
        if not isinstance(tech_status_dict, dict):
            return
        for tech_type in ["physics", "society", "engineering"]:
            scientist_id = tech_status_dict.get("leaders", {}).get(tech_type)
            scientist = self._leader_dict.get(scientist_id)
            self.history_add_research_leader_events(country_model, scientist, tech_type)

            progress_dict = tech_status_dict.get(f"{tech_type}_queue")
            if progress_dict and isinstance(progress_dict, list):
                progress_dict = progress_dict[0]
            if not isinstance(progress_dict, dict):
                continue
            tech_name = progress_dict.get("technology")
            if not isinstance(tech_name, str):
                continue
            matching_description = self._get_or_add_shared_description(text=tech_name)
            # TODO CHECK IF THIS WORKS FOR REPEATABLE TECH
            matching_event = self._session.query(models.HistoricalEvent).filter_by(
                event_type=models.HistoricalEventType.researched_technology,
                country=country_model,
                db_description=matching_description,
            ).one_or_none()
            if matching_event is None:
                date_str = progress_dict.get("date")
                start_date = models.date_to_days(date_str) if date_str else self._basic_info.date_in_days
                matching_event = models.HistoricalEvent(
                    event_type=models.HistoricalEventType.researched_technology,
                    country=country_model,
                    leader=scientist,
                    start_date_days=start_date,
                    end_date_days=self._basic_info.date_in_days,
                    db_description=matching_description,
                    event_is_known_to_player=country_model.has_met_player(),
                )
            else:
                matching_event.end_date_days = self._basic_info.date_in_days
            self._session.add(matching_event)

    def history_add_research_leader_events(self, country_model: models.Country,
                                           scientist: models.Leader,
                                           tech_type: str):
        """ Record which scientist was in charge of leading research for a given tech type. """
        if scientist is None:
            return

        description = self._get_or_add_shared_description(text=tech_type.capitalize())
        matching_event = self._session.query(models.HistoricalEvent).filter_by(
            event_type=models.HistoricalEventType.research_leader,
            country=country_model,
            db_description=description,
        ).order_by(models.HistoricalEvent.start_date_days.desc()).first()
        if matching_event is None:
            is_known_to_player = country_model.has_met_player()
            new_event = models.HistoricalEvent(
                event_type=models.HistoricalEventType.research_leader,
                country=country_model,
                leader=scientist,
                start_date_days=self._basic_info.date_in_days,
                end_date_days=self._basic_info.date_in_days,
                db_description=description,
                event_is_known_to_player=is_known_to_player,
            )
            self._session.add(new_event)
        elif matching_event.leader == scientist:
            matching_event.end_date_days = self._basic_info.date_in_days
            self._session.add(matching_event)


class WarProcessor(AbstractGamestateDataProcessor):
    ID = "wars"
    DEPENDENCIES = [
        RulerEventProcessor.ID,
        CountryProcessor.ID,
        SystemProcessor.ID,
        PlanetModelProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
        self._ruler_dict = None
        self._countries_dict = None
        self._system_models_dict = None
        self._planet_models_dict = None

    def extract_data_from_gamestate(self, dependencies):
        self._ruler_dict = dependencies[RulerEventProcessor.ID]
        self._countries_dict = dependencies[CountryProcessor.ID]
        self._system_models_dict = dependencies[SystemProcessor.ID]
        self._planet_models_dict = dependencies[PlanetModelProcessor.ID]

        wars_dict = self._gamestate_dict.get("war")
        if not wars_dict:
            return

        for war_id, war_dict in wars_dict.items():
            if not isinstance(war_dict, dict):
                continue
            war_name = war_dict.get("name", "Unnamed war")
            war_model = self._session.query(models.War).order_by(models.War.start_date_days.desc()).filter_by(
                game=self._db_game, name=war_name
            ).first()
            if war_model is None or (war_model.outcome != models.WarOutcome.in_progress
                                     and war_model.end_date_days < self._basic_info.date_in_days - 5 * 360):
                start_date_days = models.date_to_days(war_dict["start_date"])
                war_model = models.War(
                    war_id_in_game=war_id,
                    game=self._db_game,
                    start_date_days=start_date_days,
                    end_date_days=self._basic_info.date_in_days,
                    name=war_name,
                    outcome=models.WarOutcome.in_progress,
                )
            elif war_dict.get("defender_force_peace") == "yes":
                war_model.outcome = models.WarOutcome.status_quo
                war_model.end_date_days = models.date_to_days(war_dict.get("defender_force_peace_date"))
            elif war_dict.get("attacker_force_peace") == "yes":
                war_model.outcome = models.WarOutcome.status_quo
                war_model.end_date_days = models.date_to_days(war_dict.get("attacker_force_peace_date"))
            elif war_model.outcome != models.WarOutcome.in_progress:
                continue
            else:
                war_model.end_date_days = self._basic_info.date_in_days
            self._session.add(war_model)
            war_goal_attacker = war_dict.get("attacker_war_goal", {}).get("type")
            war_goal_defender = war_dict.get("defender_war_goal", {})
            if isinstance(war_goal_defender, dict):
                war_goal_defender = war_goal_defender.get("type")
            elif not war_goal_defender or war_goal_defender == "none":
                war_goal_defender = None

            attackers = {p["country"] for p in war_dict["attackers"]}
            for war_party_info in itertools.chain(war_dict["attackers"], war_dict["defenders"]):
                if not isinstance(war_party_info, dict):
                    continue  # just in case
                country_id_ingame = war_party_info.get("country")
                db_country = self._session.query(models.Country).filter_by(game=self._db_game, country_id_in_game=country_id_ingame).one_or_none()

                country_dict = self._gamestate_dict["country"][country_id_ingame]
                if db_country is None:
                    country_name = country_dict["name"]
                    logger.warning(f"{self._basic_info.logger_str}     Could not find country matching war participant {country_name}")
                    continue

                is_attacker = country_id_ingame in attackers

                war_participant = self._session.query(models.WarParticipant).filter_by(
                    war=war_model, country=db_country
                ).one_or_none()
                if war_participant is None:
                    war_goal = war_goal_attacker if is_attacker else war_goal_defender
                    war_participant = models.WarParticipant(
                        war=war_model,
                        war_goal=war_goal,
                        country=db_country,
                        is_attacker=is_attacker,
                    )
                    self._session.add(models.HistoricalEvent(
                        event_type=models.HistoricalEventType.war,
                        country=war_participant.country,
                        leader=self._ruler_dict.get(country_id_ingame),
                        start_date_days=self._basic_info.date_in_days,
                        end_date_days=self._basic_info.date_in_days,
                        war=war_model,
                        event_is_known_to_player=war_participant.country.has_met_player(),
                    ))
                if war_participant.war_goal is None:
                    war_participant.war_goal = war_goal_defender
                self._session.add(war_participant)

            self._extract_combat_victories(war_dict, war_model)

    def _extract_combat_victories(self, war_dict, war: models.War):

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

            combat_type = models.CombatType.__members__.get(battle_dict.get("type"), models.CombatType.other)

            date_str = battle_dict.get("date")
            date_in_days = models.date_to_days(date_str)
            if date_in_days < 0:
                date_in_days = self._basic_info.date_in_days

            attacker_exhaustion = battle_dict.get("attacker_war_exhaustion", 0.0)
            defender_exhaustion = battle_dict.get("defender_war_exhaustion", 0.0)
            if defender_exhaustion + attacker_exhaustion <= 0.001 and combat_type != models.CombatType.armies:
                continue
            combat = self._session.query(models.Combat).filter_by(
                war=war,
                system=system if system is not None else planet_model.system,
                planet=planet_model,
                combat_type=combat_type,
                attacker_victory=attacker_victory,
                attacker_war_exhaustion=attacker_exhaustion,
                defender_war_exhaustion=defender_exhaustion,
            ).order_by(models.Combat.date.desc()).first()

            if combat is not None:
                continue

            combat = models.Combat(
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
                    logger.warning(f"{self._basic_info.logger_str}     Could not find country with ID {country_id} when processing battle {battle_dict}")
                    continue
                is_known_to_player = is_known_to_player or db_country.has_met_player()
                war_participant = self._session.query(models.WarParticipant).filter_by(
                    war=war,
                    country=db_country,
                ).one_or_none()
                if war_participant is None:
                    logger.info(f"{self._basic_info.logger_str}     Could not find War participant matching country {db_country.country_name} and war {war.name}.")
                    continue
                self._session.add(models.CombatParticipant(
                    combat=combat, war_participant=war_participant, is_attacker=country_id in battle_attackers,
                ))

            event_type = models.HistoricalEventType.army_combat if combat_type == models.CombatType.armies else models.HistoricalEventType.fleet_combat
            self._session.add(models.HistoricalEvent(
                event_type=event_type,
                system=system,
                planet=planet_model,
                war=war,
                start_date_days=date_in_days,
                event_is_known_to_player=is_known_to_player,
            ))

    def _settle_finished_wars(self):
        truces_dict = self._gamestate_dict.get("truce", {})
        if not isinstance(truces_dict, dict):
            return
        #  resolve wars based on truces...
        for truce_id, truce_info in truces_dict.items():
            if not isinstance(truce_info, dict):
                continue
            war_name = truce_info.get("name")
            truce_type = truce_info.get("truce_type", "other")
            if not war_name or truce_type != "war":
                continue  # truce is due to diplomatic agreements or similar
            matching_war = self._session.query(models.War).order_by(models.War.start_date_days.desc()).filter_by(name=war_name).first()
            if matching_war is None:
                continue
            end_date = truce_info.get("start_date")  # start of truce => end of war
            if isinstance(end_date, str) and end_date != None:
                matching_war.end_date_days = models.date_to_days(end_date)

            if matching_war.outcome == models.WarOutcome.in_progress:
                if matching_war.attacker_war_exhaustion < matching_war.defender_war_exhaustion:
                    matching_war.outcome = models.WarOutcome.attacker_victory
                elif matching_war.defender_war_exhaustion < matching_war.attacker_war_exhaustion:
                    matching_war.outcome = models.WarOutcome.defender_victory
                else:
                    matching_war.outcome = models.WarOutcome.status_quo
                self._history_add_peace_events(matching_war)
            self._session.add(matching_war)
        #  resolve wars that are no longer in the save files...
        for war in self._session.query(models.War).filter_by(outcome=models.WarOutcome.in_progress).all():
            if war.end_date_days < self._basic_info.date_in_days - 5 * 360:
                war.outcome = models.WarOutcome.unknown
                self._session.add(war)
                self._history_add_peace_events(war)

    def _history_add_peace_events(self, war: models.War):
        for wp in war.participants:
            matching_event = self._session.query(models.HistoricalEvent).filter_by(
                event_type=models.HistoricalEventType.peace,
                country=wp.country,
                war=war,
            ).one_or_none()
            if matching_event is None:
                self._session.add(models.HistoricalEvent(
                    event_type=models.HistoricalEventType.peace,
                    war=war,
                    country=wp.country,
                    leader=self._ruler_dict.get(wp.country.country_id_in_game),
                    start_date_days=war.end_date_days,
                    event_is_known_to_player=wp.country.has_met_player(),
                ))


class PopStatsProcessor(AbstractGamestateDataProcessor):
    ID = "pop_stats"
    DEPENDENCIES = [CountryProcessor.ID,
                    SpeciesProcessor.ID,
                    FactionProcessor.ID,
                    CountryDataProcessor.ID]

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

        # TODO: Maybe make it possible to read other countries' pop stats??
        country_id_in_game, country_model = next(
            (cid, cm) for (cid, cm) in countries_dict.items() if cm.is_player
        )
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
            country_model = countries_dict.get(planet_country_id_in_game)

            species_id = pop_dict.get("species_index")
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
            stats["crime"] /= stats["pop_count"]
            stats["happiness"] /= stats["pop_count"]
            stats["power"] /= stats["pop_count"]

            species = species_dict[species_id]
            self._session.add(models.PopStatsBySpecies(
                country_data=country_data,
                species=species,
                **stats,
            ))

        gamestate_dict_factions = self._gamestate_dict.get("pop_factions", {})
        if not isinstance(gamestate_dict_factions, dict):
            gamestate_dict_factions = {}
        for faction_id, stats in stats_by_faction.items():
            if stats["pop_count"] == 0:
                continue
            faction_dict = gamestate_dict_factions.get(faction_id, {})
            if not isinstance(faction_dict, dict):
                faction_dict = {}
            stats["crime"] /= stats["pop_count"]
            stats["happiness"] /= stats["pop_count"]
            stats["power"] /= stats["pop_count"]
            stats["faction_approval"] = faction_dict.get("faction_approval", 0.0)
            stats["support"] = faction_dict.get("support", 0.0)

            faction = self._session.query(models.PoliticalFaction).filter_by(
                country=country_model,
                faction_id_in_game=faction_id,
            ).one_or_none()
            if faction is None:
                continue
            self._session.add(models.PopStatsByFaction(
                country_data=country_data,
                faction=faction,
                **stats,
            ))

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

            planet = self._session.query(models.Planet).filter_by(
                planet_id_in_game=planet_id
            ).one_or_none()
            if planet is None:
                logger.warning(f"{self._basic_info.logger_str}     Could not find planet with ID {planet_id}!")
                continue
            self._session.add(models.PlanetStats(
                gamestate=self._db_gamestate,
                planet=planet,
                **stats,
            ))

        for job, stats in stats_by_job.items():
            if stats["pop_count"] == 0:
                continue
            stats["crime"] /= stats["pop_count"]
            stats["happiness"] /= stats["pop_count"]
            stats["power"] /= stats["pop_count"]

            job = self._get_or_add_shared_description(job)
            self._session.add(models.PopStatsByJob(
                country_data=country_data,
                db_job_description=job,
                **stats,
            ))

        for stratum, stats in stats_by_stratum.items():
            if stats["pop_count"] == 0:
                continue
            stats["crime"] /= stats["pop_count"]
            stats["happiness"] /= stats["pop_count"]
            stats["power"] /= stats["pop_count"]

            stratum = self._get_or_add_shared_description(stratum)
            self._session.add(models.PopStatsByStratum(
                country_data=country_data,
                db_stratum_description=stratum,
                **stats,
            ))

        for ethos, stats in stats_by_ethos.items():
            if stats["pop_count"] == 0:
                continue
            stats["crime"] /= stats["pop_count"]
            stats["happiness"] /= stats["pop_count"]
            stats["power"] /= stats["pop_count"]

            ethos = self._get_or_add_shared_description(ethos)
            self._session.add(models.PopStatsByEthos(
                country_data=country_data,
                db_ethos_description=ethos,
                **stats,
            ))

    def _initialize_planet_owner_dict(self):
        self.country_by_planet_id = {}
        for country_id, country_dict in self._gamestate_dict["country"].items():
            if not isinstance(country_dict, dict):
                continue
            for planet_id in country_dict.get("owned_planets", []):
                self.country_by_planet_id[planet_id] = country_id
