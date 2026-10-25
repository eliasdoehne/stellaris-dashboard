import abc
import collections
import dataclasses
import datetime
import itertools
import json
import logging
import random
import threading
import time
import zlib
from typing import Dict, Any, Set, Iterable, Optional, Union, List, Tuple, Collection

import sqlalchemy
from sqlalchemy.orm import joinedload

from stellarisdashboard import datamodel, game_info, config
from stellarisdashboard.country_color_cache import clear_cached_country_colors

from stellarisdashboard.parsing import galaxy_events, save_copies, save_format

logger = logging.getLogger(__name__)


def dump_name(name: dict):
    return json.dumps(name, sort_keys=True)


def _stable_digest(items: Iterable) -> int:
    """Order-independent, process-stable digest of an iterable of hashable items.

    Used for values persisted to the DB (planet district/building/deposit/modifier
    change-detection hashes and bypass network ids). Python's built-in ``hash()``
    is salted per process (``PYTHONHASHSEED``), so a stored ``hash()`` never matches
    in a new process — every restart would re-diff and rewrite the child rows the
    hash columns exist to skip. Sorting the item reprs makes the digest independent
    of set/dict iteration order; ``crc32`` keeps it deterministic across processes.
    """
    canonical = ";".join(sorted(repr(item) for item in items))
    return zlib.crc32(canonical.encode("utf-8"))


def _extract_id(val, default: int = -1) -> int:
    """Normalize an in-game ID reference to an int.

    Recent Stellaris versions serialize some scalar ID references as a dict of
    the form ``{"reference": <id>, ...}`` instead of a bare integer. Accept
    either form and fall back to ``default`` for anything unexpected (a missing
    value, the "none" sentinel, etc.) so parsing doesn't crash on newer saves.
    """
    if isinstance(val, dict):
        val = val.get("reference", default)
    if isinstance(val, (int, float)):
        return int(val)
    return default


# this is a naive cache for shared_descriptions, which helps to cut down on DB queries while processing
# it needs to be cleared between processing saves (at the end of TimelineExtractor.process_gamestate)
# the built-in @cache decorator was leaking memory, hanging on to references of processor instances
_shared_description_cache: dict[str, datamodel.SharedDescription] = {}
# module-level caches like the one above are not thread-safe: one save is processed at a time per process
_process_gamestate_lock = threading.Lock()

_BIOSHIP_FAMILY_HULLS = {
    "mauler": "corvette",
    "weaver": "destroyer",
    "harbinger": "cruiser",
    "stinger": "battleship",
}
# Fallen empire and NPC warship sizes, mapped by the hull class of their design (the
# reactor/thruster components in the 4.0.1 test save, or the hull named in the key).
# Space fauna, crystals, and other unique guardians are left out on purpose.
_NPC_HULLS = {
    "small_ship_fallen_empire": "destroyer",
    "large_ship_fallen_empire": "battleship",
    "massive_ship_fallen_empire": "titan",
    "ancient_corvette": "corvette",
    "ancient_destroyer": "destroyer",
    # pirate and marauder hulls: size_multiplier (5/10/20/40) and slot size match the standard class
    "pirate_corvette": "corvette",
    "pirate_destroyer": "destroyer",
    "pirate_cruiser": "cruiser",
    "marauder_corvette": "corvette",
    "marauder_destroyer": "destroyer",
    "marauder_cruiser": "cruiser",
    "marauder_galleon": "battleship",
    "caravaneer_destroyer_01": "destroyer",
    "caravaneer_cruiser_01": "cruiser",
}
_FLEET_COMPOSITION_HULLS = {
    "frigate",
    "corvette",
    "destroyer",
    "cruiser",
    "battleship",
    "titan",
    "colossus",
}


def _fleet_composition_hull(ship_size) -> Optional[str]:
    """Map a ship size to its fleet composition hull, including BioGenesis
    (<family>_stage_<n>, bio_titan) and Overlord (offspring_*) bioships, and
    fallen empire and NPC warships."""
    if not isinstance(ship_size, str):
        return None
    if ship_size == "bio_titan":
        return "titan"
    if ship_size in _NPC_HULLS:
        return _NPC_HULLS[ship_size]
    name = ship_size.removeprefix("offspring_")
    family, sep, stage = name.partition("_stage_")
    if sep and stage.isdigit():
        return _BIOSHIP_FAMILY_HULLS.get(family)
    if name in _FLEET_COMPOSITION_HULLS:
        return name
    return None


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


# games whose missing player country has already been warned about
_warned_missing_player_games: Set[Optional[str]] = set()


class TimelineExtractor:
    def __init__(self):
        self.basic_info: BasicGameInfo = None
        self._session = None
        self._gamestate_dict = None
        self.number_of_parsed_saves = 0
        self._other_players = set()
        # failures of the last processed gamestate, see save_copies.failure
        self.failures: List[Dict[str, Any]] = []

    def process_gamestate(
        self, game_id: str, gamestate_dict: Dict[str, Any], replace_existing: bool = False
    ):
        """Import a gamestate. With replace_existing, a gamestate of the same date is deleted and imported again."""
        self.failures = []
        self._gamestate_dict = gamestate_dict
        self._read_basic_game_info(game_id)
        logger.info(f"{self.basic_info.logger_str} Processing Gamestate")
        t_start_gs = time.process_time()
        with _process_gamestate_lock, datamodel.get_db_session(game_id=game_id, write=True) as self._session:
            sqlalchemy.event.listen(
                self._session, "before_flush", datamodel.drop_special_country_events
            )
            try:
                db_game = self._get_or_add_game_to_db(game_id)
                if replace_existing:
                    self._delete_existing_gamestate(db_game)
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
                self.failures.append(
                    save_copies.failure(save_copies.STAGE_IMPORT, save_copies.format_exception(e))
                )
                logger.exception(
                    f"{self.basic_info.logger_str} Rolling back changes to database..."
                )
                if config.CONFIG.debug_mode or isinstance(e, KeyboardInterrupt):
                    raise e
                
            # needs to be cleared between processing saves, see more notes at declaration
            _shared_description_cache.clear()

    def _delete_existing_gamestate(self, db_game):
        existing = (
            self._session.query(datamodel.GameState)
            .filter_by(game=db_game, date=self.basic_info.date_in_days)
            .all()
        )
        for gamestate in existing:
            logger.info(f"{self.basic_info.logger_str} Replacing existing gamestate")
            self._session.delete(gamestate)
        self._session.flush()

    def _check_if_gamestate_exists(self, db_game):
        # single indexed lookup; loading db_game.game_states would fetch every
        # gamestate row of the campaign on each processed save
        return (
            self._session.query(datamodel.GameState.gamestate_id)
            .filter_by(game=db_game, date=self.basic_info.date_in_days)
            .first()
            is not None
        )

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
                # a failing processor only loses its own rows; processors depending on it are skipped
                savepoint = self._session.begin_nested()
                try:
                    data_processor.extract_data_from_gamestate(
                        {
                            key: all_dependencies[key]
                            for key in data_processor.DEPENDENCIES
                        }
                    )
                    data = data_processor.data()
                    self._session.flush()
                except Exception as e:
                    savepoint.rollback()
                    # descriptions created inside the rolled-back savepoint are no longer persisted
                    _shared_description_cache.clear()
                    self.failures.append(
                        save_copies.failure(
                            save_copies.STAGE_PROCESSOR,
                            save_copies.format_exception(e),
                            processor=data_processor.ID,
                        )
                    )
                    if config.CONFIG.debug_mode:
                        raise
                    logger.exception(
                        f"{self.basic_info.logger_str}   - Processor {data_processor.ID} failed, "
                        f"discarding its changes"
                    )
                    continue
                savepoint.commit()
                all_dependencies[data_processor.ID] = data
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
            elif len(self._other_players) == 0:
                player_country_name = "Observer Mode"
            else:
                player_country_name = "Player Not Found"
            galaxy_info = self._gamestate_dict["galaxy"]
            game = datamodel.Game(
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
        date_str = self._gamestate_dict["date"]
        date_in_days = datamodel.date_to_days(date_str)
        self.basic_info = BasicGameInfo(
            game_id=game_id,
            date_in_days=date_in_days,
            player_country_id=self._identify_player_country(game_id),
            other_players=self._other_players,
            number_of_parsed_saves=self.number_of_parsed_saves,
        )

    def _identify_player_country(self, game_id: Optional[str] = None):
        player_country = self._find_player_country()
        countries = self._gamestate_dict.get("country")
        if player_country is not None and not (
            isinstance(countries, dict) and isinstance(countries.get(player_country), dict)
        ):
            # warn once per game, the player's empire stays missing in every later save
            log = logger.debug if game_id in _warned_missing_player_games else logger.warning
            _warned_missing_player_games.add(game_id)
            log(
                f"Player country {player_country} is not in the gamestate, treating as no player"
            )
            return None
        return player_country

    def _find_player_country(self):
        players = self._gamestate_dict.get("player")
        if players:
            if len(players) == 1:
                return players[0]["country"]
            else:
                playercountry = None
                for player in players:
                    if player["name"] == config.CONFIG.mp_username:
                        playercountry = player["country"]
                    else:
                        self._other_players.add(player["country"])
                if playercountry is None:
                    logger.warning(
                        f"Could not find player matching Multiplayer username \"{config.CONFIG.mp_username}\""
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
        yield PlanetProcessor()
        yield LeaderProcessor()
        yield SectorColonyEventProcessor()
        yield PlanetUpdateProcessor()
        yield RulerEventProcessor()
        yield CouncilProcessor()
        yield GovernmentProcessor()
        yield PolicyProcessor()
        yield FactionProcessor()
        yield FirstContactProcessor()
        yield DiplomacyUpdatesProcessor()
        yield FederationProcessor()
        yield GalacticCommunityProcessor()
        yield FederationProgressProcessor()
        yield CrisisProgressProcessor()
        yield ScientistEventProcessor()
        yield EnvoyEventProcessor()
        yield FleetInfoProcessor()
        yield AnomalyProcessor()
        yield ArchaeologyProcessor()
        yield AstralRiftProcessor()
        yield RelicProcessor()
        yield LeviathanProcessor()
        yield MegastructureProcessor()
        yield EspionageProcessor()
        yield GalaxyEventProcessor()
        yield WarProcessor()
        yield TruceProcessor()
        # before SubjectProcessor, which closes the relations of destroyed subjects
        yield CountryDestroyedProcessor()
        yield SubjectProcessor()
        yield PopStatsProcessor()


# LeaderProcessor needs to refer to this before PlanetProcessor has been declared
# For consistency, maybe we should move all IDs up above here
PLANET_PROCESSOR_ID = "planet_models"


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
        if text in _shared_description_cache:
            return _shared_description_cache[text]
        matching_description = (
            self._session.query(datamodel.SharedDescription)
            .filter_by(text=text)
            .one_or_none()
        )
        if matching_description is None:
            matching_description = datamodel.SharedDescription(text=text)
            self._session.add(matching_description)
        _shared_description_cache[text] = matching_description
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
            neighbor_model = self.systems_by_ingame_id.get(neighbor_id)
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
                    network_id = _stable_digest(["lgate"])
                elif bypass_type == "gateway":
                    network_id = _stable_digest(set(connections) | {bypass_id})
                elif bypass_type == "wormhole":
                    network_id = _stable_digest(set(connections) | {bypass_id})
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
        # preload all known countries once instead of one query per country per save
        known_countries = {
            c.country_id_in_game: c
            for c in self._session.query(datamodel.Country).filter_by(
                game=self._db_game
            )
        }
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
            origin = country_data_dict.get("government", {}).get("origin")
            country_model = known_countries.get(country_id)
            is_new_country = country_model is None

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
                    origin=origin
                )
                if country_id == self._basic_info.player_country_id:
                    country_model.first_player_contact_date = 0
                self._session.add(country_model)
            if (
                country_name != country_model.country_name
                or country_type != country_model.country_type
                or primary_color != country_model.primary_color
                or secondary_color != country_model.secondary_color
                or origin != country_model.origin
            ):
                country_model.country_name = country_name
                country_model.country_type = country_type
                country_model.primary_color = primary_color
                country_model.secondary_color = secondary_color
                country_model.origin = origin
                self._session.add(country_model)
            country_model.founded_in_gamestate = is_new_country
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


# a crisis claiming a system within this many days of its previous owner losing it is credited with the loss
CRISIS_CLAIM_WINDOW_DAYS = 2 * 360


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
        crisis_fleet_systems = self._crisis_fleet_systems(fleet_owners_dict)

        starbase_systems = set()

        for starbase_id, starbase_dict in starbases.items():
            if not isinstance(starbase_dict, dict):
                continue
            # Nomad waystations share a system with the real starbase and don't confer ownership
            if str(starbase_dict.get("level", "")).startswith(
                "starbase_level_waystation"
            ):
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

            if system_id_in_game in starbase_systems:
                # Only one starbase per system determines its owner
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
            self._update_owner(
                None, system_model, destroyed_by=crisis_fleet_systems.get(system_id)
            )

    def _crisis_fleet_systems(
        self, fleet_owners_dict: Dict[int, datamodel.Country]
    ) -> Dict[int, datamodel.Country]:
        """Systems with a crisis warfleet in them, by in-game system ID."""
        fleets = self._gamestate_dict.get("fleet")
        if not isinstance(fleets, dict):
            return {}
        result = {}
        for fleet_id, country in fleet_owners_dict.items():
            if not country.is_crisis_country():
                continue
            fleet = fleets.get(fleet_id)
            if not isinstance(fleet, dict) or not fleet.get("military_power"):
                continue
            movement = fleet.get("movement_manager")
            coordinate = movement.get("coordinate") if isinstance(movement, dict) else None
            system_id = coordinate.get("origin") if isinstance(coordinate, dict) else None
            if isinstance(system_id, int):
                result.setdefault(system_id, country)
        return result

    def _update_owner(
        self,
        current_owner: Optional[datamodel.Country],
        system_model: datamodel.System,
        destroyed_by: Optional[datamodel.Country] = None,
    ):
        """destroyed_by: the crisis whose fleet is in a system that just lost its starbase."""
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
            # Successor states, liberated empires and rebels take their systems when first seen; crises invade
            event_type = (
                datamodel.HistoricalEventType.founded_with_system
                if current_owner.founded_in_gamestate and not current_owner.is_crisis_country()
                else datamodel.HistoricalEventType.conquered_system
            )

        if owner_changed:
            had_owner = system_model.country is not None
            system_model.country = current_owner
            self._session.add(system_model)

            ownership = (
                self._session.query(datamodel.SystemOwnership)
                .filter_by(system=system_model)
                .order_by(datamodel.SystemOwnership.end_date_days.desc())
                .first()
            )
            if ownership is not None and had_owner:
                ownership.end_date_days = self._basic_info.date_in_days - 1
                self._session.add(ownership)

                target_country = ownership.country
                if target_country is not None:
                    taken_by = current_owner or destroyed_by
                    is_visible = target_country.has_met_player() or (
                        taken_by is not None and taken_by.has_met_player()
                    )
                    self._session.add(
                        datamodel.HistoricalEvent(
                            event_type=datamodel.HistoricalEventType.lost_system,
                            country=target_country,
                            target_country=taken_by,
                            system=system_model,
                            start_date_days=self._basic_info.date_in_days,
                            event_is_known_to_player=is_visible,
                        )
                    )
            elif (
                ownership is not None
                and current_owner is not None
                and current_owner.is_crisis_country()
            ):
                target_country = self._credit_crisis_conquest(
                    ownership, current_owner, system_model
                )
                if target_country is not None:
                    event_type = datamodel.HistoricalEventType.conquered_system
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

    def _credit_crisis_conquest(
        self,
        ownership: datamodel.SystemOwnership,
        crisis_country: datamodel.Country,
        system_model: datamodel.System,
    ) -> Optional[datamodel.Country]:
        """Crises and the Khan destroy starbases and claim the empty system later: credit them with
        the previous owner's recent loss. Returns the previous owner if credited."""
        previous_owner = ownership.country
        if (
            previous_owner is None
            or previous_owner == crisis_country
            or not previous_owner.is_real_country()
            or self._basic_info.date_in_days - ownership.end_date_days
            > CRISIS_CLAIM_WINDOW_DAYS
        ):
            return None
        lost_event = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(
                event_type=datamodel.HistoricalEventType.lost_system,
                country=previous_owner,
                system=system_model,
                target_country_id=None,
            )
            .filter(datamodel.HistoricalEvent.start_date_days > ownership.end_date_days)
            .first()
        )
        if lost_event is not None:
            lost_event.target_country = crisis_country
            self._session.add(lost_event)
        return previous_owner


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
    DEPENDENCIES = [CountryProcessor.ID, DiplomacyDictProcessor.ID]

    def __init__(self):
        super().__init__()
        self.diplo_relations: Dict[int, Dict[int, datamodel.DiplomaticRelation]] = None

    def data(self):
        return self.diplo_relations

    def extract_data_from_gamestate(self, dependencies):
        countries_dict: Dict[int, datamodel.Country] = dependencies[CountryProcessor.ID]
        diplomacy_dict = dependencies[DiplomacyDictProcessor.ID]["diplomacy"]

        # A DiplomaticRelation row exists only for ordered country pairs that have
        # (or once had) some relation. We iterate two sources so DiplomacyUpdatesProcessor
        # can still detect both directions of change:
        #   - all existing rows: a pair active in a previous save keeps its row, so a
        #     relation that vanishes this save is still visited and its event closed
        #     (cancelled-agreement tracking);
        #   - pairs active in this save: a newly-formed relation gets a row created here
        #     so its "started" event fires.
        # Pairs with no relation at all get no row (a missing row reads identically to an
        # all-False row for every consumer), which avoids the previous O(N^2) pair sweep.
        self.diplo_relations: Dict[int, Dict[int, datamodel.DiplomaticRelation]] = {}

        for r in self._session.query(datamodel.DiplomaticRelation).all():
            owner_id = r.owner.country_id_in_game
            target_id = r.target.country_id_in_game
            self.diplo_relations.setdefault(owner_id, {})[target_id] = r

        for c_id_1, c_model_1 in countries_dict.items():
            if not c_model_1.is_real_country():
                continue
            existing_targets = self.diplo_relations.setdefault(c_id_1, {})
            active_targets = set().union(*diplomacy_dict.get(c_id_1, {}).values())
            for c_id_2 in active_targets:
                if c_id_2 == c_id_1 or c_id_2 in existing_targets:
                    continue
                c_model_2 = countries_dict.get(c_id_2)
                if c_model_2 is None or not c_model_2.is_real_country():
                    continue
                r = datamodel.DiplomaticRelation(
                    country_id=c_model_1.country_id,
                    target_country_id=c_model_2.country_id,
                )
                existing_targets[c_id_2] = r
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
                net_trade=0.0,
                net_food=0.0,
                net_unity=0.0,
                net_influence=0.0,
                net_physics_research=0.0,
                net_society_research=0.0,
                net_engineering_research=0.0,
                net_biomass=0.0,
                **diplomacy_data,
            )
            self._session.add(country_data)
            self._extract_country_economy(country_data, country_data_dict)

            if country_model.first_player_contact_date is None and diplomacy_data.get(
                "has_communications_with_player"
            ):
                country_model.first_player_contact_date = self._basic_info.date_in_days
                self._session.add(country_model)

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
                if (
                    self._basic_info.player_country_id is not None
                    and attitude.get("country") == self._basic_info.player_country_id
                ):
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
        if not isinstance(budget_dict, dict):
            return

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
                "trade",
                "unity",
                "influence",
                "physics_research",
                "society_research",
                "engineering_research",
                "biomass",
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
            country_data.net_trade += resources.get("trade", 0.0)
            country_data.net_food += resources.get("food")
            country_data.net_unity += resources.get("unity")
            country_data.net_influence += resources.get("influence")
            country_data.net_physics_research += resources.get("physics_research")
            country_data.net_society_research += resources.get("society_research")
            country_data.net_engineering_research += resources.get(
                "engineering_research"
            )
            country_data.net_biomass += resources.get("biomass", 0.0)

            if not country.is_real_country():
                continue  # only the totals are kept for special countries
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
                    net_trade=resources.get("trade", 0.0),
                    net_unity=resources.get("unity"),
                    net_influence=resources.get("influence"),
                    net_volatile_motes=values.get("volatile_motes", 0.0),
                    net_exotic_gases=values.get("exotic_gases", 0.0),
                    net_rare_crystals=values.get("rare_crystals", 0.0),
                    net_living_metal=save_format.get_budget_value(values, "living_metal"),
                    net_zro=save_format.get_budget_value(values, "zro"),
                    net_dark_matter=save_format.get_budget_value(values, "dark_matter"),
                    net_nanites=values.get("nanites", 0.0),
                    net_minor_artifacts=values.get("minor_artifacts", 0.0),
                    net_astral_threads=values.get("astral_threads", 0.0),
                    net_biomass=values.get("biomass", 0.0),
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
        # preload all known species once instead of one query per species per save
        known_species = {
            s.species_id_in_game: s
            for s in self._session.query(datamodel.Species).filter_by(
                game=self._db_game
            )
        }
        for species_ingame_id, species_dict in sorted(
            self._gamestate_dict.get("species_db", {}).items()
        ):
            species_model = self._get_or_add_species(
                species_ingame_id, species_dict, known_species
            )
            self._species_by_ingame_id[species_ingame_id] = species_model
            if species_dict.get("class") == "ROBOT":
                self._robot_species.add(species_ingame_id)

    def _get_or_add_species(
        self,
        species_id_in_game: int,
        species_data: Dict,
        known_species: Dict[int, datamodel.Species],
    ):
        species_name = dump_name(species_data.get("name", "Unnamed Species"))
        species = known_species.get(species_id_in_game)
        if species is None:
            species = datamodel.Species(
                game=self._db_game,
                species_name=species_name,
                species_class=species_data.get("class", "Unknown Class"),
                species_id_in_game=species_id_in_game,
                parent_species_id_in_game=_extract_id(species_data.get("base", -1)),
                home_planet_id=_extract_id(species_data.get("home_planet", -1)),
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
    DEPENDENCIES = [CountryProcessor.ID, SpeciesProcessor.ID, PLANET_PROCESSOR_ID]

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
        self._planets_by_ingame_id = dependencies.get(PlanetProcessor.ID)
        self._countries_by_ingame_id = dependencies.get(CountryProcessor.ID)

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
                country = self._countries_by_ingame_id.get(leader_dict.get("country"))
                self._update_leader_attributes(country=country, leader=leader, leader_dict=leader_dict)
            if not leader.is_active:
                country_data = leader.country.get_most_recent_data() if leader.country is not None else None
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
        ethic = leader_dict.get("ethic", "ethic_neutral")
        job = leader_dict.get("job")
        # 4.4 replaced "planet" with "background_planet" = {type=planet, reference=<id>}
        planet_id = _extract_id(leader_dict.get("planet", leader_dict.get("background_planet")))
        planet = self._planets_by_ingame_id.get(planet_id)
        creator_id = leader_dict.get("creator")
        creator = self._countries_by_ingame_id.get(creator_id)
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
            ethic=ethic,
            job=job,
            planet=planet,
            creator=creator,
        )
        self._update_leader_attributes(
            country=country_model, leader=leader, leader_dict=leader_dict
        )  # sets additional attributes
        country_data = country_model.get_most_recent_data()
        event = datamodel.HistoricalEvent(
            event_type=datamodel.HistoricalEventType.leader_recruited,
            country=country_model,
            leader=leader,
            start_date_days=date_hired,
            end_date_days=self._basic_info.date_in_days,
            event_is_known_to_player=(country_model is not None and country_model.is_player)
            or (country_data is not None and country_data.attitude_towards_player.reveals_economy_info()),
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

    def _update_leader_attributes(self, country: datamodel.Country, leader: datamodel.Leader, leader_dict):
        leader_class = leader_dict.get("class", "unknown class")
        leader_gender = leader_dict.get("gender", "other")
        first_name, second_name = self.get_leader_name(leader_dict)
        level = leader_dict.get("level", -1)
        species_id = _extract_id(leader_dict.get("species", -1))
        leader_species = self._species_dict.get(species_id)
        subclass, leader_traits = self._get_leader_traits(leader_dict)
        ethic = leader_dict.get("ethic", "ethic_neutral")
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
            or leader.country != country
            or leader.ethic != ethic
        ):
            hist_event_kwargs = dict(
                country=country,
                start_date_days=self._basic_info.date_in_days,
                leader=leader,
            )
            if leader.last_level != level:
                self._session.add(
                    datamodel.HistoricalEvent(
                        **hist_event_kwargs,
                        event_type=datamodel.HistoricalEventType.level_up,
                        db_description=self._get_or_add_shared_description(str(level)),
                        event_is_known_to_player=country is not None and country.is_player,
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
                            event_is_known_to_player=country is not None and country.is_player,
                        )
                    )
            if leader.country != country:
                if leader.country is not None:
                    country_data = leader.country.get_most_recent_data()
                    self._session.add(
                        datamodel.HistoricalEvent(
                            event_type=datamodel.HistoricalEventType.leader_left_country,
                            country=leader.country,
                            leader=leader,
                            start_date_days=self._basic_info.date_in_days,
                            event_is_known_to_player=(leader.country is not None and leader.country.is_player)
                            or (country_data is not None and country_data.attitude_towards_player.reveals_economy_info()),
                        )
                    )
                if country is not None:
                    country_data = country.get_most_recent_data()
                    self._session.add(
                        datamodel.HistoricalEvent(
                            **hist_event_kwargs,
                            event_type=datamodel.HistoricalEventType.leader_recruited,
                            end_date_days=self._basic_info.date_in_days,
                            event_is_known_to_player=(country is not None and country.is_player)
                            or (country_data is not None and country_data.attitude_towards_player.reveals_economy_info()),
                        )
                    )
            if leader.ethic != ethic:
                self._session.add(
                    datamodel.HistoricalEvent(
                        **hist_event_kwargs,
                        event_type=datamodel.HistoricalEventType.leader_changed_ethic,
                        db_description=self._get_or_add_shared_description(ethic),
                        event_is_known_to_player=country is not None and country.is_player,
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
            leader.ethic = ethic

            # apparently, setting leader.country = None deletes the entire leader from the DB
            # but setting leader.country_id = None is fine
            if country is None:
                leader.country_id = None
            else:
                leader.country = country

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
    ID = PLANET_PROCESSOR_ID
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

        planets_by_system = [
            (system_id, system_dict.get("planet", []))
            for system_id, system_dict in sorted(
                self._gamestate_dict["galactic_object"].items()
            )
        ]
        # colonies carried by ships (nomad arkships) are recorded as planets without a system;
        # the system is set from the carrying ship's position below
        planets_by_system.append(
            (None, save_format.get_arkship_planet_ids(self._gamestate_dict))
        )

        for system_id, planets in planets_by_system:
            if isinstance(planets, int):
                planets = [planets]
            for ingame_id in planets:
                planet_dict = save_format.get_planet(self._gamestate_dict, ingame_id)
                if not isinstance(planet_dict, dict):
                    continue

                if save_format.is_arkship_planet_id(ingame_id):
                    system_id = save_format.get_arkship_system_id(
                        self._gamestate_dict, ingame_id
                    )
                if ingame_id not in self.planets_by_ingame_id:
                    system_model = systems_by_id.get(system_id)
                    self.planets_by_ingame_id[ingame_id] = self._add_planet_model(
                        planet_dict=planet_dict,
                        planet_id=ingame_id,
                        system_model=system_model,
                    )

                planet_model = self.planets_by_ingame_id[ingame_id]
                was_updated = False
                # an arkship keeps its last known system while in transit
                arkship_system = (
                    systems_by_id.get(system_id)
                    if save_format.is_arkship_planet_id(ingame_id)
                    else None
                )
                if arkship_system is not None and planet_model.system is not arkship_system:
                    planet_model.system = arkship_system
                    was_updated = True
                was_updated |= self._update_countable_planet_attributes(
                    planet_model=planet_model,
                    entity_attribute_name="districts",
                    entity_hash_attribute="districts_hash",
                    current_entities=save_format.get_district_types(
                        self._gamestate_dict, planet_dict
                    ),
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
        return save_format.get_building_types(self._gamestate_dict, planet_dict)

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
                    db_modifier.expiry_date = current_modifiers[modifier_text]
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
        current_hash = _stable_digest(entity_dict.items())
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
                sector_capital_id = save_format.resolve_colony_to_planet_id(
                    self._gamestate_dict, sector_info.get("local_capital")
                )
                sector_capital = self._planets_dict.get(sector_capital_id)
                sector_capital_planet_dict = save_format.get_planet(self._gamestate_dict, sector_capital_id)
                governor_model = self._leaders_dict.get(sector_capital_planet_dict.get("governor")) if sector_capital_planet_dict is not None else None

                for system_id in sector_info.get("systems", []):
                    self._history_add_planetary_events_within_sector(
                        country_model, system_id, governor_model, sector_capital, sector_description
                    )
                    if system_id in unprocessed_systems:
                        unprocessed_systems.remove(system_id)

            for system_id in unprocessed_systems:
                self._history_add_planetary_events_within_sector(
                    country_model, system_id, None, None, None
                )

    def _history_add_planetary_events_within_sector(
        self,
        country_model: datamodel.Country,
        system_id: int,
        sector_governor: Optional[datamodel.Leader],
        sector_capital: Optional[datamodel.Planet],
        sector_description: Optional[datamodel.SharedDescription],
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
            planet_dict = save_format.get_planet(self._gamestate_dict, planet_id)
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
            planet_governor = self._leaders_dict.get(planet_dict.get("governor"))
            governor = planet_governor if planet_governor is not None else sector_governor
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
            if planet_governor is not None :
                self._history_add_or_update_governor_events(
                    country_model,
                    planet_model,
                    sector_capital,
                    planet_governor,
                    sector_description,
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
            planet_model.colonized_date = end_date_days
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

    def _history_add_or_update_governor_events(
        self,
        country_model: datamodel.Country,
        planet: datamodel.Planet,
        sector_capital: Optional[datamodel.Planet],
        governor: datamodel.Leader,
        sector_description: Optional[datamodel.SharedDescription],
    ):
        event_type = datamodel.HistoricalEventType.governed_sector if sector_capital == planet else datamodel.HistoricalEventType.governed_planet
        # check if governor was ruling same planet/sector before => update date and return.
        # Filter by planet: governed_planet events have a null db_description, so without
        # it the query would match a different planet's (or country's) event.
        event = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(
                event_type=event_type,
                planet=planet,
                db_description=sector_description,
            )
            .order_by(datamodel.HistoricalEvent.end_date_days.desc())
            .first()
        )
        if (
            event is not None
            and event.leader == governor
            and event.end_date_days > self._basic_info.date_in_days - 5 * 360
        ):  # if the governor ruled this planet/sector less than 5 years ago, re-use the event...
            event.end_date_days = self._basic_info.date_in_days - 1
        else:
            country_data = country_model.get_most_recent_data()
            event = datamodel.HistoricalEvent(
                event_type=event_type,
                leader=governor,
                country=country_model,
                db_description=sector_description,
                start_date_days=self._basic_info.date_in_days,
                end_date_days=self._basic_info.date_in_days,
                event_is_known_to_player=country_data is not None
                and country_data.attitude_towards_player.reveals_economy_info(),
            )

        if event.planet is None and planet is not None:
            event.planet = planet
            event.system = planet.system
        self._session.add(event)


class PlanetUpdateProcessor(AbstractGamestateDataProcessor):
    ID = "planet_updates"
    DEPENDENCIES = [PlanetProcessor.ID, SectorColonyEventProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        planet_models = dependencies[PlanetProcessor.ID]
        for ingame_id, planet_model in planet_models.items():
            planet_dict = save_format.get_planet(self._gamestate_dict, ingame_id)
            if planet_dict is None and not save_format.is_arkship_planet_id(ingame_id):
                planet_dict = {}  # the planet no longer exists
            if not isinstance(planet_dict, dict):
                continue
            self._update_planet_model(planet_dict, planet_model)

    def _update_planet_model(self, planet_dict: Dict, planet_model: datamodel.Planet):
        planet_class = planet_dict.get("planet_class")
        planet_name = dump_name(planet_dict.get("name")) if "name" in planet_dict else None
        if planet_name is not None and planet_model.planet_name != planet_name:
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
                continue
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
        capital_id = save_format.resolve_colony_to_planet_id(self._gamestate_dict, capital_id)
        capital = self._planet_by_ingame_id.get(capital_id)
        if capital != country_model.capital:
            previous_capital = country_model.capital
            country_model.capital = capital
            self._session.add(country_model)
            # the first capital seen for a country is not a relocation
            if capital is not None and previous_capital is not None:
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
                previous_ruler_event.event_is_known_to_player = country_model.has_met_player()
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
        
        active_council_positions = set() # used to check if any open HistoricalEvents need to be closed
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
            active_council_positions.add(f"{country_model.country_id}-{leader_model.leader_id}-{councilor_type}")

            previous_event = (
                self._session.query(datamodel.HistoricalEvent)
                .filter_by(
                    event_type=datamodel.HistoricalEventType.councilor,
                    country=country_model,
                    db_description=desc,
                    end_date_days=None,
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

        # check if there are any open events that need to be closed (eg the position doesn't exist anymore)
        open_councilor_events = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(event_type=datamodel.HistoricalEventType.councilor, end_date_days=None)
        )
        for event in open_councilor_events:
            key = f"{event.country_id}-{event.leader_id}-{event.db_description.text}"
            if key not in active_council_positions:
                event.end_date_days = self._basic_info.date_in_days - 1
                self._session.add(event)


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
            max_civics = len(datamodel.Government.CIVIC_COLUMNS)
            if len(civics) > max_civics:
                logger.warning(
                    f"{self._basic_info.logger_str} Country {country_id} has {len(civics)} civics, "
                    f"but only {max_civics} can be stored. Ignoring the rest."
                )
            # truncate before comparing, since the previous government only has the stored civics
            civics = set(sorted(civics)[:max_civics])
            authority = gov_dict.get("authority", "other")
            gov_type = gov_dict.get("type", "other")
            gov_was_reformed = False
            empire_was_renamed = False
            # a new pre-FTL age rewrites the name, government and civics, so it replaces those events
            age_changed = self._update_pre_ftl_age(country_model, country_dict)

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
                previous_civics = prev_gov.civics
                gov_was_reformed = (
                    (ethics != previous_ethics)
                    or (civics != previous_civics)
                    or (authority != prev_gov.authority)
                    or (gov_type != prev_gov.gov_type)
                )
                empire_was_renamed = gov_name != prev_gov.gov_name
                # nothing has changed...
                if not (gov_was_reformed or empire_was_renamed):
                    continue

            ethics = dict(zip([f"ethics_{i}" for i in range(1, 6)], sorted(ethics)))
            civics = dict(zip(datamodel.Government.CIVIC_COLUMNS, sorted(civics)))

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
            if gov_was_reformed and not age_changed:
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
            if empire_was_renamed and not age_changed:
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.empire_renamed,
                        country=country_model,
                        leader=rulers_dict.get(country_id),
                        # The names are JSON, which never contains a raw newline
                        db_description=self._get_or_add_shared_description(
                            f"{prev_gov.gov_name}\n{gov_name}"
                        ),
                        start_date_days=self._basic_info.date_in_days,
                        end_date_days=self._basic_info.date_in_days,
                        event_is_known_to_player=country_model.has_met_player(),
                    )
                )

    def _update_pre_ftl_age(self, country_model: datamodel.Country, country_dict) -> bool:
        """Track a primitive country's technology age; returns True if it changed since the last save."""
        if country_model.country_type != "primitive":
            return False
        age = country_dict.get("preftl_age")
        if not isinstance(age, str) or age == country_model.pre_ftl_age:
            return False
        previous_age = country_model.pre_ftl_age
        country_model.pre_ftl_age = age
        self._session.add(country_model)
        if previous_age is None:
            return False  # first time seen, nothing to compare against
        self._session.add(
            datamodel.HistoricalEvent(
                event_type=datamodel.HistoricalEventType.pre_ftl_age_change,
                country=country_model,
                db_description=self._get_or_add_shared_description(
                    f"{previous_age}|{age}"
                ),
                start_date_days=self._basic_info.date_in_days,
                end_date_days=self._basic_info.date_in_days,
                event_is_known_to_player=country_model.has_met_player(),
            )
        )
        return True


class PolicyProcessor(AbstractGamestateDataProcessor):
    ID = "policy"
    DEPENDENCIES = [CountryProcessor.ID, RulerEventProcessor.ID]

    def extract_data_from_gamestate(self, dependencies):
        countries_dict = dependencies[CountryProcessor.ID]
        rulers_dict = dependencies[RulerEventProcessor.ID]

        for country_id, country_model in countries_dict.items():
            if not country_model.is_real_country():
                continue
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
                            event_is_known_to_player=country_model.has_met_player(),
                        )
                    )

    def _get_current_policies(self, country_id) -> dict[str, (str, str)]:
        country_gs_dict = self._gamestate_dict["country"][country_id]
        current_policies = country_gs_dict.get("active_policies")
        if not isinstance(current_policies, list):
            current_policies = []
        current_stance_per_policy = {
            p.get("policy"): (p.get("selected"), p.get("date"))
            for p in current_policies if isinstance(p, dict) # ambiguous {} is parsed as empty list, not empty dict

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
            matching_event.event_is_known_to_player = is_known
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

        federation_type = datamodel.HistoricalEventType.formed_federation
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
            # the flag is still toggled and old formed_federation spans closed; FederationProcessor records memberships
            (federation_type, None, "federations"),
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

                    # the first contact process event already records this contact, with exact dates
                    covered_by_process = (
                        event_type == datamodel.HistoricalEventType.first_contact
                        and self._has_first_contact_process(
                            country_model, target_country_model
                        )
                    )

                    if is_now_active:  # Create new historical event entry
                        if covered_by_process or event_type == federation_type:
                            continue
                        for (et, c_model, tc_model, c_ruler) in country_tuples:
                            matching_event = datamodel.HistoricalEvent(
                                event_type=et,
                                country=c_model,
                                target_country=tc_model,
                                leader=c_ruler,
                                start_date_days=self._basic_info.date_in_days,
                                event_is_known_to_player=is_known_to_player,
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
                            elif not covered_by_process and event_type != federation_type:
                                logger.warning(
                                    f"Could not find event matching {relation}, {diplo_dict_key}"
                                )

    def _has_first_contact_process(self, country_a, country_b) -> bool:
        process_type = datamodel.HistoricalEventType.first_contact_process
        query = self._session.query(datamodel.HistoricalEvent.historical_event_id).filter(
            datamodel.HistoricalEvent.event_type == process_type,
            sqlalchemy.or_(
                sqlalchemy.and_(
                    datamodel.HistoricalEvent.country == country_a,
                    datamodel.HistoricalEvent.target_country == country_b,
                ),
                sqlalchemy.and_(
                    datamodel.HistoricalEvent.country == country_b,
                    datamodel.HistoricalEvent.target_country == country_a,
                ),
            ),
        )
        return query.first() is not None

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


class FederationProcessor(AbstractGamestateDataProcessor):
    """Records federations, their membership spans and the founding, joining, leaving and dissolution events."""

    ID = "federations"
    DEPENDENCIES = [CountryProcessor.ID]

    def __init__(self):
        super().__init__()
        self._federations: Dict[int, datamodel.Federation] = None

    def initialize_data(self):
        self._federations = {}

    def data(self) -> Dict[int, datamodel.Federation]:
        """The federations of this save by in-game id."""
        return self._federations

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        today = self._basic_info.date_in_days
        yesterday = today - 1
        previous_date = (
            self._session.query(sqlalchemy.func.max(datamodel.GameState.date))
            .filter(datamodel.GameState.date < today)
            .scalar()
        )

        save_federations = self._gamestate_dict.get("federation")
        if not isinstance(save_federations, dict):
            save_federations = {}
        current = {}  # in-game id -> (name, start, {country id: is_associate})
        for f_id, fdict in save_federations.items():
            if not isinstance(fdict, dict):
                continue
            start = None  # unknown without a valid start_date
            try:
                start = datamodel.date_to_days(fdict.get("start_date"))
            except (AttributeError, ValueError):
                pass
            roles = {}
            for key, is_associate in (("associates", True), ("members", False)):
                ids = fdict.get(key)
                for c_id in ids if isinstance(ids, list) else []:
                    country = countries.get(c_id)
                    if country is not None and country.is_real_country():
                        roles[c_id] = is_associate
            current[f_id] = (
                dump_name(fdict.get("name", "Unnamed Federation")),
                start,
                roles,
            )

        active = {
            f.federation_id_in_game: f
            for f in self._session.query(datamodel.Federation).filter(
                datamodel.Federation.end_date_days.is_(None)
            )
        }
        for f_id in sorted(active):
            federation = active[f_id]
            if f_id in current and current[f_id][1] in (None, federation.start_date_days):
                continue
            federation.end_date_days = max(federation.start_date_days, yesterday)
            for membership in self._open_memberships(federation):
                membership.end_date_days = max(membership.start_date_days, yesterday)
                self._add_event(
                    datamodel.HistoricalEventType.dissolved_federation,
                    membership.country,
                    federation,
                    today,
                )
            del active[f_id]

        for f_id in sorted(current):
            name, start, roles = current[f_id]
            federation = active.get(f_id)
            if federation is None:
                if start is None:
                    start = today
                federation = datamodel.Federation(
                    federation_id_in_game=f_id, name=name, start_date_days=start
                )
                self._session.add(federation)
                self._federations[f_id] = federation
                # without an earlier save the founding and joining dates are unknown
                founded = previous_date is not None and start > previous_date
                for c_id in sorted(roles):
                    is_associate = roles[c_id]
                    country = countries[c_id]
                    self._open_membership(
                        federation, country, is_associate, start if founded else today
                    )
                    if founded:
                        self._add_event(
                            datamodel.HistoricalEventType.joined_federation
                            if is_associate
                            else datamodel.HistoricalEventType.founded_federation,
                            country,
                            federation,
                            start,
                        )
                continue

            federation.name = name
            self._federations[f_id] = federation
            open_memberships = {
                m.country.country_id_in_game: m
                for m in self._open_memberships(federation)
            }
            for c_id in sorted(open_memberships):
                membership = open_memberships[c_id]
                if c_id not in roles:
                    membership.end_date_days = max(membership.start_date_days, yesterday)
                    self._add_event(
                        datamodel.HistoricalEventType.left_federation,
                        membership.country,
                        federation,
                        today,
                    )
            for c_id in sorted(roles):
                membership = open_memberships.get(c_id)
                if membership is not None:
                    if bool(membership.is_associate) == roles[c_id]:
                        continue
                    membership.end_date_days = max(membership.start_date_days, yesterday)
                country = countries[c_id]
                self._open_membership(federation, country, roles[c_id], today)
                self._add_event(
                    datamodel.HistoricalEventType.joined_federation,
                    country,
                    federation,
                    today,
                )

    @staticmethod
    def _open_memberships(federation: datamodel.Federation):
        return [m for m in federation.memberships if m.end_date_days is None]

    def _open_membership(self, federation, country, is_associate, start_date_days):
        self._session.add(
            datamodel.FederationMembership(
                federation=federation,
                country=country,
                is_associate=is_associate,
                start_date_days=start_date_days,
            )
        )

    def _add_event(self, event_type, country, federation, date_days):
        self._session.add(
            datamodel.HistoricalEvent(
                event_type=event_type,
                country=country,
                federation=federation,
                start_date_days=date_days,
                event_is_known_to_player=country.has_met_player(),
            )
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
                or not country_data_dict[country_id].country.is_real_country()
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


def president_start_date(
    last_succession_date: Any, previous_start_days: Optional[int], current_days: int
) -> int:
    """Uses the exact succession date only if it falls after the previous term began and not in the future."""
    exact = _date_to_days_or_none(last_succession_date)
    if exact is None or exact > current_days:
        return current_days
    if previous_start_days is not None and exact <= previous_start_days:
        return current_days
    return exact


class FederationProgressProcessor(AbstractGamestateDataProcessor):
    """Records federation presidencies (spans) and level-ups, and keeps each federation's type and level."""

    ID = "federation_progress"
    DEPENDENCIES = [CountryProcessor.ID, FederationProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        federations = dependencies[FederationProcessor.ID]
        today = self._basic_info.date_in_days
        save_federations = self._gamestate_dict.get("federation")
        if not isinstance(save_federations, dict):
            save_federations = {}

        previous_date = (
            self._session.query(sqlalchemy.func.max(datamodel.GameState.date))
            .filter(datamodel.GameState.game == self._db_game, datamodel.GameState.date < today)
            .scalar()
        )
        open_presidents: Dict[int, datamodel.HistoricalEvent] = {
            event.federation_id: event
            for event in self._session.query(datamodel.HistoricalEvent)
            .filter(
                datamodel.HistoricalEvent.event_type == datamodel.HistoricalEventType.federation_president,
                datamodel.HistoricalEvent.end_date_days.is_(None),
                datamodel.HistoricalEvent.federation_id.isnot(None),
            )
            .options(
                joinedload(datamodel.HistoricalEvent.country),
                joinedload(datamodel.HistoricalEvent.federation),
            )
        }

        for f_id, federation in sorted(federations.items()):
            # rows added in this save have no id yet, and no presidency
            current = open_presidents.pop(federation.federation_id, None) if federation.federation_id else None
            fdict = save_federations.get(f_id)
            progression = fdict.get("federation_progression") if isinstance(fdict, dict) else None
            president = countries.get(_as_game_id(fdict.get("leader"))) if isinstance(fdict, dict) else None
            if president is None or not isinstance(progression, dict):
                if current is not None:
                    open_presidents[federation.federation_id] = current
                continue
            federation_type = progression.get("federation_type")
            if isinstance(federation_type, str) and federation_type:
                federation.federation_type = federation_type
            # a federation founded after the previous save has a full history here
            newly_founded = previous_date is not None and federation.start_date_days > previous_date

            if current is None or current.country is not president:
                start = president_start_date(
                    progression.get("last_succession_date"),
                    current.start_date_days if current is not None else None,
                    today,
                )
                if current is None and newly_founded and start == today:
                    start = federation.start_date_days  # the founder presides until the first succession
                if current is not None:
                    current.end_date_days = max(start, current.start_date_days)
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.federation_president,
                        country=president,
                        federation=federation,
                        start_date_days=start,
                        event_is_known_to_player=president.has_met_player(),
                    )
                )

            level = progression.get("levels")
            if not isinstance(level, int):
                continue
            # a federation first seen at a higher level gives no date for its earlier level-ups
            known = federation.level if federation.level is not None else (1 if newly_founded else None)
            if known is not None and level > known:
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.federation_level_up,
                        country=president,
                        federation=federation,
                        start_date_days=today,
                        db_description=self._get_or_add_shared_description(str(level)),
                        event_is_known_to_player=president.has_met_player(),
                    )
                )
            federation.level = level

        # presidencies of dissolved federations end with them
        for event in open_presidents.values():
            if event.federation.end_date_days is not None or event.federation.federation_id_in_game not in federations:
                end = event.federation.end_date_days if event.federation.end_date_days is not None else today
                event.end_date_days = max(end, event.start_date_days)


def reached_crisis_levels(country_dict: Any) -> List[str]:
    """The crisis level keys a country has unlocked, in order; empty without a crisis path."""
    progression = country_dict.get("crisis_progression") if isinstance(country_dict, dict) else None
    if not isinstance(progression, dict):
        return []
    levels = progression.get("level")  # a repeated key: a string for one level, a list for more
    if isinstance(levels, str):
        levels = [levels]
    return [level for level in levels if isinstance(level, str)] if isinstance(levels, list) else []


class CrisisProgressProcessor(AbstractGamestateDataProcessor):
    """Records the crisis levels ("Become the Crisis", any path) countries unlock."""

    ID = "crisis_progress"
    DEPENDENCIES = [CountryProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        save_countries = self._gamestate_dict.get("country")
        if not isinstance(save_countries, dict):
            return
        for country_id, country in dependencies[CountryProcessor.ID].items():
            levels = reached_crisis_levels(save_countries.get(country_id))
            if not levels:
                continue
            # a country first seen with several levels gives no dates for its earlier ones
            known = country.crisis_level if country.crisis_level is not None else len(levels)
            if len(levels) > known:
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.crisis_level_up,
                        country=country,
                        start_date_days=self._basic_info.date_in_days,
                        db_description=self._get_or_add_shared_description(levels[-1]),
                        event_is_known_to_player=True,  # the crisis is visible galaxy-wide
                    )
                )
            country.crisis_level = len(levels)


@dataclasses.dataclass(frozen=True)
class PassedResolution:
    type: str
    date_days: int
    proposer: Optional[int]
    target: Optional[int]


CUSTODIAN_NOMINATION = "resolution_galacticreforms_nominate_custodian"
# the custodian, or an awakened empire controlling the Contingency, proclaims the Imperium
IMPERIUM_PROCLAMATIONS = frozenset(
    {"resolution_custodian_form_empire", "resolution_awakened_galactic_empire"}
)
CUSTODIAN_ENDINGS = frozenset(
    {"resolution_galacticreforms_unseat_custodian", "resolution_custodian_end_custodianship"}
)


def passed_resolutions(resolutions: Any) -> List[PassedResolution]:
    """All resolutions that ever passed, by pass date; the save keeps them after they are repealed or cancelled."""
    if not isinstance(resolutions, dict):
        return []
    passed = []
    for resolution in resolutions.values():
        if not isinstance(resolution, dict):
            continue
        resolution_type = resolution.get("type")
        date_days = _date_to_days_or_none(resolution.get("pass_date"))
        if not isinstance(resolution_type, str) or date_days is None:
            continue
        passed.append(
            PassedResolution(
                type=resolution_type,
                date_days=date_days,
                proposer=_as_game_id(resolution.get("country")),
                target=_as_game_id(resolution.get("target")),
            )
        )
    return sorted(passed, key=lambda r: (r.date_days, r.type))


def custodian_role_and_start(
    passed: List[PassedResolution], country_id: Optional[int], current_days: int
) -> Tuple[str, int]:
    """The office ("custodian" or "emperor") the Galactic Community leader holds, and when it began.

    The leader is emperor if the Imperium was proclaimed after the latest custodian nomination;
    a custodian's term starts with the nomination targeting them, or the current date if none matches.
    """
    passed = [r for r in passed if r.date_days <= current_days]
    proclamation = max((r.date_days for r in passed if r.type in IMPERIUM_PROCLAMATIONS), default=None)
    nominations = [r for r in passed if r.type == CUSTODIAN_NOMINATION]
    if proclamation is not None and proclamation >= max((r.date_days for r in nominations), default=proclamation):
        return "emperor", proclamation
    own = [r.date_days for r in nominations if r.target == country_id]
    return "custodian", max(own, default=current_days)


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
        previous_date = (
            self._session.query(sqlalchemy.func.max(datamodel.GameState.date))
            .filter(
                datamodel.GameState.game == self._db_game,
                datamodel.GameState.date < self._basic_info.date_in_days,
            )
            .scalar()
        )
        self._update_community_members(
            community_dict, self._founding_date(community_dict.get("community_formed"), previous_date)
        )
        self._update_council_members(
            community_dict, self._founding_date(community_dict.get("council_established"), previous_date)
        )
        resolutions = self._gamestate_dict.get("resolution")
        if not isinstance(resolutions, dict):
            resolutions = {}
        passed = passed_resolutions(resolutions)
        self._add_passed_resolutions(passed)
        self._update_custodian(community_dict, passed)

    def _add_passed_resolutions(self, passed: List["PassedResolution"]):
        event_type = datamodel.HistoricalEventType.galactic_resolution_passed
        known = set()  # (resolution type, pass date) of the stored events
        for event in self._session.query(datamodel.HistoricalEvent).filter_by(
            event_type=event_type
        ).options(joinedload(datamodel.HistoricalEvent.db_description)):
            if event.db_description is not None:
                known.add((event.db_description.text, event.start_date_days))
        for resolution in passed:
            if (resolution.type, resolution.date_days) in known:
                continue
            proposer = self._countries_dict.get(resolution.proposer)
            target = self._countries_dict.get(resolution.target)
            # the save drops the proposer once it no longer exists; the ledger lists events by country
            if proposer is None or resolution.date_days > self._basic_info.date_in_days:
                continue
            known.add((resolution.type, resolution.date_days))
            self._session.add(
                datamodel.HistoricalEvent(
                    event_type=event_type,
                    country=proposer,
                    target_country=target if target is not proposer else None,
                    start_date_days=resolution.date_days,
                    db_description=self._get_or_add_shared_description(resolution.type),
                    event_is_known_to_player=proposer.has_met_player(),
                )
            )

    def _update_custodian(self, community_dict, passed: List["PassedResolution"]):
        event_type = datamodel.HistoricalEventType.galactic_custodian
        now = self._basic_info.date_in_days
        current = self._countries_dict.get(_as_game_id(community_dict.get("leader")))
        role, start = custodian_role_and_start(
            passed, current.country_id_in_game if current else None, now
        )
        open_event = (
            self._session.query(datamodel.HistoricalEvent)
            .filter_by(event_type=event_type, end_date_days=None)
            .order_by(datamodel.HistoricalEvent.start_date_days.desc())
            .first()
        )
        if open_event is not None:
            open_role = open_event.db_description.text if open_event.db_description else None
            if current is not None and open_event.country == current and open_role == role:
                return
            start = max(start, open_event.start_date_days)
            if current is None:
                # a custodianship ended by resolution ends on its pass date, otherwise (term expiry) now
                start = max(
                    (
                        r.date_days
                        for r in passed
                        if r.type in CUSTODIAN_ENDINGS
                        and open_event.start_date_days <= r.date_days <= now
                    ),
                    default=now,
                )
            open_event.end_date_days = start
            self._session.add(open_event)
        if current is None:
            return
        self._session.add(
            datamodel.HistoricalEvent(
                event_type=event_type,
                country=current,
                start_date_days=start,
                db_description=self._get_or_add_shared_description(role),
                event_is_known_to_player=current.has_met_player(),
            )
        )

    def _founding_date(self, founded: Any, previous_date: Optional[int]) -> int:
        """The founding date if it lies after the previous save (the members seen now are the founders), else today."""
        today = self._basic_info.date_in_days
        founded = _date_to_days_or_none(founded)
        if previous_date is not None and founded is not None and previous_date < founded <= today:
            return founded
        return today

    def _update_community_members(self, community_dict, join_date: int):
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
            self._update_membership_events(c_id, matching_event_types, new_event_type, join_date)

        new_event_type = datamodel.HistoricalEventType.left_galactic_community
        for c_id in non_members:
            self._update_membership_events(c_id, matching_event_types, new_event_type)

    def _update_council_members(self, community_dict, join_date: int):
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
            self._update_membership_events(c_id, matching_event_types, new_event_type, join_date)

        new_event_type = datamodel.HistoricalEventType.left_galactic_council
        for c_id in non_members:
            self._update_membership_events(c_id, matching_event_types, new_event_type)

    def _update_membership_events(self, c_id, matching_event_types, new_event_type, start_date_days=None):
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
                    start_date_days=start_date_days if start_date_days is not None else self._basic_info.date_in_days,
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
        # preload all technologies with their descriptions in one query, instead
        # of a lazy relationship load per country plus one query per technology
        techs_by_country_id = collections.defaultdict(list)
        for tech in self._session.query(datamodel.Technology).options(
            joinedload(datamodel.Technology.db_description)
        ):
            techs_by_country_id[tech.country_id].append(tech)
        for country_id, country_model in countries_dict.items():
            self._history_add_tech_events(
                country_model,
                techs_by_country_id.get(country_model.country_id, []),
                self._gamestate_dict["country"][country_id],
            )

    def _history_add_tech_events(
        self,
        country_model: datamodel.Country,
        technologies: List[datamodel.Technology],
        country_dict,
    ):
        in_progress_techs = {}
        completed_techs = {}
        for t in technologies:
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
                    if matching_event is not None:  # not stored for special countries
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
            if raw_leader_dict.get("class") not in {"envoy", "official"}:
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

        # dead or dismissed envoys are no longer in the save, so their assignments end here
        for event in (
            self._session.query(datamodel.HistoricalEvent)
            .join(datamodel.HistoricalEvent.leader)
            .filter(
                datamodel.HistoricalEvent.end_date_days.is_(None),
                datamodel.HistoricalEvent.event_type.in_(datamodel.ENVOY_EVENT_TYPES),
                datamodel.Leader.is_active.is_(False),
            )
        ):
            event.end_date_days = max(
                event.start_date_days, self._basic_info.date_in_days - 1
            )
            self._session.add(event)

    def _previous_assignment(
        self, envoy: datamodel.Leader
    ) -> Optional[datamodel.HistoricalEvent]:
        # officials now do some assignments (galcom, federation) that envoys did previously
        # however, the previous assignment logic breaks when officials then do other things (like being a councilor)
        # to prevent this, we only consider actual envoy assignment events
        return (
            self._session.query(datamodel.HistoricalEvent)
            .filter(datamodel.HistoricalEvent.end_date_days.is_(None))
            .filter(
                datamodel.HistoricalEvent.event_type.in_(datamodel.ENVOY_EVENT_TYPES)
            )
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
        self._fleets_by_ingame_id = None

    def initialize_data(self):
        self._new_fleet_commands = {}
        self._fleet_compos = {}

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        self._leaders = dependencies[LeaderProcessor.ID]
        self._country_datas = dependencies[CountryDataProcessor.ID]
        self._fleet_owners = dependencies[FleetOwnershipProcessor.ID]
        # preload all known fleets once instead of one query per led ship per save
        self._fleets_by_ingame_id = {
            f.fleet_id_in_game: f for f in self._session.query(datamodel.Fleet)
        }

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
            fleet_model = self._fleets_by_ingame_id.get(fleet_id)
            if fleet_model is None:
                fleet_model = datamodel.Fleet(
                    name=fleet_name,
                    fleet_id_in_game=fleet_id,
                    is_civilian_fleet=self._get_ship_class(ship_dict) == "science",
                )
                self._session.add(fleet_model)
                self._fleets_by_ingame_id[fleet_id] = fleet_model
            elif fleet_model.name != fleet_name:
                fleet_model.name = fleet_name
                self._session.add(fleet_model)
            self._new_fleet_commands[leader_id] = fleet_model

    def _count_ship_for_fleet_composition(self, owner_id, ship_dict):
        ship_class = _fleet_composition_hull(self._get_ship_class(ship_dict))
        if ship_class is None:
            return
        if owner_id not in self._fleet_compos:
            self._fleet_compos[owner_id] = dict(
                ship_count_frigate=0,
                ship_count_corvette=0,
                ship_count_destroyer=0,
                ship_count_cruiser=0,
                ship_count_battleship=0,
                ship_count_titan=0,
                ship_count_colossus=0,
            )
        self._fleet_compos[owner_id][f"ship_count_{ship_class}"] += 1

    def _get_ship_class(self, ship_dict):
        return save_format.get_ship_size(self._gamestate_dict, ship_dict)

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


NO_GAME_ID = 4294967295  # the save's "none" value for IDs


def _as_game_id(value: Any) -> Optional[int]:
    """The value if it is a valid in-game ID; None for non-integers and the "none" ID."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value != NO_GAME_ID:
        return value
    return None


class AnomalyProcessor(AbstractGamestateDataProcessor):
    """Tracks anomalies from their discovery until their research is finished."""

    ID = "anomalies"
    DEPENDENCIES = [
        CountryProcessor.ID,
        PlanetProcessor.ID,
        LeaderProcessor.ID,
        FleetOwnershipProcessor.ID,
    ]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        planets = dependencies[PlanetProcessor.ID]
        leaders = dependencies[LeaderProcessor.ID]
        fleet_owners = dependencies[FleetOwnershipProcessor.ID]
        planet_dicts = (self._gamestate_dict.get("planets") or {}).get("planet")
        if not isinstance(planet_dicts, dict):
            return

        active = {}  # planet id -> (category, surveying country id)
        for planet_id, planet_dict in planet_dicts.items():
            if not isinstance(planet_dict, dict):
                continue
            category = planet_dict.get("anomaly")
            if isinstance(category, str) and category != "none":
                active[planet_id] = (category, _as_game_id(planet_dict.get("surveyed_by")))

        # one open discovered_anomaly event per anomaly that has not been researched yet
        open_events = {
            event.planet.planet_id_in_game: event
            for event in self._session.query(datamodel.HistoricalEvent)
            .filter(
                datamodel.HistoricalEvent.event_type
                == datamodel.HistoricalEventType.discovered_anomaly,
                datamodel.HistoricalEvent.end_date_days.is_(None),
                datamodel.HistoricalEvent.planet_id.isnot(None),
            )
            .options(
                joinedload(datamodel.HistoricalEvent.planet),
                joinedload(datamodel.HistoricalEvent.db_description),
            )
        }
        today = self._basic_info.date_in_days

        for planet_id, (category, surveyor_id) in sorted(active.items()):
            country = countries.get(surveyor_id)
            planet = planets.get(planet_id)
            if planet_id in open_events or country is None or planet is None:
                continue
            event = datamodel.HistoricalEvent(
                event_type=datamodel.HistoricalEventType.discovered_anomaly,
                country=country,
                planet=planet,
                system=planet.system,
                start_date_days=today,
                db_description=self._get_or_add_shared_description(category),
                event_is_known_to_player=country.has_met_player(),
            )
            self._session.add(event)
            open_events[planet_id] = event

        # the leader of the science ship researching an anomaly; the last one seen is kept
        for planet_id, scientist in self._scientists_researching(leaders).items():
            event = open_events.get(planet_id)
            if event is not None and planet_id in active and event.leader is not scientist:
                event.leader = scientist
                self._session.add(event)

        # an anomaly that is gone from its planet has been researched
        for planet_id, event in open_events.items():
            if planet_id in active or not isinstance(planet_dicts.get(planet_id), dict):
                continue
            event.end_date_days = today - 1
            self._session.add(event)
            if event.leader is None:
                continue
            country = event.leader.country or event.country
            self._session.add(
                datamodel.HistoricalEvent(
                    event_type=datamodel.HistoricalEventType.researched_anomaly,
                    country=country,
                    leader=event.leader,
                    planet=event.planet,
                    system=event.system,
                    start_date_days=today,
                    db_description=event.db_description,
                    event_is_known_to_player=country.has_met_player(),
                )
            )

    def _scientists_researching(self, leaders) -> Dict[int, datamodel.Leader]:
        """Maps the planet ids that are being researched to the leader of the researching ship."""
        scientists = {}
        for fleet_id, fleet_dict in sorted(self._gamestate_dict["fleet"].items()):
            if not isinstance(fleet_dict, dict):
                continue
            orders = fleet_dict.get("current_order")
            for order in orders if isinstance(orders, list) else [orders]:
                research = order.get("research_anomaly_order") if isinstance(order, dict) else None
                planet_id = _as_game_id(research.get("planet")) if isinstance(research, dict) else None
                ships = fleet_dict.get("ships")
                if planet_id is None or not isinstance(ships, list):
                    continue
                for ship_id in ships:
                    ship_dict = self._gamestate_dict["ships"].get(ship_id)
                    leader = leaders.get(ship_dict.get("leader")) if isinstance(ship_dict, dict) else None
                    if leader is not None:
                        scientists[planet_id] = leader
                        break
        return scientists


def _as_dict_list(value: Any) -> List[Dict[str, Any]]:
    """The dict entries of a save list; a lone entry is stored as a plain dict."""
    if isinstance(value, dict):
        value = [value]
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def dig_site_state(
    chapters_done: int, stage_count: Optional[int], excavating: bool
) -> datamodel.StorylineState:
    """Completed once all chapters are done, else active while a fleet excavates, else paused."""
    if stage_count and chapters_done >= stage_count:
        return datamodel.StorylineState.completed
    return datamodel.StorylineState.active if excavating else datamodel.StorylineState.paused


def astral_rift_state(status: Any) -> datamodel.StorylineState:
    """The storyline state of an astral rift from its save status."""
    if status == "in_progress":
        return datamodel.StorylineState.active
    if status == "finished":
        return datamodel.StorylineState.completed
    return datamodel.StorylineState.paused


FIRST_CONTACT_STORYLINE_STATES = {
    "in_progress": datamodel.StorylineState.active,
    "finished": datamodel.StorylineState.completed,
    "aborted": datamodel.StorylineState.abandoned,
}


def _storylines_by_game_id(
    session, storyline_type: datamodel.StorylineType
) -> Dict[int, datamodel.Storyline]:
    """The stored storylines of a type by in-game id; the newest wins if an id was reused."""
    storylines = {}
    for storyline in (
        session.query(datamodel.Storyline)
        .filter_by(storyline_type=storyline_type)
        .order_by(datamodel.Storyline.storyline_id)
    ):
        storylines[storyline.storyline_id_in_game] = storyline
    return storylines


def _set_if_changed(obj: Any, **values: Any) -> bool:
    """Sets the attributes that differ, so that unchanged rows cause no UPDATE; True if any did."""
    changed = False
    for name, value in values.items():
        if getattr(obj, name) is not value and getattr(obj, name) != value:
            setattr(obj, name, value)
            changed = True
    return changed


def _fleet_leader(
    gamestate_dict: Dict[str, Any], fleet_id: Any, leaders: Dict[int, datamodel.Leader]
) -> Optional[datamodel.Leader]:
    """The leader of the first ship of a fleet that has one."""
    fleet_dict = gamestate_dict["fleet"].get(fleet_id)
    ships = fleet_dict.get("ships") if isinstance(fleet_dict, dict) else None
    for ship_id in ships if isinstance(ships, list) else []:
        ship_dict = gamestate_dict["ships"].get(ship_id)
        leader = leaders.get(ship_dict.get("leader")) if isinstance(ship_dict, dict) else None
        if leader is not None:
            return leader
    return None


class ArchaeologyProcessor(AbstractGamestateDataProcessor):
    """Records each completed chapter of an archaeological dig site, and groups them as a storyline."""

    ID = "archaeology"
    DEPENDENCIES = [CountryProcessor.ID, PlanetProcessor.ID, LeaderProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        planets = dependencies[PlanetProcessor.ID]
        leaders = dependencies[LeaderProcessor.ID]
        sites = (self._gamestate_dict.get("archaeological_sites") or {}).get("sites")
        if not isinstance(sites, dict):
            return

        known = {}  # (planet id, chapter) -> stored event
        for event in self._session.query(datamodel.HistoricalEvent).filter_by(
            event_type=datamodel.HistoricalEventType.excavated_dig_site
        ).options(
            joinedload(datamodel.HistoricalEvent.planet),
            joinedload(datamodel.HistoricalEvent.db_description),
        ):
            if event.planet is not None and event.db_description is not None:
                _, chapter = datamodel.split_packed_description(event.db_description.text)
                known[(event.planet.planet_id_in_game, chapter)] = event
        storylines = _storylines_by_game_id(self._session, datamodel.StorylineType.dig_site)

        for site_id, site in sorted(sites.items()):
            if not isinstance(site, dict) or not isinstance(site.get("type"), str):
                continue
            location = site.get("location")
            planet = planets.get(_as_game_id(location.get("id")) if isinstance(location, dict) else None)
            if planet is None:
                continue
            entries = _as_dict_list(site.get("completed"))
            chapters = []  # (chapter, country, start day) of the valid entries
            for chapter, entry in enumerate(entries, start=1):
                country = countries.get(_as_game_id(entry.get("country")))
                date = entry.get("date")
                if country is None or not isinstance(date, str):
                    continue
                try:
                    chapters.append((chapter, country, datamodel.date_to_days(date)))
                except ValueError:
                    continue
            if not chapters:
                continue

            fleet_id = _as_game_id(site.get("excavator_fleet"))
            excavating = fleet_id is not None and fleet_id in self._gamestate_dict["fleet"]
            excavator = _fleet_leader(self._gamestate_dict, fleet_id, leaders) if excavating else None
            state = dig_site_state(len(entries), game_info.dig_site_stage_count(site["type"]), excavating)
            storyline = storylines.get(int(site_id))
            if storyline is None or storyline.planet is not planet:
                storyline = storylines[int(site_id)] = datamodel.Storyline(
                    storyline_type=datamodel.StorylineType.dig_site,
                    storyline_id_in_game=int(site_id),
                    title=site["type"],
                    planet=planet,
                    system=planet.system,
                    start_date_days=chapters[0][2],
                    state=state,
                )
            _set_if_changed(
                storyline,
                start_date_days=min(day for _, _, day in chapters),
                country=chapters[-1][1],
                leader=excavator or storyline.leader,
                state=state,
                end_date_days=chapters[-1][2] if state == datamodel.StorylineState.completed else None,
            )
            self._session.add(storyline)

            for chapter, country, start_date in chapters:
                key = (planet.planet_id_in_game, str(chapter))
                event = known.get(key)
                if event is None:
                    known[key] = event = datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.excavated_dig_site,
                        country=country,
                        leader=excavator,
                        planet=planet,
                        system=planet.system,
                        start_date_days=start_date,
                        db_description=self._get_or_add_shared_description(
                            f"{site['type']}|{chapter}"
                        ),
                        event_is_known_to_player=country.has_met_player(),
                    )
                    self._session.add(event)
                if _set_if_changed(event, storyline=storyline, stage_index=chapter):
                    self._session.add(event)


class AstralRiftProcessor(AbstractGamestateDataProcessor):
    """Records each completed exploration step of an astral rift, and groups them as a storyline."""

    ID = "astral_rifts"
    DEPENDENCIES = [CountryProcessor.ID, SystemProcessor.ID, LeaderProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        systems = dependencies[SystemProcessor.ID]["systems_by_ingame_id"]
        leaders = dependencies[LeaderProcessor.ID]
        rifts = (self._gamestate_dict.get("astral_rifts") or {}).get("rifts")
        if not isinstance(rifts, dict):
            return

        known = {}  # (system id, step event id, date) -> stored event
        for event in self._session.query(datamodel.HistoricalEvent).filter_by(
            event_type=datamodel.HistoricalEventType.explored_astral_rift
        ).options(
            joinedload(datamodel.HistoricalEvent.system),
            joinedload(datamodel.HistoricalEvent.db_description),
        ):
            if event.system is not None and event.db_description is not None:
                _, event_id = datamodel.split_packed_description(event.db_description.text)
                known[(event.system.system_id_in_game, event_id, event.start_date_days)] = event
        storylines = _storylines_by_game_id(self._session, datamodel.StorylineType.astral_rift)

        for rift_id, rift in sorted(rifts.items()):
            if not isinstance(rift, dict) or not isinstance(rift.get("type"), str):
                continue
            country = countries.get(_as_game_id(rift.get("explorer")))
            coordinate = rift.get("coordinate")
            system_id = _as_game_id(coordinate.get("origin")) if isinstance(coordinate, dict) else None
            system = systems.get(system_id)
            if country is None or system is None:
                continue
            leader = leaders.get(_as_game_id(rift.get("leader")))
            steps = []  # (position, event id, start day) of the valid entries
            for position, entry in enumerate(_as_dict_list(rift.get("completed")), start=1):
                event_id = entry.get("event_id")
                event_id = event_id if isinstance(event_id, str) else ""
                date = entry.get("date")
                if not isinstance(date, str):
                    continue
                try:
                    steps.append((position, event_id, datamodel.date_to_days(date)))
                except ValueError:
                    continue
            if not steps:
                continue

            state = astral_rift_state(rift.get("status"))
            storyline = storylines.get(int(rift_id))
            if storyline is None or storyline.system is not system:
                storyline = storylines[int(rift_id)] = datamodel.Storyline(
                    storyline_type=datamodel.StorylineType.astral_rift,
                    storyline_id_in_game=int(rift_id),
                    title=rift["type"],
                    system=system,
                    start_date_days=steps[0][2],
                    state=state,
                )
            _set_if_changed(
                storyline,
                start_date_days=steps[0][2],
                country=country,
                leader=leader or storyline.leader,
                state=state,
                end_date_days=steps[-1][2] if state == datamodel.StorylineState.completed else None,
            )
            self._session.add(storyline)

            for position, event_id, start_date in steps:
                key = (system_id, event_id, start_date)
                event = known.get(key)
                if event is None:
                    known[key] = event = datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.explored_astral_rift,
                        country=country,
                        leader=leader,
                        system=system,
                        start_date_days=start_date,
                        db_description=self._get_or_add_shared_description(
                            f"{rift['type']}|{event_id}"
                        ),
                        event_is_known_to_player=country.has_met_player(),
                    )
                    self._session.add(event)
                if _set_if_changed(event, storyline=storyline, stage_index=position):
                    self._session.add(event)


FIRST_CONTACT_STATUSES = ("in_progress", "finished", "aborted")


def first_contact_end_day(
    status: str, completed: Any, start_day: int, seen_open_on: Optional[int] = None
) -> Optional[int]:
    """The day a first contact process ended: None while in progress.

    A process seen open before ended in the day before this save; otherwise this is the last
    recorded stage, as the final stage is not always in the completed list.
    """
    if status == "in_progress":
        return None
    days = []
    for entry in _as_dict_list(completed):
        try:
            days.append(datamodel.date_to_days(entry["date"]))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    last_stage_day = max(days, default=start_day)
    return last_stage_day if seen_open_on is None else max(seen_open_on - 1, last_stage_day)


class FirstContactProcessor(AbstractGamestateDataProcessor):
    """Tracks the first contact processes of the save, with the envoy, system and outcome."""

    ID = "first_contact_process"
    DEPENDENCIES = [CountryProcessor.ID, SystemProcessor.ID, LeaderProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        systems = dependencies[SystemProcessor.ID]["systems_by_ingame_id"]
        leaders = dependencies[LeaderProcessor.ID]
        contacts = (self._gamestate_dict.get("first_contacts") or {}).get("contacts")
        if not isinstance(contacts, dict):
            return

        known = {}  # (owner id, met country id, start day) -> stored event
        for event in self._session.query(datamodel.HistoricalEvent).filter_by(
            event_type=datamodel.HistoricalEventType.first_contact_process
        ).options(
            joinedload(datamodel.HistoricalEvent.country),
            joinedload(datamodel.HistoricalEvent.target_country),
            joinedload(datamodel.HistoricalEvent.db_description),
        ):
            if event.country is not None and event.target_country is not None:
                known[
                    (
                        event.country.country_id_in_game,
                        event.target_country.country_id_in_game,
                        event.start_date_days,
                    )
                ] = event

        known_stages = set(
            self._session.query(
                datamodel.HistoricalEvent.storyline_id, datamodel.HistoricalEvent.stage_index
            ).filter(
                datamodel.HistoricalEvent.event_type == datamodel.HistoricalEventType.first_contact_stage
            )
        )
        storylines = _storylines_by_game_id(self._session, datamodel.StorylineType.first_contact)

        for contact_id, contact in sorted(contacts.items()):
            if not isinstance(contact, dict) or contact.get("status") not in FIRST_CONTACT_STATUSES:
                continue
            owner_id = _as_game_id(contact.get("owner"))
            target_id = _as_game_id(contact.get("country"))
            owner = countries.get(owner_id)
            target = countries.get(target_id)
            date = contact.get("date")
            if owner is None or target is None or not isinstance(date, str):
                continue
            try:
                start_day = datamodel.date_to_days(date)
            except ValueError:
                continue
            status = contact["status"]
            leader = leaders.get(_as_game_id(contact.get("leader")))
            key = (owner_id, target_id, start_day)
            event = known.get(key)
            was_open = event is not None and event.end_date_days is None
            end_day = first_contact_end_day(
                status,
                contact.get("completed"),
                start_day,
                self._basic_info.date_in_days if was_open else None,
            )
            if event is None:
                known[key] = event = datamodel.HistoricalEvent(
                    event_type=datamodel.HistoricalEventType.first_contact_process,
                    country=owner,
                    target_country=target,
                    leader=leader,
                    system=systems.get(_as_game_id(contact.get("location"))),
                    start_date_days=start_day,
                    end_date_days=end_day,
                    db_description=self._get_or_add_shared_description(status),
                    event_is_known_to_player=owner.has_met_player(),
                )
                self._session.add(event)
            else:
                changed = False
                if was_open and end_day is not None:
                    event.end_date_days = end_day
                    changed = True
                if event.db_description is None or event.db_description.text != status:
                    event.db_description = self._get_or_add_shared_description(status)
                    changed = True
                if event.leader is None and leader is not None:
                    event.leader = leader
                    changed = True
                if changed:
                    self._session.add(event)

            system = systems.get(_as_game_id(contact.get("location")))
            storyline = storylines.get(int(contact_id))
            if (
                storyline is None
                or storyline.country is not owner
                or storyline.target_country is not target
                or storyline.start_date_days != start_day
            ):
                storyline = storylines[int(contact_id)] = datamodel.Storyline(
                    storyline_type=datamodel.StorylineType.first_contact,
                    storyline_id_in_game=int(contact_id),
                    country=owner,
                    target_country=target,
                    start_date_days=start_day,
                    state=FIRST_CONTACT_STORYLINE_STATES[status],
                )
            outcome = contact.get("stage")
            _set_if_changed(
                storyline,
                leader=leader or storyline.leader,
                system=system or storyline.system,
                end_date_days=event.end_date_days,
                state=FIRST_CONTACT_STORYLINE_STATES[status],
                outcome=outcome if status != "in_progress" and isinstance(outcome, str) else None,
            )
            self._session.add(storyline)
            if _set_if_changed(event, storyline=storyline, stage_index=None):
                self._session.add(event)

            for index, entry in enumerate(_as_dict_list(contact.get("completed")), start=1):
                stage = entry.get("stage")
                stage_date = entry.get("date")
                if not isinstance(stage, str) or not isinstance(stage_date, str):
                    continue
                if storyline.storyline_id is not None and (storyline.storyline_id, index) in known_stages:
                    continue
                try:
                    stage_day = datamodel.date_to_days(stage_date)
                except ValueError:
                    continue
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.first_contact_stage,
                        country=owner,
                        target_country=target,
                        leader=leader or storyline.leader,
                        system=system,
                        start_date_days=stage_day,
                        db_description=self._get_or_add_shared_description(stage),
                        storyline=storyline,
                        stage_index=index,
                        event_is_known_to_player=owner.has_met_player(),
                    )
                )


# operations that restart after completing; others end when they reach full progress
REPEATABLE_OPERATIONS = frozenset(
    {"operation_acquire_asset", "operation_extort_favors", "operation_steal_technology"}
)
OPERATION_COMPLETE_INFO = 10


def _date_to_days_or_none(date: Any) -> Optional[int]:
    try:
        return datamodel.date_to_days(date)
    except (ValueError, TypeError, AttributeError):
        return None


def espionage_start_day(operation: Dict[str, Any]) -> Optional[int]:
    """The day of an operation's first roll, or None if it has not rolled yet."""
    for entry in _as_dict_list(operation.get("log")):
        day = _date_to_days_or_none(entry.get("date"))
        if day is not None:
            return day
    return None


def espionage_completion_days(operation: Dict[str, Any]) -> List[int]:
    """The days an operation was completed: its completed list, or the roll that filled a one-off operation."""
    days = [
        day
        for entry in _as_dict_list(operation.get("completed"))
        if (day := _date_to_days_or_none(entry.get("date"))) is not None
    ]
    if "completed" in operation or operation.get("type") in REPEATABLE_OPERATIONS:
        return days
    for entry in _as_dict_list(operation.get("log")):
        info, day = entry.get("info"), _date_to_days_or_none(entry.get("date"))
        if isinstance(info, (int, float)) and info >= OPERATION_COMPLETE_INFO and day is not None:
            return [day]
    return []


class EspionageProcessor(AbstractGamestateDataProcessor):
    """Tracks the espionage operations of spy networks while they run and when they complete."""

    ID = "espionage"
    DEPENDENCIES = [CountryProcessor.ID, LeaderProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        leaders = dependencies[LeaderProcessor.ID]
        operations = (self._gamestate_dict.get("espionage_operations") or {}).get("operations")
        networks = self._gamestate_dict.get("spy_networks")
        if not isinstance(operations, dict) or not isinstance(networks, dict):
            return
        today = self._basic_info.date_in_days
        event_type = datamodel.HistoricalEventType

        running = {}  # operation id -> stored span, while the operation is in the save
        known_completions = set()  # (owner id, target id, operation type, day) of the stored events
        for event in self._session.query(datamodel.HistoricalEvent).filter(
            datamodel.HistoricalEvent.event_type.in_(
                [event_type.espionage_operation, event_type.completed_espionage_operation]
            )
        ).options(
            joinedload(datamodel.HistoricalEvent.country),
            joinedload(datamodel.HistoricalEvent.target_country),
            joinedload(datamodel.HistoricalEvent.db_description),
        ):
            if event.country is None or event.target_country is None or event.db_description is None:
                continue
            operation_type, operation_id = datamodel.split_packed_description(event.db_description.text)
            if event.event_type == event_type.espionage_operation:
                running[operation_id] = event
            else:
                known_completions.add(
                    (
                        event.country.country_id_in_game,
                        event.target_country.country_id_in_game,
                        operation_type,
                        event.start_date_days,
                    )
                )

        live_ids = set()
        for operation_id, operation in sorted(operations.items()):
            network = networks.get(operation.get("spy_network")) if isinstance(operation, dict) else None
            target = operation.get("target") if isinstance(operation, dict) else None
            if not isinstance(network, dict) or not isinstance(target, dict):
                continue
            operation_type = operation.get("type")
            owner_id = _as_game_id(network.get("owner"))
            target_id = _as_game_id(target.get("id"))
            owner = countries.get(owner_id)
            target_country = countries.get(target_id)
            if not isinstance(operation_type, str) or owner is None or target_country is None:
                continue
            leader = leaders.get(_as_game_id(network.get("leader")))
            live_ids.add(str(operation_id))

            event = running.get(str(operation_id))
            if event is None:
                running[str(operation_id)] = event = datamodel.HistoricalEvent(
                    event_type=event_type.espionage_operation,
                    country=owner,
                    target_country=target_country,
                    leader=leader,
                    start_date_days=espionage_start_day(operation) or today,
                    db_description=self._get_or_add_shared_description(
                        f"{operation_type}|{operation_id}"
                    ),
                    event_is_known_to_player=owner.has_met_player(),
                )
                self._session.add(event)
            elif event.leader is None and leader is not None:
                event.leader = leader
                self._session.add(event)

            for day in espionage_completion_days(operation):
                key = (owner_id, target_id, operation_type, day)
                if key in known_completions:
                    continue
                known_completions.add(key)
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=event_type.completed_espionage_operation,
                        country=owner,
                        target_country=target_country,
                        leader=leader,
                        start_date_days=day,
                        db_description=self._get_or_add_shared_description(operation_type),
                        event_is_known_to_player=owner.has_met_player(),
                    )
                )

        # an operation that is gone from the save has ended
        for operation_id, event in running.items():
            if operation_id not in live_ids and event.end_date_days is None:
                event.end_date_days = max(today - 1, event.start_date_days)
                self._session.add(event)


class RelicProcessor(AbstractGamestateDataProcessor):
    """Records the relics owned by each country.

    The save has no acquisition dates, so a relic is dated by the first save that shows it.
    """

    ID = "relics"
    DEPENDENCIES = [CountryProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        known = set()  # (country id, relic) of the stored events
        for event in self._session.query(datamodel.HistoricalEvent).filter_by(
            event_type=datamodel.HistoricalEventType.acquired_relic
        ).options(
            joinedload(datamodel.HistoricalEvent.country),
            joinedload(datamodel.HistoricalEvent.db_description),
        ):
            if event.country is not None and event.db_description is not None:
                known.add((event.country.country_id_in_game, event.db_description.text))

        for country_id, country_dict in sorted(self._gamestate_dict["country"].items()):
            country = countries.get(country_id)
            relics = country_dict.get("relics") if isinstance(country_dict, dict) else None
            if country is None or not isinstance(relics, list):
                continue
            for relic in relics:
                if not isinstance(relic, str) or (country_id, relic) in known:
                    continue
                known.add((country_id, relic))
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.acquired_relic,
                        country=country,
                        start_date_days=self._basic_info.date_in_days,
                        db_description=self._get_or_add_shared_description(relic),
                        event_is_known_to_player=country.has_met_player(),
                    )
                )


RING_WORLD_FAMILY = "ring_world_3_intermediate"
# family final key -> (stage keys in build order, index of the first finished stage), from common/megastructures
_MEGASTRUCTURE_FAMILIES: Dict[str, Tuple[Tuple[str, ...], int]] = {
    "dyson_sphere_5": (tuple(f"dyson_sphere_{i}" for i in range(6)), 5),
    RING_WORLD_FAMILY: (
        ("ring_world_1", "ring_world_2_intermediate", "ring_world_2", "ring_world_3_intermediate"),
        3,
    ),
    "think_tank_3": (tuple(f"think_tank_{i}" for i in range(5)), 3),  # _4 is the Groik Nexus upgrade
    "spy_orb_4": (tuple(f"spy_orb_{i}" for i in range(5)), 4),
    "matter_decompressor_4": (tuple(f"matter_decompressor_{i}" for i in range(5)), 4),
    "mega_art_installation_3": (tuple(f"mega_art_installation_{i}" for i in range(5)), 3),
    "interstellar_assembly_4": (tuple(f"interstellar_assembly_{i}" for i in range(5)), 4),
    "quantum_catapult_3": (tuple(f"quantum_catapult_{i}" for i in range(4)), 3),
    "strategic_coordination_center_3": (
        tuple(f"strategic_coordination_center_{i}" for i in range(4)),
        3,
    ),
    "mega_shipyard_3": (tuple(f"mega_shipyard_{i}" for i in range(4)), 3),
    "dyson_gun_3": (tuple(f"dyson_gun_{i}" for i in range(4)), 3),
    "galactic_crucible_4": (tuple(f"galactic_crucible_{i}" for i in range(6)), 4),
    "crisis_sphere_4": (tuple(f"crisis_sphere_{i}" for i in range(5)), 4),
}
# stage key -> (family final key, stage index, index of the first finished stage)
MEGASTRUCTURE_STAGES: Dict[str, Tuple[str, int, int]] = {
    stage: (final, index, finished_index)
    for final, (stages, finished_index) in _MEGASTRUCTURE_FAMILIES.items()
    for index, stage in enumerate(stages)
}
MEGASTRUCTURE_START_EVENTS = ("timeline_megastructure", "timeline_first_megastructure")


def megastructure_final_type(ms_type: Any) -> Optional[str]:
    """The finished stage's key of a tracked megastructure's family; None if not tracked."""
    stage = MEGASTRUCTURE_STAGES.get(ms_type) if isinstance(ms_type, str) else None
    return stage[0] if stage else None


def is_finished_megastructure(ms_type: Any) -> bool:
    stage = MEGASTRUCTURE_STAGES.get(ms_type) if isinstance(ms_type, str) else None
    return stage is not None and stage[1] >= stage[2]


def collect_megastructure_starts(countries: Any) -> Dict[int, Tuple[int, int]]:
    """Collect megastructure id -> (builder country id, earliest date_days) from the timelines."""
    starts: Dict[int, Tuple[int, int]] = {}
    for _, data, date_days in iter_timeline_events(countries, MEGASTRUCTURE_START_EVENTS):
        if len(data) < 2:
            continue
        builder_id, megastructure_id = _as_game_id(data[0]), _as_game_id(data[1])
        if builder_id is None or megastructure_id is None:
            continue
        known = starts.get(megastructure_id)
        if known is None or date_days < known[1]:
            starts[megastructure_id] = (builder_id, date_days)
    return starts


class MegastructureProcessor(AbstractGamestateDataProcessor):
    """Records megastructure constructions, from the site's creation until a finished stage."""

    ID = "megastructures"
    DEPENDENCIES = [CountryProcessor.ID, SystemProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        systems = dependencies[SystemProcessor.ID]["systems_by_ingame_id"]
        megastructures = self._gamestate_dict.get("megastructures")
        if not isinstance(megastructures, dict):
            return

        stored = {}  # megastructure id -> event
        for event in self._session.query(datamodel.HistoricalEvent).filter_by(
            event_type=datamodel.HistoricalEventType.megastructure_construction
        ).options(joinedload(datamodel.HistoricalEvent.db_description)):
            if event.db_description is not None:
                _, ms_id = datamodel.split_packed_description(event.db_description.text)
                if ms_id.isdigit():
                    stored[int(ms_id)] = event

        starts = collect_megastructure_starts(self._gamestate_dict.get("country"))
        for ms_id, (builder_id, date_days) in sorted(
            starts.items(), key=lambda item: (item[1][1], item[0])
        ):
            megastructure = megastructures.get(ms_id)
            if ms_id in stored or not isinstance(megastructure, dict):
                continue
            ms_type = megastructure.get("type")
            final_type = megastructure_final_type(ms_type)
            builder = countries.get(builder_id)
            if final_type is None or builder is None:
                continue
            coordinate = megastructure.get("coordinate")
            origin = coordinate.get("origin") if isinstance(coordinate, dict) else None
            self._session.add(
                datamodel.HistoricalEvent(
                    event_type=datamodel.HistoricalEventType.megastructure_construction,
                    country=builder,
                    system=systems.get(origin),
                    start_date_days=date_days,
                    end_date_days=date_days if is_finished_megastructure(ms_type) else None,
                    db_description=self._get_or_add_shared_description(f"{final_type}|{ms_id}"),
                    event_is_known_to_player=builder.has_met_player(),
                )
            )

        for ms_id, event in stored.items():
            if event.end_date_days is not None:
                continue
            megastructure = megastructures.get(ms_id)
            if isinstance(megastructure, dict):
                finished = is_finished_megastructure(megastructure.get("type"))
            else:
                # completing a ring world frame replaces it with new section megastructures
                family, _ = datamodel.split_packed_description(event.db_description.text)
                finished = family == RING_WORLD_FAMILY
            if finished:
                event.end_date_days = max(self._basic_info.date_in_days, event.start_date_days)
                self._session.add(event)


class LeviathanProcessor(AbstractGamestateDataProcessor):
    """Records the destruction of leviathans, as reported by the countries' timeline events."""

    ID = "leviathans"
    DEPENDENCIES = [CountryProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        known = set()  # (country id, date) of the stored events
        for event in self._session.query(datamodel.HistoricalEvent).filter_by(
            event_type=datamodel.HistoricalEventType.destroyed_leviathan
        ).options(
            joinedload(datamodel.HistoricalEvent.country),
            joinedload(datamodel.HistoricalEvent.target_country),
        ):
            if event.country is not None:
                known.add((event.country.country_id_in_game, event.start_date_days))

        kills = collect_leviathan_kills(self._gamestate_dict.get("country"))
        for country_id, target_id, date_days in sorted(
            kills, key=lambda k: (k[2], k[0], k[1] or -1)
        ):
            country = countries.get(country_id)
            target = countries.get(target_id)
            # the leviathan's country may no longer exist in later saves, so it is not part of the key
            key = (country_id, date_days)
            if country is None or key in known:
                continue
            known.add(key)
            self._session.add(
                datamodel.HistoricalEvent(
                    event_type=datamodel.HistoricalEventType.destroyed_leviathan,
                    country=country,
                    target_country=target,
                    start_date_days=date_days,
                    event_is_known_to_player=country.has_met_player(),
                )
            )


class GalaxyEventProcessor(AbstractGamestateDataProcessor):
    """Records galaxy-wide crises and events, see galaxy_events."""

    ID = "galaxy_events"
    DEPENDENCIES = [CountryProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        known_keys = set()
        known_pairs = set()  # (key, country id) of the stored events
        for event in self._session.query(datamodel.HistoricalEvent).filter_by(
            event_type=datamodel.HistoricalEventType.galactic_crisis
        ).options(
            joinedload(datamodel.HistoricalEvent.country),
            joinedload(datamodel.HistoricalEvent.db_description),
        ):
            if event.country is not None and event.db_description is not None:
                known_keys.add(event.db_description.text)
                known_pairs.add((event.db_description.text, event.country.country_id_in_game))

        for galaxy_event in galaxy_events.collect_galaxy_events(
            self._gamestate_dict.get("flags"),
            self._gamestate_dict.get("country"),
            self._basic_info.date_in_days,
        ):
            if galaxy_event.all_countries:
                targets = [countries.get(cid) for cid in galaxy_event.country_ids]
            elif galaxy_event.key in known_keys:
                continue
            else:
                targets = [self._find_country(galaxy_event, countries)]
            for country in targets:
                if country is None:
                    continue  # retried with the next save
                pair = (galaxy_event.key, country.country_id_in_game)
                if pair in known_pairs:
                    continue
                known_keys.add(galaxy_event.key)
                known_pairs.add(pair)
                self._session.add(
                    datamodel.HistoricalEvent(
                        event_type=datamodel.HistoricalEventType.galactic_crisis,
                        country=country,
                        start_date_days=galaxy_event.date_days,
                        db_description=self._get_or_add_shared_description(galaxy_event.key),
                        event_is_known_to_player=True,  # announced to the whole galaxy
                    )
                )

    def _find_country(
        self, galaxy_event: galaxy_events.GalaxyEvent, countries: Dict[int, datamodel.Country]
    ) -> Optional[datamodel.Country]:
        for country_id in galaxy_event.country_ids:
            if country_id in countries:
                return countries[country_id]
        # a defeated crisis country is gone from the gamestate but kept in the database
        known = (
            self._session.query(datamodel.Country)
            .filter(
                datamodel.Country.game == self._db_game,
                datamodel.Country.country_type.in_(galaxy_event.country_types),
            )
            .all()
        )
        known.sort(key=lambda c: galaxy_event.country_types.index(c.country_type))
        return known[0] if known else None


def collect_subject_relations(countries: Any) -> Dict[int, Tuple[int, Optional[int]]]:
    """Map each subject to (overlord, date_days the relation began) from the countries' overlord fields."""
    relations = {}
    if not isinstance(countries, dict):
        return relations
    for country_id, country in countries.items():
        if not isinstance(country, dict):
            continue
        overlord = _as_game_id(country.get("overlord"))
        if overlord is None or overlord == country_id:
            continue
        relations[country_id] = (overlord, _date_to_days_or_none(country.get("subject_date")))
    return relations


def collect_agreement_presets(agreements: Any) -> Dict[Tuple[int, int], str]:
    """Map (overlord, subject) to the agreement preset of their subject agreement."""
    presets = {}
    agreements = agreements.get("agreements") if isinstance(agreements, dict) else None
    if not isinstance(agreements, dict):
        return presets
    for agreement in agreements.values():
        if not isinstance(agreement, dict):
            continue
        owner, target = _as_game_id(agreement.get("owner")), _as_game_id(agreement.get("target"))
        term_data = agreement.get("term_data")
        preset = term_data.get("agreement_preset") if isinstance(term_data, dict) else None
        if owner is not None and target is not None and isinstance(preset, str):
            presets[(owner, target)] = preset
    return presets


class CountryDestroyedProcessor(AbstractGamestateDataProcessor):
    """Records the end of empires: a country that was in the previous save and is gone from this one.

    Dated by the war it lost if that ended after the previous save, otherwise by the save date.
    """

    ID = "country_destroyed"
    DEPENDENCIES = [CountryProcessor.ID]
    # a war lost this long before the previous save still explains the country's end
    WAR_LOOKBACK_DAYS = 360

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        destroyed_type = datamodel.HistoricalEventType.country_destroyed
        already_destroyed = {
            country_id
            for (country_id,) in self._session.query(datamodel.HistoricalEvent.country_id).filter_by(
                event_type=destroyed_type
            )
        }
        now = self._basic_info.date_in_days
        previous_date = (
            self._session.query(sqlalchemy.func.max(datamodel.GameState.date))
            .filter(
                datamodel.GameState.game == self._db_game,
                datamodel.GameState.date < now,
            )
            .scalar()
        )
        if previous_date is None:
            return
        # only countries of the previous save, so countries that ended before tracking get no event
        previous_countries = (
            self._session.query(datamodel.Country)
            .join(datamodel.CountryData, datamodel.CountryData.country_id == datamodel.Country.country_id)
            .filter(datamodel.Country.game == self._db_game, datamodel.CountryData.date == previous_date)
            .all()
        )
        gone = [
            country
            for country in previous_countries
            if country.country_id_in_game not in countries
            and country.country_id not in already_destroyed
            and country.is_real_country()
        ]
        if not gone:
            return

        lost_wars = collect_war_results(self._gamestate_dict.get("country"))
        for country in gone:
            date_days, cause, target = now, "", None
            defeats = [
                (date, winner)
                for (winner, loser, date) in lost_wars
                if loser == country.country_id_in_game
                and previous_date - self.WAR_LOOKBACK_DAYS <= date <= now
            ]
            overlord_event = (
                self._session.query(datamodel.HistoricalEvent)
                .filter_by(
                    event_type=datamodel.HistoricalEventType.became_subject,
                    country=country,
                    end_date_days=None,
                )
                .first()
            )
            if defeats:
                war_date, winner = max(defeats)
                cause, target = "war", countries.get(winner)
                if war_date > previous_date:
                    date_days = war_date
            elif overlord_event is not None:
                cause, target = "subject", overlord_event.target_country
            self._session.add(
                datamodel.HistoricalEvent(
                    event_type=destroyed_type,
                    country=country,
                    target_country=target,
                    start_date_days=date_days,
                    db_description=self._get_or_add_shared_description(cause) if cause else None,
                    event_is_known_to_player=country.has_met_player(),
                )
            )


class SubjectProcessor(AbstractGamestateDataProcessor):
    """Records subject relations between empires as spans from the exact start date until they end."""

    ID = "subjects"
    DEPENDENCIES = [CountryProcessor.ID]

    def extract_data_from_gamestate(self, dependencies: Dict[str, Any]):
        countries = dependencies[CountryProcessor.ID]
        event_type = datamodel.HistoricalEventType.became_subject
        now = self._basic_info.date_in_days
        relations = collect_subject_relations(self._gamestate_dict.get("country"))
        presets = collect_agreement_presets(self._gamestate_dict.get("agreements"))

        open_events = {}
        for event in self._session.query(datamodel.HistoricalEvent).filter_by(
            event_type=event_type, end_date_days=None
        ).options(
            joinedload(datamodel.HistoricalEvent.country),
            joinedload(datamodel.HistoricalEvent.target_country),
        ):
            open_events[event.country.country_id_in_game] = event

        for subject_id, (overlord_id, start_days) in sorted(relations.items()):
            subject, overlord = countries.get(subject_id), countries.get(overlord_id)
            if (
                subject is None
                or overlord is None
                or not subject.is_real_country()
                or not overlord.is_real_country()
            ):
                continue
            event = open_events.pop(subject_id, None)
            if event is not None and event.target_country == overlord:
                continue
            if start_days is None or start_days > now:
                start_days = now
            if event is not None:
                start_days = max(start_days, event.start_date_days)
                event.end_date_days = start_days
                self._session.add(event)
            preset = presets.get((overlord_id, subject_id))
            self._session.add(
                datamodel.HistoricalEvent(
                    event_type=event_type,
                    country=subject,
                    target_country=overlord,
                    start_date_days=start_days,
                    db_description=self._get_or_add_shared_description(preset) if preset else None,
                    event_is_known_to_player=subject.has_met_player() and overlord.has_met_player(),
                )
            )

        # relations that ended: released, integrated, or one side no longer exists
        for event in open_events.values():
            event.end_date_days = now
            self._session.add(event)


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
        if isinstance(war_dict.get("name"), dict):
            war_model.name = dump_name(war_dict["name"])
        war_model.attacker_war_exhaustion = war_dict.get("attacker_war_exhaustion", 0.0)
        war_model.defender_war_exhaustion = war_dict.get("defender_war_exhaustion", 0.0)
        war_model.end_date_days = self._basic_info.date_in_days - 1
        self._session.add(war_model)
        return war_model

    @staticmethod
    def _parse_war_goal(war_goal):
        if isinstance(war_goal, dict):
            return war_goal.get("type")
        return None  # missing or "none"

    def update_war_participants(self, war_dict, war_model):
        war_goal_attacker = self._parse_war_goal(war_dict.get("attacker_war_goal"))
        war_goal_defender = self._parse_war_goal(war_dict.get("defender_war_goal"))
        attackers = {
            p.get("country")
            for p in save_format._as_list(war_dict.get("attackers"))
            if isinstance(p, dict) and p.get("country") is not None
        }
        for war_party_info in itertools.chain(
            save_format._as_list(war_dict.get("attackers")),
            save_format._as_list(war_dict.get("defenders")),
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
                self._session.add(war_participant)  # before the autoflushing query below
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

            planet_model = self._planet_models_dict.get(_extract_id(battle_dict.get("planet")))
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
                        f"{db_country.country_name} and war {war.war_id_in_game}."
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


# country timeline events reporting a war result; True if data is [winner, loser]
WAR_RESULT_EVENTS = {
    "timeline_war_won": True,
    "timeline_first_war_won": True,
    "timeline_war_lost": False,
    "timeline_first_war_lost": False,
}


@dataclasses.dataclass(frozen=True)
class WarTruceCandidate:
    """The in-game country IDs of an unresolved war that can be matched to a truce."""

    war: Any
    attacker_leader: Optional[int]
    defender_leader: Optional[int]
    participants: frozenset

    @staticmethod
    def from_war(war: datamodel.War) -> "WarTruceCandidate":
        def leader_of(participants: List[datamodel.WarParticipant]) -> Optional[int]:
            # the primary participant started the war; allies are called in later
            primary = [wp for wp in participants if wp.call_type == "primary"]
            leader = (primary or participants or [None])[0]
            return None if leader is None else leader.country.country_id_in_game

        participants = sorted(war.participants, key=lambda wp: wp.warparticipant_id)
        return WarTruceCandidate(
            war=war,
            attacker_leader=leader_of([wp for wp in participants if wp.is_attacker]),
            defender_leader=leader_of(
                [wp for wp in participants if not wp.is_attacker]
            ),
            participants=frozenset(
                wp.country.country_id_in_game for wp in participants
            ),
        )


def match_truces_to_wars(
    truces: Dict[Any, Collection[int]], candidates: Collection[WarTruceCandidate]
) -> Dict[Any, WarTruceCandidate]:
    """Match each truce to the unresolved war it ended, if any.

    A war matches a truce if the truce includes the leaders of both sides (or has
    exactly the same countries as the war). If several wars match, the one sharing the
    most countries with the truce is chosen. Each war is matched at most once.
    """
    remaining = list(candidates)
    matches = {}
    for truce_id, countries in truces.items():
        countries = frozenset(countries)
        matching = [
            c
            for c in remaining
            if c.participants == countries
            or (c.attacker_leader in countries and c.defender_leader in countries)
        ]
        if not matching:
            continue
        best = max(
            matching,
            key=lambda c: (len(c.participants & countries), -len(c.participants)),
        )
        matches[truce_id] = best
        remaining.remove(best)
    return matches


@dataclasses.dataclass(frozen=True)
class WarSidesCandidate:
    """An unresolved war with the in-game country IDs on each side."""

    war: Any
    last_seen_days: int
    attackers: frozenset
    defenders: frozenset

    @staticmethod
    def from_war(war: datamodel.War) -> "WarSidesCandidate":
        return WarSidesCandidate(
            war=war,
            last_seen_days=war.end_date_days or 0,
            attackers=frozenset(
                wp.country.country_id_in_game for wp in war.participants if wp.is_attacker
            ),
            defenders=frozenset(
                wp.country.country_id_in_game
                for wp in war.participants
                if not wp.is_attacker
            ),
        )


def _is_country_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def iter_timeline_events(
    countries: Any, definitions: Collection[str]
) -> Iterable[Tuple[str, List[Any], int]]:
    """Yield (definition, data, date_days) of the countries' timeline events with one of the
    definitions, skipping malformed entries."""
    if not isinstance(countries, dict):
        return
    for country in countries.values():
        events = country.get("timeline_events") if isinstance(country, dict) else None
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            definition = event.get("definition")
            data = event.get("data")
            date = event.get("date")
            if definition not in definitions or not isinstance(date, str):
                continue
            if not isinstance(data, list):
                continue
            try:
                date_days = datamodel.date_to_days(date)
            except ValueError:
                continue
            yield definition, data, date_days


def collect_war_results(countries: Any) -> Set[Tuple[int, int, int]]:
    """Collect the (winner, loser, date_days) of all won/lost wars in the countries' timelines."""
    results = set()
    for definition, data, date_days in iter_timeline_events(
        countries, WAR_RESULT_EVENTS
    ):
        if len(data) < 2 or not (_is_country_id(data[0]) and _is_country_id(data[1])):
            continue
        if WAR_RESULT_EVENTS[definition]:
            results.add((data[0], data[1], date_days))
        else:
            results.add((data[1], data[0], date_days))
    return results


def collect_leviathan_kills(countries: Any) -> Set[Tuple[int, Optional[int], int]]:
    """Collect the (country, leviathan country or None, date_days) of all destroyed leviathans."""
    kills = set()
    for _, data, date_days in iter_timeline_events(
        countries, {"timeline_destroyed_leviathan"}
    ):
        if not data or not _is_country_id(data[0]):
            continue
        target = data[1] if len(data) > 1 and _is_country_id(data[1]) else None
        kills.add((data[0], target, date_days))
    return kills


def match_results_to_wars(
    results: Collection[Tuple[int, int, int]],
    candidates: Collection[WarSidesCandidate],
    current_days: int,
) -> Dict[Any, Tuple[bool, int]]:
    """Match unresolved wars to the won/lost results that ended them.

    A result matches a war if it is dated after the war was last seen (and not after
    the current date) and the winner and loser are on opposite sides of the war. The
    earliest matching result is used, and each result resolves at most one war.
    Returns {war: (attacker_won, end_date_days)}.
    """
    remaining = sorted(results, key=lambda r: r[2])
    matches = {}
    for candidate in sorted(candidates, key=lambda c: c.last_seen_days):
        for result in remaining:
            winner, loser, date_days = result
            if not candidate.last_seen_days < date_days <= current_days:
                continue
            if winner in candidate.attackers and loser in candidate.defenders:
                attacker_won = True
            elif winner in candidate.defenders and loser in candidate.attackers:
                attacker_won = False
            else:
                continue
            matches[candidate.war] = (attacker_won, date_days)
            remaining.remove(result)
            break
    return matches


class TruceProcessor(AbstractGamestateDataProcessor):
    ID = "truces"
    DEPENDENCIES = [
        CountryProcessor.ID,
        RulerEventProcessor.ID,
        DiplomacyDictProcessor.ID,
        WarProcessor.ID,
    ]

    def __init__(self):
        super().__init__()
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
        candidates = [WarTruceCandidate.from_war(w) for w in unresolved_wars]

        truce_countries = {}
        for truce_id, countries in diplo_truces.items():
            truce_info = truces_dict.get(truce_id)
            if not isinstance(truce_info, dict):
                continue
            truce_type = truce_info.get("truce_type", "other")
            if truce_type != "war":
                continue  # truce is due to diplomatic agreements or similar, ignore
            truce_countries[truce_id] = countries

        #  resolve wars based on truces...
        resolved = set()
        for truce_id, candidate in match_truces_to_wars(
            truce_countries, candidates
        ).items():
            truce_info = truces_dict[truce_id]
            matching_war = candidate.war
            resolved.add(matching_war.war_id)

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

        #  ...then based on the war results in the countries' timelines (wars won outright)
        sides = [
            WarSidesCandidate.from_war(w)
            for w in unresolved_wars
            if w.war_id not in resolved
        ]
        for war, (attacker_won, end_date) in match_results_to_wars(
            collect_war_results(self._gamestate_dict.get("country")),
            sides,
            self._basic_info.date_in_days,
        ).items():
            resolved.add(war.war_id)
            war.end_date_days = end_date
            war.outcome = (
                datamodel.WarOutcome.attacker_victory
                if attacker_won
                else datamodel.WarOutcome.defender_victory
            )
            self._history_add_peace_events(war)
            self._session.add(war)

        # resolve the wars that are no longer in the save file and have no known result:
        for war in unresolved_wars:
            if war.war_id in resolved:
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
        PlanetProcessor.ID,
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
        planets_by_ingame_id = dependencies[PlanetProcessor.ID]

        # create a mapping from pop_groups to job assignments, to be used later for stats_by_job
        pop_jobs_dict = self._gamestate_dict.get("pop_jobs", {})
        if not isinstance(pop_jobs_dict, dict):
            pop_jobs_dict = {}
        pop_group_to_jobs = dict()
        for pop_job in pop_jobs_dict.values():
            if not isinstance(pop_job, dict):
                continue
            pop_groups = pop_job.get("pop_groups", [])
            if isinstance(pop_groups, dict):
                pop_groups = [pop_groups]
            for pop_group in pop_groups:
                pop_group_to_jobs.setdefault(pop_group["pop_group"], []).append({
                    "job": pop_job["type"],
                    "amount": pop_group["amount"],
                })

        pop_groups_dict = self._gamestate_dict.get("pop_groups", {})
        if not isinstance(pop_groups_dict, dict):
            pop_groups_dict = {}
        for country_id_in_game, country_model in countries_dict.items():
            if not country_model.is_real_country():
                continue
            country_data = country_data_dict[country_id_in_game]
            stats_by_species = {}
            stats_by_faction = {}
            stats_by_job = {}
            stats_by_stratum = {}
            stats_by_ethos = {}
            stats_by_planet = {}

            for pop_group_id, pop_group_dict in pop_groups_dict.items():
                if not isinstance(pop_group_dict, dict):
                    continue
                planet_id = _extract_id(pop_group_dict.get("planet"))
                planet_country_id_in_game = self.country_by_planet_id.get(planet_id)
                if planet_country_id_in_game != country_id_in_game:
                    continue

                size = pop_group_dict.get("size")
                if size == 0:
                    continue

                species_id = _extract_id(pop_group_dict.get("key").get("species"))
                stratum = pop_group_dict.get("key").get("category", "unknown stratum")

                # crime and power are already totals, but happiness is average, so multiply by size
                crime = pop_group_dict.get("crime", 0.0)
                happiness = pop_group_dict.get("happiness", 0.0) * size
                power = pop_group_dict.get("power", 0.0)
                group_stats = (size, crime, happiness, power)

                self._add_pop_stats(stats_by_species, species_id, size, *group_stats)
                self._add_pop_stats(stats_by_stratum, stratum, size, *group_stats)
                self._add_pop_stats(stats_by_planet, planet_id, size, *group_stats)
                # since 4.5, the pops in one group can hold different ethics and belong to different factions
                for ethos, pop_count in save_format.get_pop_group_ethics(pop_group_dict):
                    self._add_pop_stats(stats_by_ethos, ethos, pop_count, *group_stats)
                for faction_id, pop_count in save_format.get_pop_group_factions(pop_group_dict):
                    if faction_id is None:
                        faction_id = self._get_unaffiliated_faction_id(stratum, species_id, robot_species)
                    self._add_pop_stats(stats_by_faction, faction_id, pop_count, *group_stats)

                # each pop_group can have multiple jobs; collect stats based on fraction assigned to each job
                unemployed_fraction = 1 # civilians are tracked as a job, so I don't think there will ever be unemployed pops, but let's be safe
                for pop_job in pop_group_to_jobs.get(pop_group_id, []):
                    job = pop_job["job"]
                    if job not in stats_by_job:
                        stats_by_job[job] = init_dict()
                    fraction = pop_job["amount"] / size
                    unemployed_fraction -= fraction
                    stats_by_job[job]["pop_count"] += pop_job["amount"]
                    stats_by_job[job]["crime"] += crime * fraction
                    stats_by_job[job]["happiness"] += happiness * fraction
                    stats_by_job[job]["power"] += power * fraction
                unemployed_amount = int(size * unemployed_fraction) # prevent floating point math issues
                if unemployed_amount >= 1:
                    job = "unemployed"
                    if job not in stats_by_job:
                        stats_by_job[job] = init_dict()
                    stats_by_job[job]["pop_count"] += unemployed_amount
                    stats_by_job[job]["crime"] += crime * unemployed_fraction
                    stats_by_job[job]["happiness"] += happiness * unemployed_fraction
                    stats_by_job[job]["power"] += power * unemployed_fraction

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
                # recent versions (at least 3.12+) store the support fraction as "support_percent"
                stats["support"] = faction_dict.get("support_percent", faction_dict.get("support", 0.0))

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

                # since 4.4, pop groups reference their colony rather than the planet
                planet_id = save_format.resolve_colony_to_planet_id(self._gamestate_dict, planet_id)
                planet_dict = save_format.get_planet(self._gamestate_dict, planet_id)
                if not isinstance(planet_dict, dict):
                    continue

                stats["free_amenities"] = planet_dict.get("free_amenities", 0.0)
                stats["free_housing"] = planet_dict.get("free_housing", 0.0)
                stats["stability"] = planet_dict.get("stability", 0.0)

                planet = planets_by_ingame_id.get(planet_id)
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

    @staticmethod
    def _add_pop_stats(stats_dict, key, pop_count, size, crime, happiness, power):
        """Add pop_count pops of a pop group to stats_dict[key], with a proportional share of the group's stats."""
        if key not in stats_dict:
            stats_dict[key] = dict(pop_count=0, crime=0, happiness=0, power=0)
        fraction = pop_count / size
        stats_dict[key]["pop_count"] += pop_count
        stats_dict[key]["crime"] += crime * fraction
        stats_dict[key]["happiness"] += happiness * fraction
        stats_dict[key]["power"] += power * fraction

    @staticmethod
    def _get_unaffiliated_faction_id(stratum, species_id, robot_species) -> int:
        if stratum == "slave":
            return FactionProcessor.SLAVE_FACTION_ID
        elif species_id in robot_species:
            return FactionProcessor.NON_SENTIENT_ROBOT_FACTION_ID
        elif stratum == "purge":
            return FactionProcessor.PURGE_FACTION_ID
        return FactionProcessor.NO_FACTION_ID

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
