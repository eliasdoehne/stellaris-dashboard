import contextlib
import enum
import itertools
import json
import logging
import pathlib
import threading
from typing import Dict, List, Union, Optional, Iterable, Collection

import sqlalchemy
from sqlalchemy import Column, Integer, String, ForeignKey, Float, Boolean, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, scoped_session

from stellarisdashboard import config, game_info

logger = logging.getLogger(__name__)


Base = declarative_base()
_ENGINES = {}
_SESSIONMAKERS = {}
_DB_LOCKS = {}


@contextlib.contextmanager
def get_db_session(game_id) -> sqlalchemy.orm.Session:
    if game_id not in _SESSIONMAKERS:
        db_file = config.CONFIG.db_path / f"{game_id}.db"
        if not db_file.exists():
            logger.info(f"Creating database for game {game_id} in file {db_file}.")
        engine = sqlalchemy.create_engine(f"sqlite:///{db_file}", echo=False)
        Base.metadata.create_all(bind=engine)
        _ENGINES[game_id] = engine
        _SESSIONMAKERS[game_id] = scoped_session(sessionmaker(bind=engine))
        _DB_LOCKS[game_id] = threading.Lock()

    with _DB_LOCKS[game_id]:
        session_factory = _SESSIONMAKERS[game_id]
        s = session_factory()
        try:
            yield s
        finally:
            s.close()


### Some enum types representing various in-game concepts
@enum.unique
class Attitude(enum.Enum):
    is_player = enum.auto()

    unknown = enum.auto()
    neutral = enum.auto()
    wary = enum.auto()
    receptive = enum.auto()
    cordial = enum.auto()
    friendly = enum.auto()
    protective = enum.auto()
    unfriendly = enum.auto()
    rival = enum.auto()
    hostile = enum.auto()
    domineering = enum.auto()
    threatened = enum.auto()
    overlord = enum.auto()
    loyal = enum.auto()
    disloyal = enum.auto()
    dismissive = enum.auto()
    patronizing = enum.auto()
    angry = enum.auto()
    arrogant = enum.auto()
    imperious = enum.auto()
    belligerent = enum.auto()
    custodial = enum.auto()
    enigmatic = enum.auto()
    berserk = enum.auto()

    other = enum.auto()

    def reveals_military_info(self) -> bool:
        return self in {
            Attitude.friendly,
            Attitude.loyal,
            Attitude.disloyal,
            Attitude.overlord,
            Attitude.is_player,
        }

    def reveals_technology_info(self) -> bool:
        return self.reveals_military_info() or (
            self in {Attitude.protective, Attitude.disloyal}
        )

    def reveals_economy_info(self) -> bool:
        return self.reveals_technology_info() or (
            self in {Attitude.cordial, Attitude.receptive}
        )

    def reveals_demographic_info(self) -> bool:
        return self.reveals_economy_info() or (
            self in {Attitude.neutral, Attitude.wary}
        )

    def is_known(self) -> bool:
        return self != Attitude.unknown

    def __str__(self):
        return self.name.capitalize()


@enum.unique
class CombatType(enum.Enum):
    ships = enum.auto()
    armies = enum.auto()

    other = enum.auto()

    def __str__(self):
        return self.name


@enum.unique
class WarOutcome(enum.Enum):
    in_progress = enum.auto()
    truce = enum.auto()
    resolution_unknown = enum.auto()

    def __str__(self):
        return game_info.convert_id_to_name(self.name)


@enum.unique
class HistoricalEventScope(enum.IntEnum):
    galaxy = 100
    country = 80
    leader = 30
    system = 20
    all = 0


class HistoricalEventType(enum.Enum):
    # tied to a specific leader:
    ruled_empire = enum.auto()
    governed_sector = enum.auto()
    councilor = enum.auto()
    faction_leader = enum.auto()
    leader_recruited = enum.auto()
    leader_died = enum.auto()  # TODO
    level_up = enum.auto()
    fleet_command = enum.auto()

    # empire progress:
    researched_technology = enum.auto()
    tradition = enum.auto()
    ascension_perk = enum.auto()
    edict = enum.auto()
    expanded_to_system = enum.auto()

    # Planets and sectors:
    colonization = enum.auto()
    discovered_new_system = enum.auto()
    habitat_ringworld_construction = enum.auto()
    megastructure_construction = enum.auto()
    sector_creation = enum.auto()
    capital_relocation = enum.auto()
    planetary_unrest = enum.auto()  # TODO
    terraforming = enum.auto()
    planet_destroyed = enum.auto()  # TODO

    # related to internal politics:
    new_faction = enum.auto()
    government_reform = enum.auto()
    species_rights_reform = enum.auto()  # TODO

    # Galactic community and council
    joined_galactic_community = enum.auto()
    joined_galactic_council = enum.auto()
    left_galactic_community = enum.auto()
    left_galactic_council = enum.auto()
    voted_for_resolution = enum.auto()  # TODO
    voted_against_resolution = enum.auto()  # TODO

    # diplomacy:
    first_contact = enum.auto()
    non_aggression_pact = enum.auto()
    defensive_pact = enum.auto()
    formed_federation = enum.auto()
    commercial_pact = enum.auto()
    research_agreement = enum.auto()
    migration_treaty = enum.auto()
    embassy = enum.auto()

    closed_borders = enum.auto()
    received_closed_borders = enum.auto()
    sent_rivalry = enum.auto()
    received_rivalry = enum.auto()

    # envoys
    envoy_community = enum.auto()
    envoy_federation = enum.auto()
    envoy_improving_relations = enum.auto()
    envoy_harming_relations = enum.auto()

    # war
    war = enum.auto()
    peace = enum.auto()
    fleet_combat = enum.auto()
    army_combat = enum.auto()
    conquered_system = enum.auto()
    lost_system = enum.auto()

    def __str__(self):
        return self.name

    @property
    def scope(self):
        if self in {
            HistoricalEventType.ruled_empire,
            HistoricalEventType.habitat_ringworld_construction,
            HistoricalEventType.megastructure_construction,
            HistoricalEventType.new_faction,
            HistoricalEventType.government_reform,
            HistoricalEventType.species_rights_reform,
            HistoricalEventType.first_contact,
            HistoricalEventType.non_aggression_pact,
            HistoricalEventType.defensive_pact,
            HistoricalEventType.formed_federation,
            HistoricalEventType.migration_treaty,
            HistoricalEventType.research_agreement,
            HistoricalEventType.commercial_pact,
            HistoricalEventType.closed_borders,
            HistoricalEventType.received_closed_borders,
            HistoricalEventType.sent_rivalry,
            HistoricalEventType.received_rivalry,
            HistoricalEventType.joined_galactic_community,
            HistoricalEventType.joined_galactic_council,
            HistoricalEventType.left_galactic_community,
            HistoricalEventType.left_galactic_council,
            HistoricalEventType.war,
            HistoricalEventType.peace,
            HistoricalEventType.terraforming,
            HistoricalEventType.planet_destroyed,
            HistoricalEventType.envoy_community,
            HistoricalEventType.envoy_federation,
            HistoricalEventType.envoy_improving_relations,
            HistoricalEventType.envoy_harming_relations,
            HistoricalEventType.embassy,
        }:
            return HistoricalEventScope.galaxy
        elif self in {
            HistoricalEventType.discovered_new_system,
            HistoricalEventType.colonization,
            HistoricalEventType.fleet_command,
            HistoricalEventType.expanded_to_system,
            HistoricalEventType.capital_relocation,
            HistoricalEventType.sector_creation,
            HistoricalEventType.planetary_unrest,
            HistoricalEventType.governed_sector,
            HistoricalEventType.councilor,
            HistoricalEventType.faction_leader,
            HistoricalEventType.leader_recruited,
            HistoricalEventType.leader_died,
            HistoricalEventType.researched_technology,
            HistoricalEventType.tradition,
            HistoricalEventType.ascension_perk,
            HistoricalEventType.edict,
            HistoricalEventType.conquered_system,
            HistoricalEventType.lost_system,
        }:
            return HistoricalEventScope.country
        elif self in {
            HistoricalEventType.level_up,
        }:
            return HistoricalEventScope.leader
        elif self in {
            HistoricalEventType.fleet_combat,
            HistoricalEventType.army_combat,
        }:
            return HistoricalEventScope.system
        return HistoricalEventScope.all


### Some convenience functions
def date_to_days(date_str: str) -> int:
    """Converts a date given in-game ("2200.03.01") to an integer counting the days passed since
    2200.01.01.

    :param date_str: Date in YYYY.MM.DD format
    :return: Days passed since 2200.01.01
    """
    y, m, d = map(int, date_str.split("."))
    return (y - 2200) * 360 + (m - 1) * 30 + d - 1


def days_to_date(days: float) -> str:
    """Converts an integer counting the days passed in-game since 2200.01.01 to a readable date in YYYY.MM.DD format
    (In Stellaris, there are 12 months with 30 days each)

    :param date_str: Date in YYYY.MM.DD format
    :return: Days passed since 2200.01.01
    """
    days = int(days)
    year_offset = days // 360
    year = 2200 + year_offset
    days -= 360 * year_offset
    month_offset = days // 30
    month = 1 + month_offset
    day = days - 30 * month_offset + 1
    return f"{year:04}.{month:02}.{day:02}"


### Some helper functions to conveniently access the databases.
def get_last_modified_time(path: pathlib.Path) -> int:
    return path.stat().st_mtime


def get_known_games(game_name_prefix: str = "") -> List[str]:
    files = sorted(
        config.CONFIG.db_path.glob(f"{game_name_prefix}*.db"),
        key=get_last_modified_time,
        reverse=True,
    )
    return [fname.stem for fname in files]


def get_available_games_dict() -> Dict[str, Dict[str, str]]:
    """Returns a dictionary mapping game id to some basic info about the game."""
    games = {}
    for game_id in get_known_games():
        with get_db_session(game_id) as session:
            game = session.query(Game).one_or_none()
            if game is None:
                continue
            most_recent_gamestate = (
                session.query(GameState).order_by(GameState.date.desc()).first()
            )
            games[game_id] = dict(
                game_id=game_id,
                game_date=days_to_date(most_recent_gamestate.date),
                num_saves=session.query(GameState.gamestate_id).count(),
                country_name=game_info.render_name(game.player_country_name),
                difficulty=game.difficulty,
                galaxy=game.galaxy,
                last_updated=game.last_updated,
            )
    return games


def count_gamestates_since(game_name: str, date: float) -> int:
    with get_db_session(game_name) as session:
        return session.query(GameState).filter(GameState.date > date).count()


def get_gamestates_since(game_name: str, date: float):
    with get_db_session(game_name) as session:
        game = session.query(Game).one()
        for gs in (
            session.query(GameState)
            .filter(GameState.game == game, GameState.date > date)
            .order_by(GameState.date)
            .all()
        ):
            yield gs


class Game(Base):
    """Root object representing an entire game."""

    __tablename__ = "gametable"
    game_id = Column(Integer, primary_key=True)
    game_name = Column(String(50))

    player_country_name = Column(String(100))
    db_galaxy_template = Column(String(100))
    db_galaxy_shape = Column(String(100))
    db_difficulty = Column(String(100))

    db_last_updated = Column(sqlalchemy.DateTime, default=None)

    systems = relationship(
        "System", back_populates="game", cascade="all,delete,delete-orphan"
    )
    countries = relationship(
        "Country", back_populates="game", cascade="all,delete,delete-orphan"
    )
    species = relationship(
        "Species", back_populates="game", cascade="all,delete,delete-orphan"
    )
    game_states = relationship(
        "GameState", back_populates="game", cascade="all,delete,delete-orphan"
    )
    wars = relationship(
        "War", back_populates="game", cascade="all,delete,delete-orphan"
    )
    leaders = relationship(
        "Leader", back_populates="game", cascade="all,delete,delete-orphan"
    )

    @property
    def galaxy(self):
        return f"{self.galaxy_template} {self.galaxy_shape}"

    @property
    def galaxy_template(self):
        return game_info.convert_id_to_name(self.db_galaxy_template)

    @property
    def galaxy_shape(self):
        return game_info.convert_id_to_name(self.db_galaxy_shape)

    @property
    def difficulty(self):
        return game_info.convert_id_to_name(self.db_difficulty)

    @property
    def last_updated(self):
        return f"{self.db_last_updated:%Y.%m.%d %H:%M}"


class SharedDescription(Base):
    """Represents short descriptions like technology names that are likely to occur many times for various empires."""

    __tablename__ = "shareddescriptiontable"
    description_id = Column(Integer, primary_key=True)

    text = Column(String(500), index=True)


class System(Base):
    """Represents a single star system/galactic_object."""

    __tablename__ = "systemtable"
    system_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    country_id = Column(ForeignKey("countrytable.country_id"), index=True)

    name = Column(String(80))
    system_id_in_game = Column(Integer, index=True)
    star_class = Column(String(20))

    coordinate_x = Column(Float)
    coordinate_y = Column(Float)

    game = relationship("Game", back_populates="systems")
    country = relationship("Country", back_populates="systems")
    ownership_history = relationship(
        "SystemOwnership", back_populates="system", cascade="all,delete,delete-orphan"
    )
    planets = relationship(
        "Planet", back_populates="system", cascade="all,delete,delete-orphan"
    )

    # this could probably be done better....
    hyperlanes_one = relationship(
        "HyperLane", foreign_keys=lambda: [HyperLane.system_one_id]
    )
    hyperlanes_two = relationship(
        "HyperLane", foreign_keys=lambda: [HyperLane.system_two_id]
    )

    bypasses = relationship("Bypass", cascade="all,delete,delete-orphan")

    historical_events = relationship(
        "HistoricalEvent", back_populates="system", cascade="all,delete,delete-orphan"
    )

    @property
    def neighbors(self):
        for hl in self.hyperlanes_one:
            yield hl.system_two
        for hl in self.hyperlanes_two:
            yield hl.system_one

    def get_owner_country_at(self, time_in_days: int) -> Optional["Country"]:
        """
        Get the (database) country ID of the empire that owned this system.

        :param time_in_days:
        :return: Database country ID
        """
        if not self.ownership_history and self.country is None:
            return None
        # could be slow, but fine for now
        for ownership in sorted(
            self.ownership_history, key=lambda oh: oh.start_date_days
        ):
            start = ownership.start_date_days or float("-inf")
            end = ownership.end_date_days or float("inf")
            if start <= time_in_days <= end:
                return ownership.country
            elif ownership.start_date_days > time_in_days:
                return None
        return self.country

    @property
    def rendered_name(self):
        rendered = game_info.render_name(self.name)
        return rendered

    def __str__(self):
        return f'System "{self.name}" @ {self.coordinate_x}, {self.coordinate_y}'


class SystemOwnership(Base):
    """Represent the timespan during which some empire owned a given system."""

    __tablename__ = "systemownershiptable"
    system_ownership_id = Column(Integer, primary_key=True)

    system_id = Column(ForeignKey(System.system_id), index=True)
    owner_country_id = Column(ForeignKey("countrytable.country_id"))

    start_date_days = Column(Integer, index=True)
    end_date_days = Column(Integer, index=True)

    system = relationship("System", back_populates="ownership_history")
    country = relationship("Country")

    def __str__(self):
        return f"SystemOwnership of {self.system_name}: {self.start_date_days} - {self.end_date_days} -- {self.country_name}"

    @property
    def country_name(self):
        return self.country.country_name

    @property
    def system_name(self):
        return self.system.name


class HyperLane(Base):
    """
    Represent hyperlane connections between systems. While the HyperLane is
    represented as a directed edge, it should be interpreted as undirected.
    """

    __tablename__ = "hyperlanetable"
    hyperlane_id = Column(Integer, primary_key=True)

    system_one_id = Column(ForeignKey(System.system_id))
    system_two_id = Column(ForeignKey(System.system_id))

    system_one = relationship(
        "System",
        back_populates="hyperlanes_one",
        foreign_keys=[system_one_id],
    )
    system_two = relationship(
        "System",
        back_populates="hyperlanes_two",
        foreign_keys=[system_two_id],
    )


class Bypass(Base):
    """
    Represent bypasses.
    """

    __tablename__ = "bypasstable"
    bypass_id = Column(Integer, primary_key=True)

    system_id = Column(ForeignKey(System.system_id), nullable=False, index=True)
    bypass_type_description_id = Column(
        ForeignKey(SharedDescription.description_id), nullable=False
    )

    network_id = Column(Integer, nullable=False, index=True)
    is_active = Column(Boolean, nullable=False)

    system = relationship(System, back_populates="bypasses", foreign_keys=[system_id])
    db_description = relationship(SharedDescription)

    @property
    def name(self):
        bypass_type = game_info.convert_id_to_name(self.db_description.text)
        status_str = "" if self.is_active else " (inactive)"
        return f"{self.system.name} {bypass_type}{status_str}"


class GameState(Base):
    """Represents the state of the game at a specific moment."""

    __tablename__ = "gamestatetable"
    gamestate_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    date = Column(Integer, index=True, nullable=False)  # Days since 2200.1.1

    game = relationship("Game", back_populates="game_states")
    country_data = relationship(
        "CountryData", back_populates="game_state", cascade="all,delete,delete-orphan"
    )
    galactic_market_resources = relationship(
        "GalacticMarketResource",
        back_populates="game_state",
        cascade="all,delete,delete-orphan",
    )

    def __str__(self):
        return f"Gamestate of {self.game.game_name} @ {days_to_date(self.date)}"


class Country(Base):
    __tablename__ = "countrytable"
    country_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    capital_planet_id = Column(ForeignKey("planettable.planet_id"))

    ruler_id = Column(ForeignKey("leadertable.leader_id"))
    scientist_physics_id = Column(ForeignKey("leadertable.leader_id"))
    scientist_society_id = Column(ForeignKey("leadertable.leader_id"))
    scientist_engineering_id = Column(ForeignKey("leadertable.leader_id"))

    is_player = Column(Boolean)
    is_other_player = Column(Boolean)
    country_name = Column(String(80))
    country_id_in_game = Column(Integer)
    first_player_contact_date = Column(Integer)
    country_type = Column(String(50))
    primary_color = Column(String(80))
    secondary_color = Column(String(80))

    game = relationship("Game", back_populates="countries")
    capital = relationship("Planet", foreign_keys=[capital_planet_id], post_update=True)

    ruler = relationship("Leader", foreign_keys=[ruler_id])
    scientist_physics = relationship("Leader", foreign_keys=[scientist_physics_id])
    scientist_society = relationship("Leader", foreign_keys=[scientist_society_id])
    scientist_engineering = relationship(
        "Leader", foreign_keys=[scientist_engineering_id]
    )

    governments = relationship(
        "Government", back_populates="country", cascade="all,delete,delete-orphan"
    )
    country_data = relationship(
        "CountryData",
        back_populates="country",
        cascade="all,delete,delete-orphan",
        order_by="CountryData.date",
    )
    political_factions = relationship(
        "PoliticalFaction", back_populates="country", cascade="all,delete,delete-orphan"
    )
    war_participation = relationship(
        "WarParticipant",
        back_populates="country",
        cascade="all,delete,delete-orphan",
        foreign_keys=lambda: [WarParticipant.country_id],
    )
    systems = relationship(System, back_populates="country")
    leaders = relationship(
        "Leader",
        back_populates="country",
        cascade="all,delete,delete-orphan",
        foreign_keys=lambda: [Leader.country_id],
    )

    traditions = relationship("Tradition", cascade="all,delete,delete-orphan")
    ascension_perks = relationship("AscensionPerk", cascade="all,delete,delete-orphan")
    technologies = relationship("Technology", cascade="all,delete,delete-orphan")

    historical_events = relationship(
        "HistoricalEvent",
        back_populates="country",
        foreign_keys=lambda: [HistoricalEvent.country_id],
        cascade="all,delete,delete-orphan",
    )

    outgoing_relations = relationship(
        "DiplomaticRelation", foreign_keys=lambda: [DiplomaticRelation.country_id]
    )
    incoming_relations = relationship(
        "DiplomaticRelation",
        foreign_keys=lambda: [DiplomaticRelation.target_country_id],
    )

    @property
    def rendered_name(self):
        rendered = game_info.render_name(self.country_name)
        if config.CONFIG.include_id_in_names:
            rendered += f" ({self.country_id_in_game})"
        return rendered

    def get_government_for_date(self, date_in_days) -> "Government":
        # could be slow, but fine for now
        for gov in self.governments:
            if gov.start_date_days <= date_in_days <= gov.end_date_days:
                return gov

    def get_current_government(self) -> Union["Government", None]:
        if not self.governments:
            return None
        return max(self.governments, key=lambda gov: gov.end_date_days)

    def get_most_recent_data(self) -> Union["CountryData", None]:
        if not self.country_data:
            return None
        return self.country_data[-1]

    def has_met_player(self) -> bool:
        return self.is_player or self.first_player_contact_date is not None

    def is_real_country(self):
        return (
            self.country_type == "default"
            or self.country_type == "fallen_empire"
            or self.country_type == "awakened_fallen_empire"
        )

    def diplo_relation_details(self):
        countries_by_relation = {}
        for relation in self.outgoing_relations:
            for key in relation.active_relations():
                if key not in countries_by_relation:
                    countries_by_relation[key] = []
                countries_by_relation[key].append(relation.target)
        return countries_by_relation


class Tradition(Base):
    __tablename__ = "traditionstable"
    tradition_id = Column(Integer, primary_key=True)
    country_id = Column(ForeignKey(Country.country_id), index=True)
    tradition_name_id = Column(ForeignKey(SharedDescription.description_id))

    db_description = relationship("SharedDescription")
    country = relationship("Country", back_populates="traditions")

    @property
    def name(self):
        return game_info.convert_id_to_name(self.db_description.text)


class AscensionPerk(Base):
    __tablename__ = "ascensionperkstable"
    tradition_id = Column(Integer, primary_key=True)
    country_id = Column(ForeignKey(Country.country_id), index=True)
    perk_name_id = Column(ForeignKey(SharedDescription.description_id))

    db_description = relationship("SharedDescription")
    country = relationship("Country", back_populates="ascension_perks")

    @property
    def name(self):
        return game_info.convert_id_to_name(self.db_description.text)


class Technology(Base):
    __tablename__ = "technologytable"
    technology_id = Column(Integer, primary_key=True)

    country_id = Column(ForeignKey(Country.country_id), index=True)
    technology_name_id = Column(ForeignKey(SharedDescription.description_id))
    is_completed = Column(Boolean, index=True, default=False)

    db_description = relationship("SharedDescription")
    country = relationship("Country", back_populates="technologies")

    @property
    def name(self):
        return game_info.convert_id_to_name(self.db_description.text)


class Government(Base):
    """
    Representation of a country's government, as specified by the country name,
    the government type (flavor text, e.g. "executive committee"), the governing
    authority (e.g. "Democracy"), the date range, as well as the governing ethics
    and civics.
    """

    __tablename__ = "govtable"
    gov_id = Column(Integer, primary_key=True)

    country_id = Column(ForeignKey(Country.country_id), index=True)

    start_date_days = Column(Integer, index=True)
    end_date_days = Column(Integer)

    gov_name = Column(String(100))
    gov_type = Column(String(80))
    personality = Column(String(80))
    authority = Column(String(80))

    ethics_1 = Column(String(80))
    ethics_2 = Column(String(80))
    ethics_3 = Column(String(80))
    ethics_4 = Column(String(80))
    ethics_5 = Column(String(80))

    civic_1 = Column(String(80))
    civic_2 = Column(String(80))
    civic_3 = Column(String(80))
    civic_4 = Column(String(80))
    civic_5 = Column(String(80))

    country = relationship("Country", back_populates="governments")

    @property
    def civics(self):
        civics = {self.civic_1, self.civic_2, self.civic_3, self.civic_4, self.civic_5}
        return civics - {None}

    @property
    def ethics(self):
        civics = {
            self.ethics_1,
            self.ethics_2,
            self.ethics_3,
            self.ethics_4,
            self.ethics_5,
        }
        return civics - {None}

    def get_reform_description_dict(self, old_gov: "Government") -> Dict[str, str]:
        reform_dict = {}

        if old_gov.gov_type != self.gov_type:
            ogt = game_info.convert_id_to_name(old_gov.gov_type, remove_prefix="gov")
            ngt = game_info.convert_id_to_name(self.gov_type, remove_prefix="gov")
            reform_dict["Type changed"] = [f'from "{ogt}" to "{ngt}"']

        if old_gov.authority != self.authority:
            old_authority = game_info.convert_id_to_name(
                old_gov.authority, remove_prefix="auth"
            )
            new_authority = game_info.convert_id_to_name(
                self.authority, remove_prefix="auth"
            )
            reform_dict["Authority changed"] = [
                f"from {old_authority} to {new_authority}"
            ]

        new_civics = self.civics - old_gov.civics
        removed_civics = old_gov.civics - self.civics
        reform_dict["Removed civics"] = sorted(
            game_info.convert_id_to_name(c, remove_prefix="civic")
            for c in removed_civics
        )
        reform_dict["Adopted civics"] = sorted(
            game_info.convert_id_to_name(c, remove_prefix="civic") for c in new_civics
        )

        new_ethics = self.ethics - old_gov.ethics
        removed_ethics = old_gov.ethics - self.ethics
        reform_dict["Abandoned ethics"] = sorted(
            game_info.convert_id_to_name(e, remove_prefix="ethic")
            for e in removed_ethics
        )
        reform_dict["Embraced ethics"] = sorted(
            game_info.convert_id_to_name(e, remove_prefix="ethic") for e in new_ethics
        )
        return {k: v for k, v in reform_dict.items() if v}

    def __str__(self):
        return f"{self.authority} {self.gov_type} {self.civics} {self.ethics}"


class DiplomaticRelation(Base):
    __tablename__ = "diplo_relation_table"

    diplo_relation_id = Column(Integer, primary_key=True)

    country_id = Column(ForeignKey(Country.country_id), nullable=False, index=True)
    target_country_id = Column(
        ForeignKey(Country.country_id), nullable=False, index=True
    )

    rivalry = Column(Boolean, default=False)
    defensive_pact = Column(Boolean, default=False)
    federation = Column(Boolean, default=False)
    non_aggression_pact = Column(Boolean, default=False)
    closed_borders = Column(Boolean, default=False)
    communations = Column(Boolean, default=False)
    migration_treaty = Column(Boolean, default=False)
    commercial_pact = Column(Boolean, default=False)
    neighbor = Column(Boolean, default=False)
    research_agreement = Column(Boolean, default=False)
    embassy = Column(Boolean, default=False)

    owner = relationship(
        Country,
        back_populates="outgoing_relations",
        foreign_keys=[country_id],
    )
    target = relationship(
        Country,
        back_populates="incoming_relations",
        foreign_keys=[target_country_id],
    )

    _dict_key_attr_mapping = dict(
        rivalries="rivalry",
        defensive_pacts="defensive_pact",
        federations="federation",
        non_aggression_pacts="non_aggression_pact",
        closed_borders="closed_borders",
        communations="communations",
        migration_treaties="migration_treaty",
        commercial_pacts="commercial_pact",
        neighbors="neighbor",
        research_agreements="research_agreement",
        embassies="embassy",
    )

    def is_active(self, key: str) -> bool:
        if key in self._dict_key_attr_mapping:
            return getattr(self, self._dict_key_attr_mapping[key])
        else:
            logger.warning(f"Queried unknown diplo relation key {key}")
            return False

    def toggle(self, key: str):
        if key in self._dict_key_attr_mapping:
            current_state = self.is_active(key)
            setattr(self, self._dict_key_attr_mapping[key], not current_state)
        else:
            logger.warning(f"Attempted toggling unknown diplo relation key {key}")

    def active_relations(self) -> Iterable[str]:
        for key in self._dict_key_attr_mapping:
            if self.is_active(key):
                yield key


class CountryData(Base):
    """Representation of the state of a single country at a specific time."""

    __tablename__ = "countrydatatable"
    country_data_id = Column(Integer, primary_key=True)
    country_id = Column(ForeignKey(Country.country_id), index=True)
    game_state_id = Column(ForeignKey(GameState.gamestate_id), index=True)

    date = Column(Integer, index=True, nullable=False)

    victory_rank = Column(Integer)
    victory_score = Column(Float)
    economy_power = Column(Float)
    tech_power = Column(Float)

    military_power = Column(Float)
    fleet_size = Column(Float)
    empire_size = Column(Float)
    empire_cohesion = Column(Float)

    tech_count = Column(Integer)
    exploration_progress = Column(Integer)
    owned_planets = Column(Integer)
    controlled_systems = Column(Integer)

    net_energy = Column(Float, nullable=False, default=0.0)
    net_minerals = Column(Float, nullable=False, default=0.0)
    net_alloys = Column(Float, nullable=False, default=0.0)
    net_consumer_goods = Column(Float, nullable=False, default=0.0)
    net_food = Column(Float, nullable=False, default=0.0)
    net_unity = Column(Float, nullable=False, default=0.0)
    net_influence = Column(Float, nullable=False, default=0.0)
    net_physics_research = Column(Float, nullable=False, default=0.0)
    net_society_research = Column(Float, nullable=False, default=0.0)
    net_engineering_research = Column(Float, nullable=False, default=0.0)

    ship_count_corvette = Column(Integer, default=0)
    ship_count_destroyer = Column(Integer, default=0)
    ship_count_cruiser = Column(Integer, default=0)
    ship_count_battleship = Column(Integer, default=0)
    ship_count_titan = Column(Integer, default=0)
    ship_count_colossus = Column(Integer, default=0)

    # Diplomacy towards player
    attitude_towards_player = Column(Enum(Attitude))
    has_embassy_with_player = Column(Boolean)
    has_research_agreement_with_player = Column(Boolean)
    has_sensor_link_with_player = Column(Boolean)
    has_rivalry_with_player = Column(Boolean)
    has_defensive_pact_with_player = Column(Boolean)
    has_migration_treaty_with_player = Column(Boolean)
    has_federation_with_player = Column(Boolean)
    has_non_aggression_pact_with_player = Column(Boolean)
    has_closed_borders_with_player = Column(Boolean)
    has_communications_with_player = Column(Boolean)
    has_commercial_pact_with_player = Column(Boolean)
    is_player_neighbor = Column(Boolean)

    has_galactic_market_access = Column(Boolean, default=False)

    country = relationship("Country", back_populates="country_data")
    game_state = relationship("GameState", back_populates="country_data")

    budget = relationship("BudgetItem", cascade="all,delete,delete-orphan")

    internal_market_resources = relationship(
        "InternalMarketResource",
        back_populates="country_data",
        cascade="all,delete,delete-orphan",
    )

    pop_stats_species = relationship(
        "PopStatsBySpecies",
        back_populates="country_data",
        cascade="all,delete,delete-orphan",
    )
    pop_stats_faction = relationship(
        "PopStatsByFaction",
        back_populates="country_data",
        cascade="all,delete,delete-orphan",
    )
    pop_stats_job = relationship(
        "PopStatsByJob",
        back_populates="country_data",
        cascade="all,delete,delete-orphan",
    )
    pop_stats_stratum = relationship(
        "PopStatsByStratum",
        back_populates="country_data",
        cascade="all,delete,delete-orphan",
    )
    pop_stats_ethos = relationship(
        "PopStatsByEthos",
        back_populates="country_data",
        cascade="all,delete,delete-orphan",
    )
    pop_stats_planets = relationship(
        "PlanetStats", back_populates="country_data", cascade="all,delete,delete-orphan"
    )

    def show_geography_info(self):
        return self.country.is_player or self.attitude_towards_player.is_known()

    def show_tech_info(self):
        return (
            self.country.is_player
            or self.has_research_agreement_with_player
            or self.attitude_towards_player.reveals_technology_info()
        )

    def show_economic_info(self):
        return (
            self.country.is_player
            or self.has_sensor_link_with_player
            or self.attitude_towards_player.reveals_economy_info()
        )

    def show_demographic_info(self):
        return (
            self.country.is_player
            or self.attitude_towards_player.reveals_demographic_info()
            or self.has_sensor_link_with_player
            or self.has_migration_treaty_with_player
        )

    def show_military_info(self):
        return (
            self.country.is_player
            or self.has_sensor_link_with_player
            or self.attitude_towards_player.reveals_military_info()
            or self.has_defensive_pact_with_player
            or self.has_federation_with_player
        )


class GalacticMarketResource(Base):
    """
    Market data for a single resource at a specific time.
    The name of the resource and its
    """

    __tablename__ = "galacticmarketresourcetable"
    galactic_market_resource_id = Column(Integer, primary_key=True)
    game_state_id = Column(ForeignKey(GameState.gamestate_id), index=True)
    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)

    # position encodes the resource, matching the order in common/strategic_resources/00_strategic_resources.txt
    resource_index = Column(Integer)
    availability = Column(Integer)  # 0 or 1

    fluctuation = Column(Float)

    # Buy volume: Sum over all countries
    resources_bought = Column(Float)
    resources_sold = Column(Float)

    game_state = relationship(
        "GameState",
        back_populates="galactic_market_resources",
    )


class InternalMarketResource(Base):
    __tablename__ = "internalmarketresourcetable"
    internal_market_resource_id = Column(Integer, primary_key=True)
    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)

    # internal market resources are stored by name
    resource_name_id = Column(ForeignKey(SharedDescription.description_id), index=True)

    fluctuation = Column(Float)

    country_data = relationship(
        "CountryData", back_populates="internal_market_resources"
    )
    resource_name = relationship("SharedDescription")


class BudgetItem(Base):
    __tablename__ = "budgetitemtable"
    budget_item_id = Column(Integer, primary_key=True)

    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)
    budget_item_description_id = Column(
        ForeignKey(SharedDescription.description_id)
    )  # e.g. "trade_routes", "ships", etc

    net_energy = Column(Float, default=0.0)
    net_minerals = Column(Float, default=0.0)
    net_food = Column(Float, default=0.0)

    net_alloys = Column(Float, default=0.0)
    net_consumer_goods = Column(Float, default=0.0)

    net_volatile_motes = Column(Float, default=0.0)
    net_exotic_gases = Column(Float, default=0.0)
    net_rare_crystals = Column(Float, default=0.0)
    net_living_metal = Column(Float, default=0.0)
    net_zro = Column(Float, default=0.0)
    net_dark_matter = Column(Float, default=0.0)
    net_nanites = Column(Float, default=0.0)

    net_physics_research = Column(Float, default=0.0)
    net_society_research = Column(Float, default=0.0)
    net_engineering_research = Column(Float, default=0.0)

    net_unity = Column(Float, default=0.0)
    net_influence = Column(Float, default=0.0)

    country_data = relationship("CountryData", back_populates="budget")
    db_budget_item_name = relationship("SharedDescription")

    @property
    def name(self) -> str:
        return self.db_budget_item_name.text


class Species(Base):
    """Represents a species in a game. Not tied to any specific time."""

    __tablename__ = "speciestable"
    species_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    species_id_in_game = Column(Integer)
    home_planet_id = Column(ForeignKey("planettable.planet_id"))

    species_name = Column(String(80))
    species_class = Column(String(20))
    parent_species_id_in_game = Column(Integer)

    game = relationship("Game", back_populates="species")
    db_traits = relationship(
        "SpeciesTrait", back_populates="species", cascade="all,delete,delete-orphan"
    )

    @property
    def traits(self):
        return set(t.name for t in self.db_traits)

    @property
    def rendered_name(self):
        rendered = game_info.render_name(self.species_name)
        return rendered


class SpeciesTrait(Base):
    __tablename__ = "speciestraittable"
    trait_id = Column(Integer, primary_key=True)

    description_id = Column(ForeignKey(SharedDescription.description_id))
    species_id = Column(ForeignKey(Species.species_id), index=True)

    species = relationship(Species, back_populates="db_traits")
    db_name = relationship(SharedDescription)

    @property
    def name(self) -> str:
        return self.db_name.text


class PoliticalFaction(Base):
    """Represents a single political faction in a game. Not tied to any specific time."""

    __tablename__ = "factiontable"
    faction_id = Column(Integer, primary_key=True)

    country_id = Column(ForeignKey(Country.country_id), index=True)

    faction_name = Column(String(80))
    faction_id_in_game = Column(Integer, index=True)
    faction_type_description_id = Column(ForeignKey(SharedDescription.description_id))

    db_faction_type = relationship("SharedDescription")
    country = relationship("Country", back_populates="political_factions")
    historical_events = relationship(
        "HistoricalEvent", back_populates="faction", cascade="all,delete,delete-orphan"
    )

    @property
    def type(self):
        return self.db_faction_type.text

    @property
    def rendered_name(self):
        rendered =  game_info.render_name(self.faction_name)
        return rendered


class War(Base):
    """Wars are represented by a list of participants and a list of combat events."""

    __tablename__ = "wartable"
    war_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))

    war_id_in_game = Column(Integer)
    name = Column(String(100), nullable=True)
    start_date_days = Column(Integer, index=True)
    end_date_days = Column(Integer)

    attacker_war_exhaustion = Column(Float, nullable=False, default=0)
    defender_war_exhaustion = Column(Float, nullable=False, default=0)

    outcome = Column(Enum(WarOutcome))

    game = relationship("Game", back_populates="wars")
    combat = relationship(
        "Combat", back_populates="war", cascade="all,delete,delete-orphan"
    )
    participants = relationship(
        "WarParticipant", back_populates="war", cascade="all,delete,delete-orphan"
    )

    @property
    def rendered_name(self):
        return (
            ", ".join(a.country.rendered_name for a in self.attackers)
            + " vs "
            + ", ".join(d.country.rendered_name for d in self.defenders)
        )

    @property
    def attackers(self) -> Iterable["WarParticipant"]:
        for p in self.participants:
            if p.is_attacker and p.call_type == "primary":
                yield p

    @property
    def defenders(self):
        for p in self.participants:
            if not p.is_attacker and p.call_type == "primary":
                yield p


class WarParticipant(Base):
    __tablename__ = "warparticipanttable"
    warparticipant_id = Column(Integer, primary_key=True)

    war_id = Column(ForeignKey(War.war_id), index=True)
    country_id = Column(ForeignKey(Country.country_id), index=True)
    caller_country_id = Column(ForeignKey(Country.country_id))
    is_attacker = Column(Boolean)
    war_goal = Column(String(80))
    call_type = Column(String(80))

    war = relationship("War", back_populates="participants")

    country = relationship(
        "Country", back_populates="war_participation", foreign_keys=[country_id]
    )
    caller_country = relationship("Country", foreign_keys=[caller_country_id])

    combat_participation = relationship(
        "CombatParticipant", back_populates="war_participant"
    )

    def get_war_goal(self):
        if self.war_goal is None:
            return "Unknown"
        return game_info.convert_id_to_name(self.war_goal, remove_prefix="wg")


class Combat(Base):
    __tablename__ = "combattable"
    combat_id = Column(Integer, primary_key=True)

    system_id = Column(ForeignKey(System.system_id), nullable=False)
    planet_id = Column(ForeignKey("planettable.planet_id"))
    war_id = Column(ForeignKey(War.war_id), nullable=False)

    date = Column(Integer, nullable=False, index=True)

    attacker_victory = Column(Boolean)
    attacker_war_exhaustion = Column(Float, nullable=False, default=0.0)
    defender_war_exhaustion = Column(Float, nullable=False, default=0.0)

    combat_type = Column(Enum(CombatType), nullable=False)

    system = relationship("System")
    planet = relationship("Planet")
    war = relationship("War", back_populates="combat")
    attackers = relationship(
        "CombatParticipant",
        primaryjoin="and_(Combat.combat_id==CombatParticipant.combat_id, CombatParticipant.is_attacker==True)",
    )
    defenders = relationship(
        "CombatParticipant",
        primaryjoin="and_(Combat.combat_id==CombatParticipant.combat_id, CombatParticipant.is_attacker==False)",
    )

    def involved_countries(self) -> Iterable[Country]:
        for cp in itertools.chain(self.attackers, self.defenders):
            yield cp.country

    def involved_country_ids(self) -> Collection[int]:
        return {cp.country_id for cp in itertools.chain(self.attackers, self.defenders)}


class CombatParticipant(Base):
    """
    CombatParticipants have their own model, because a single combat engagement may have
    more than two combatants, and the attacker in a single combat may be the defender in the
    overall war.
    """

    __tablename__ = "combatparticipant"
    combat_participant_id = Column(Integer, primary_key=True)

    combat_id = Column(ForeignKey(Combat.combat_id), index=True)
    war_participant_id = Column(
        ForeignKey(WarParticipant.warparticipant_id), index=True
    )

    is_attacker = Column(Boolean)

    war_participant = relationship(
        "WarParticipant", back_populates="combat_participation"
    )
    combat = relationship("Combat")

    @property
    def country(self):
        return self.war_participant.country


class Leader(Base):
    __tablename__ = "leadertable"

    leader_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    country_id = Column(ForeignKey(Country.country_id))
    leader_id_in_game = Column(Integer, index=True)

    first_name = Column(String(80))
    second_name = Column(String(80))

    species_id = Column(ForeignKey(Species.species_id))
    leader_class = Column(String(80))
    gender = Column(String(20))
    leader_agenda = Column(String(80))
    last_level = Column(Integer)

    date_hired = Column(Integer)  # The date when this leader was first encountered
    date_born = Column(Integer)  # estimated birthday
    last_date = Column(Integer)  # estimated death / dismissal
    is_active = Column(Boolean, index=True)
    fleet_id = Column(ForeignKey("fleettable.fleet_id"), nullable=True)

    game = relationship("Game", back_populates="leaders")
    country = relationship(
        "Country", back_populates="leaders", foreign_keys=[country_id], post_update=True
    )
    species = relationship("Species")
    fleet_command = relationship("Fleet", back_populates="commander")
    historical_events = relationship(
        "HistoricalEvent", back_populates="leader", cascade="all,delete,delete-orphan"
    )

    def get_name_and_class(self):
        return f"{self.leader_class.capitalize()} {self.get_name()}"

    @property
    def rendered_name(self):
        rendered_first = game_info.render_name(self.first_name)
        rendered_second = ""
        if self.second_name and self.second_name != '""':
            rendered_second = " " + game_info.render_name(self.second_name)
        rendered = f"{rendered_first}{rendered_second}"
        return rendered

    @property
    def agenda(self):
        return game_info.convert_id_to_name(self.leader_agenda, remove_prefix="agenda")


class Planet(Base):
    __tablename__ = "planettable"
    planet_id = Column(Integer, primary_key=True)

    planet_name = Column(String(50))
    planet_class = Column(String(20))
    planet_id_in_game = Column(Integer, index=True)
    system_id = Column(ForeignKey(System.system_id), nullable=False, index=True)
    colonized_date = Column(Integer)

    historical_events = relationship(
        "HistoricalEvent", back_populates="planet", cascade="all,delete,delete-orphan"
    )
    system = relationship("System", back_populates="planets")

    districts = relationship("PlanetDistrict", back_populates="planet")
    deposits = relationship("PlanetDeposit", back_populates="planet")
    buildings = relationship("PlanetBuilding", back_populates="planet")
    modifiers = relationship("PlanetModifier", back_populates="planet")

    # Add some integer attributes so we can cheaply detect updates
    districts_hash = Column(Integer, default=0)
    deposits_hash = Column(Integer, default=0)
    buildings_hash = Column(Integer, default=0)
    modifiers_hash = Column(Integer, default=0)

    @property
    def rendered_name(self):
        rendered = game_info.render_name(self.planet_name)
        return rendered

    @property
    def planetclass(self):
        return game_info.convert_id_to_name(self.planet_class, remove_prefix="pc")


class PlanetDistrict(Base):
    __tablename__ = "planet_district_table"
    district_id = Column(Integer, primary_key=True)

    planet_id = Column(ForeignKey(Planet.planet_id), nullable=False, index=True)
    description_id = Column(
        ForeignKey(SharedDescription.description_id), nullable=False
    )

    db_description = relationship("SharedDescription")
    count = Column(Integer, default=0)

    planet = relationship(Planet, back_populates="districts")

    @property
    def name(self):
        return game_info.convert_id_to_name(self.db_description.text, "district")


class PlanetDeposit(Base):
    __tablename__ = "planet_deposit_table"
    deposit_id = Column(Integer, primary_key=True)

    planet_id = Column(ForeignKey(Planet.planet_id), nullable=False, index=True)
    description_id = Column(
        ForeignKey(SharedDescription.description_id), nullable=False
    )

    db_description = relationship("SharedDescription")
    count = Column(Integer, default=0)

    planet = relationship(Planet, back_populates="deposits")

    @property
    def name(self):
        return game_info.convert_id_to_name(self.db_description.text, "d")

    @property
    def is_resource_deposit(self):
        return any(
            self.db_description.text.endswith(f"_{amount}") for amount in range(31)
        )


class PlanetBuilding(Base):
    __tablename__ = "planet_building_table"
    building_id = Column(Integer, primary_key=True)

    planet_id = Column(ForeignKey(Planet.planet_id), nullable=False, index=True)
    description_id = Column(
        ForeignKey(SharedDescription.description_id), nullable=False
    )

    db_description = relationship("SharedDescription")
    count = Column(Integer, default=0)

    planet = relationship(Planet, back_populates="buildings")

    @property
    def name(self):
        return game_info.convert_id_to_name(self.db_description.text, "building")


class PlanetModifier(Base):
    __tablename__ = "planet_modifier_table"
    modifier_id = Column(Integer, primary_key=True)

    planet_id = Column(ForeignKey(Planet.planet_id), nullable=False, index=True)
    description_id = Column(
        ForeignKey(SharedDescription.description_id), nullable=False
    )

    expiry_date = Column(Integer)
    db_description = relationship("SharedDescription")

    planet = relationship(Planet, back_populates="modifiers")

    @property
    def name(self):
        return game_info.convert_id_to_name(self.db_description.text, "pm")


class PopStatsBySpecies(Base):
    __tablename__ = "popstats_species_table"
    pop_stats_species_id = Column(Integer, primary_key=True)
    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)
    species_id = Column(ForeignKey(Species.species_id))

    pop_count = Column(Integer)
    happiness = Column(Float)
    power = Column(Float)
    crime = Column(Float)

    country_data = relationship("CountryData", back_populates="pop_stats_species")
    species = relationship("Species")


class PopStatsByFaction(Base):
    __tablename__ = "popstats_faction_table"
    pop_stats_faction_id = Column(Integer, primary_key=True)
    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)
    faction_id = Column(ForeignKey(PoliticalFaction.faction_id))

    pop_count = Column(Integer)
    happiness = Column(Float)
    power = Column(Float)
    crime = Column(Float)

    faction_approval = Column(Float, default=0.0)
    support = Column(Float, default=0.0)

    country_data = relationship("CountryData", back_populates="pop_stats_faction")
    faction = relationship("PoliticalFaction")


class PopStatsByJob(Base):
    __tablename__ = "popstats_job_table"
    pop_stats_job_id = Column(Integer, primary_key=True)
    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)
    job_description_id = Column(ForeignKey(SharedDescription.description_id))

    pop_count = Column(Integer)
    happiness = Column(Float)
    power = Column(Float)
    crime = Column(Float)

    db_job_description = relationship(SharedDescription)
    country_data = relationship("CountryData", back_populates="pop_stats_job")

    @property
    def job_description(self) -> str:
        return self.db_job_description.text


class PopStatsByStratum(Base):
    __tablename__ = "popstats_stratum_table"
    pop_stats_stratum_id = Column(Integer, primary_key=True)
    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)
    stratum_description_id = Column(ForeignKey(SharedDescription.description_id))

    pop_count = Column(Integer)
    happiness = Column(Float)
    power = Column(Float)
    crime = Column(Float)

    db_stratum_description = relationship(SharedDescription)
    country_data = relationship("CountryData", back_populates="pop_stats_stratum")

    @property
    def stratum(self) -> str:
        return self.db_stratum_description.text


class PopStatsByEthos(Base):
    __tablename__ = "popstats_ethos_table"
    pop_stats_stratum_id = Column(Integer, primary_key=True)
    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)
    ethos_description_id = Column(ForeignKey(SharedDescription.description_id))

    pop_count = Column(Integer)
    happiness = Column(Float)
    power = Column(Float)
    crime = Column(Float)

    db_ethos_description = relationship(SharedDescription)
    country_data = relationship("CountryData", back_populates="pop_stats_ethos")

    @property
    def ethos(self) -> str:
        return self.db_ethos_description.text


class PlanetStats(Base):
    __tablename__ = "planetstats_table"
    planet_stats_id = Column(Integer, primary_key=True)
    countrydata_id = Column(ForeignKey(CountryData.country_data_id), index=True)

    planet_id = Column(ForeignKey(Planet.planet_id))

    pop_count = Column(Integer)
    happiness = Column(Float)
    power = Column(Float)
    crime = Column(Float)

    migration = Column(Float)
    free_amenities = Column(Float)
    free_housing = Column(Float)
    stability = Column(Float)

    planet = relationship(Planet)
    country_data = relationship(CountryData, back_populates="pop_stats_planets")


class Fleet(Base):
    __tablename__ = "fleettable"
    fleet_id = Column(Integer, primary_key=True)

    fleet_id_in_game = Column(Integer, nullable=False, index=True)
    is_civilian_fleet = Column(Boolean, nullable=False)
    name = Column(String(80))

    commander = relationship(Leader)

    @property
    def rendered_name(self):
        rendered = game_info.render_name(self.name)
        return rendered


class HistoricalEvent(Base):
    """
    This class represents various historical events used for the text ledger.
    The event_type specifies which event is encoded, depending on which
    certain columns may be null.
    """

    __tablename__ = "historicaleventtable"
    historical_event_id = Column(Integer, primary_key=True)

    event_type = Column(Enum(HistoricalEventType), nullable=False, index=True)
    start_date_days = Column(Integer, nullable=False, index=True)
    event_is_known_to_player = Column(Boolean, nullable=False, default=False)

    # Any of the following columns may be undefined, depending on the event type.
    country_id = Column(ForeignKey(Country.country_id), index=True)
    leader_id = Column(ForeignKey(Leader.leader_id), nullable=True, index=True)
    war_id = Column(ForeignKey(War.war_id), nullable=True)
    combat_id = Column(ForeignKey(Combat.combat_id), nullable=True)
    system_id = Column(ForeignKey(System.system_id), nullable=True)
    planet_id = Column(ForeignKey(Planet.planet_id), nullable=True)
    faction_id = Column(ForeignKey(PoliticalFaction.faction_id), nullable=True)
    description_id = Column(ForeignKey(SharedDescription.description_id), nullable=True)
    target_country_id = Column(ForeignKey(Country.country_id), nullable=True)
    fleet_id = Column(ForeignKey(Fleet.fleet_id), nullable=True)
    end_date_days = Column(Integer)

    country = relationship(
        Country, back_populates="historical_events", foreign_keys=[country_id]
    )
    target_country = relationship(Country, foreign_keys=[target_country_id])
    war = relationship(War)
    combat = relationship(Combat)
    leader = relationship(Leader, back_populates="historical_events")
    system = relationship(System, back_populates="historical_events")
    planet = relationship(Planet, back_populates="historical_events")
    faction = relationship(PoliticalFaction, back_populates="historical_events")
    fleet = relationship(Fleet)
    db_description = relationship(SharedDescription)

    def __str__(self):
        start_date = days_to_date(self.start_date_days)
        text = f"{start_date} (A): {str(self.event_type)} {self.description}"
        if self.end_date_days:
            end_date = days_to_date(self.end_date_days)
            text = (
                f"{start_date} - {end_date}: {str(self.event_type)} {self.description}"
            )
        return text

    @property
    def description(self) -> str:
        """A brief description associated with the event, e.g. technology name or changes in government reform."""
        if self.event_type == HistoricalEventType.tradition:
            return game_info.convert_id_to_name(
                self.db_description.text, remove_prefix="tr"
            )
        elif self.event_type == HistoricalEventType.researched_technology:
            return game_info.convert_id_to_name(
                self.db_description.text, remove_prefix="tech"
            )
        elif self.event_type == HistoricalEventType.edict:
            return game_info.convert_id_to_name(self.db_description.text)
        elif self.event_type == HistoricalEventType.ascension_perk:
            return game_info.convert_id_to_name(
                self.db_description.text, remove_prefix="ap"
            )
        elif self.event_type == HistoricalEventType.government_reform:
            old_gov = self.country.get_government_for_date(self.start_date_days - 1)
            new_gov = self.country.get_government_for_date(self.start_date_days)
            reform_dict = new_gov.get_reform_description_dict(old_gov)
            reform_lines = [
                f"{cat} " + ", ".join(reforms) + "."
                for (cat, reforms) in reform_dict.items()
            ]
            ref = " ".join(reform_lines)
            return ref
        elif self.event_type == HistoricalEventType.terraforming:
            current_pc, target_pc = self.db_description.text.split(",")
            current_pc = game_info.convert_id_to_name(current_pc, remove_prefix="pc")
            target_pc = game_info.convert_id_to_name(target_pc, remove_prefix="pc")
            return f"from {current_pc} to {target_pc}"
        elif self.event_type in [
            HistoricalEventType.envoy_federation,
            HistoricalEventType.formed_federation,
            HistoricalEventType.governed_sector,
        ]:
            return game_info.render_name(self.db_description.text)
        elif self.event_type in [HistoricalEventType.war]:
            call_type = self.db_description.text
            if call_type == "primary":
                return " as a primary party"
            elif call_type == "overlord":
                return f" called by their overlord"
            elif call_type == "offensive":
                return f", attacking on the side of the"
            elif call_type == "defensive":
                return f" due to their defensive pact with the"
            elif call_type == "alliance":
                return f" due to their alliance with the"
            else:
                return f" (called as {call_type})"
        elif self.event_type == HistoricalEventType.councilor:
            return game_info.render_name(json.dumps({"key": self.db_description.text}))
        elif self.db_description:
            return game_info.convert_id_to_name(self.db_description.text)
        else:
            return "Unknown Event"

    def involved_countries(self):
        if self.country_id is not None:
            yield self.country
        if self.target_country_id is not None:
            yield self.target_country
        if self.combat_id is not None:
            yield from self.combat.involved_countries()
