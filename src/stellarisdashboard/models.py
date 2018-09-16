import contextlib
import enum
import logging
import pathlib
import threading
from typing import Dict, List

import sqlalchemy
from sqlalchemy import Column, Integer, String, ForeignKey, Float, Boolean, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, scoped_session

from stellarisdashboard import config, game_info

logger = logging.getLogger(__name__)
_ENGINES = {}
_SESSIONMAKERS = {}
DB_PATH: pathlib.Path = config.CONFIG.base_output_path / "db"
if not DB_PATH.exists():
    logger.info(f"Creating database path {DB_PATH}")
    DB_PATH.mkdir()

Base = declarative_base()

_DB_LOCK = threading.Lock()


@contextlib.contextmanager
def get_db_session(game_id) -> sqlalchemy.orm.Session:
    with _DB_LOCK:
        if game_id not in _SESSIONMAKERS:
            db_file = DB_PATH / f"{game_id}.db"
            if not db_file.exists():
                logger.info(f"Creating database for game {game_id} in file {db_file}.")
            engine = sqlalchemy.create_engine(f'sqlite:///{db_file}', echo=False)
            Base.metadata.create_all(bind=engine)
            _ENGINES[game_id] = engine
            _SESSIONMAKERS[game_id] = scoped_session(sessionmaker(bind=engine))

        session_factory = _SESSIONMAKERS[game_id]
        s = session_factory()
        try:
            yield s
        finally:
            s.close()


@enum.unique
class GovernmentAuthority(enum.Enum):
    democratic = 0
    oligarchic = 1
    dictatorial = 2
    imperial = 3
    hive_mind = 4
    machine_intelligence = 5

    other = 99

    @classmethod
    def from_str(cls, auth_str):
        auth_str = auth_str.split("auth_")[-1]
        return cls.__members__.get(auth_str, cls.other)

    def __str__(self):
        if self == self.democratic:
            return "Democracy"
        elif self == self.oligarchic:
            return "Oligarchy"
        elif self == self.dictatorial:
            return "Dictatorship"
        elif self == self.imperial:
            return "Imperium"
        elif self == self.hive_mind:
            return "Hive Mind"
        elif self == self.machine_intelligence:
            return "Machine Intelligence"
        else:
            return "Other"


@enum.unique
class Attitude(enum.Enum):
    unknown = 0
    neutral = 1
    wary = 2
    receptive = 3
    cordial = 4
    friendly = 5
    protective = 6
    unfriendly = 7
    rival = 8
    hostile = 9
    domineering = 10
    threatened = 11
    overlord = 12
    loyal = 13
    disloyal = 14
    dismissive = 15
    patronizing = 16
    angry = 17
    arrogant = 18
    imperious = 19
    belligerent = 20
    custodial = 21
    enigmatic = 22
    berserk = 23

    def reveals_military_info(self):
        return self in {Attitude.friendly, Attitude.loyal, Attitude.disloyal, Attitude.overlord}

    def reveals_technology_info(self):
        return self.reveals_military_info() or (self in {Attitude.protective, Attitude.disloyal})

    def reveals_economy_info(self):
        return self.reveals_technology_info() or (self in {Attitude.cordial, Attitude.receptive})

    def reveals_demographic_info(self):
        return self.reveals_economy_info() or (self in {Attitude.neutral, Attitude.wary})

    def is_known(self):
        return self != Attitude.unknown


@enum.unique
class CombatType(enum.Enum):
    ships = 0
    armies = 1

    other = 99

    # TODO are there other types of war events?

    def __str__(self):
        if self == CombatType.ships:
            return "Fleet combat"
        elif self == CombatType.armies:
            return "Planetary invasion"
        else:
            return "Other engagement"


@enum.unique
class WarOutcome(enum.Enum):
    status_quo = 0
    attacker_victory = 1
    defender_victory = 2
    in_progress = 3
    other = 99


@enum.unique
class WarGoal(enum.Enum):
    """
    A list of possible wargoals, taken/adapted from https://stellaris.paradoxwikis.com/War_goals
    """
    wg_conquest = 0
    wg_force_ideology = 1
    wg_vassalize = 2
    wg_make_tributary = 3
    wg_humiliate = 4
    wg_independence = 5
    wg_plunder = 6
    wg_colossus = 7
    wg_stop_colossus = 8
    wg_cleansing = 9  # fanatic purifiers
    wg_absorption = 10  # devouring swarm
    wg_assimilation = 11  # driven assimilators
    wg_end_threat = 12

    # Fallen Empire Wargoals
    wg_stop_atrocities = 13
    wg_outlaw_ai = 14
    wg_cleanse_holy_world = 15
    wg_decontaminate = 16

    wg_other = 99

    def __str__(self):
        descriptions = {
            self.wg_conquest: "Conquest",
            self.wg_force_ideology: "Force Ideology",
            self.wg_vassalize: "Vassalize",
            self.wg_make_tributary: "Make Tributary",
            self.wg_humiliate: "Humiliate",
            self.wg_independence: "Independence",
            self.wg_plunder: "Plunder",
            self.wg_colossus: "Colossus",
            self.wg_stop_colossus: "Stop Colossus",
            self.wg_cleansing: "Cleansing",
            self.wg_absorption: "Absorption",
            self.wg_assimilation: "Assimilation",
            self.wg_end_threat: "End Threat",
            self.wg_stop_atrocities: "Stop Atrocities",
            self.wg_outlaw_ai: "Outlaw AI",
            self.wg_cleanse_holy_world: "Cleanse Holy World",
            self.wg_decontaminate: "Decontaminate",
            self.wg_other: "Other",
        }
        return descriptions[self]


@enum.unique
class PopEthics(enum.IntEnum):
    imperialist = 0
    isolationist = 1
    progressive = 2
    prosperity = 3
    supremacist = 4
    technologist = 5
    totalitarian = 6
    traditionalist = 7
    xenoist = 8
    enslaved = 9
    purge = 10
    no_ethics = 11  # e.g. robots1476894696

    other = 99  # the other values should be exhaustive, but this value serves as a default fallback to detect any issues.

    @classmethod
    def from_str(cls, ethics_description: str):
        return cls.__members__.get(ethics_description, PopEthics.other)


@enum.unique
class LeaderClass(enum.Enum):
    ruler = 0
    governor = 1
    scientist = 2
    admiral = 3
    general = 4

    unknown = 99


@enum.unique
class LeaderGender(enum.Enum):
    female = 0
    male = 1
    other = 2


@enum.unique
class LeaderAgenda(enum.Enum):
    secure_the_borders = 0
    fleet_expansion = 1
    develop_industry = 2
    scientific_leap = 3
    grow_economy = 4
    a_new_generation = 5
    expansionist_overtures = 6
    national_purity = 7
    public_debates = 8
    import_export = 9
    native_privilege = 10
    skill_development = 11
    slave_optimizations = 12
    selective_nostalgia = 13
    xeno_outreach = 14
    other = 99

    def __str__(self):
        descriptions = {
            self.secure_the_borders: "Secure the Borders",
            self.fleet_expansion: "Fleet Expansion",
            self.develop_industry: "Develop Industry",
            self.scientific_leap: "Scientific Leap",
            self.grow_economy: "Grow Economy",
            self.a_new_generation: "New Generation",
            self.expansionist_overtures: "Expansionist Overtures",
            self.national_purity: "National Purity",
            self.public_debates: "Public Debates",
            self.import_export: "Import / Export",
            self.native_privilege: "Native Privilege",
            self.skill_development: "Skill Development",
            self.slave_optimizations: "Slave Optimizations",
            self.selective_nostalgia: "Selective Nostalgia",
            self.xeno_outreach: "Xeno Outreach",
            self.other: "Other",
        }
        return descriptions[self]


AGENDA_STR_TO_ENUM = dict(
    agenda_defensive_focus=LeaderAgenda.secure_the_borders,
    agenda_naval_focus=LeaderAgenda.fleet_expansion,
    agenda_industrial=LeaderAgenda.develop_industry,
    agenda_science=LeaderAgenda.scientific_leap,
    agenda_finanical=LeaderAgenda.grow_economy,
    agenda_welfare=LeaderAgenda.a_new_generation,
    agenda_expansionist_overtures=LeaderAgenda.expansionist_overtures,
    agenda_national_purity=LeaderAgenda.national_purity,
    agenda_public_debates=LeaderAgenda.public_debates,
    agenda_import_export=LeaderAgenda.import_export,
    agenda_native_privilege=LeaderAgenda.native_privilege,
    agenda_skill_development=LeaderAgenda.skill_development,
    agenda_slave_optimization=LeaderAgenda.slave_optimizations,
    agenda_selective_nostalgia=LeaderAgenda.selective_nostalgia,
    agenda_xeno_outreach=LeaderAgenda.xeno_outreach,
    agenda_null=LeaderAgenda.other,
)


@enum.unique
class HistoricalEventType(enum.Enum):
    # tied to a specific leader:
    ruled_empire = enum.auto()
    governed_sector = enum.auto()
    faction_leader = enum.auto()

    # civilizational advancement:
    researched_technology = enum.auto()
    tradition = enum.auto()
    ascension_perk = enum.auto()
    edict = enum.auto()

    # expansion:
    colonization = enum.auto()
    discovered_new_system = enum.auto()  # TODO: for when new systems are detected, e.g. precursor homeworlds
    habitat_ringworld_construction = enum.auto()
    megastructure_construction = enum.auto()
    sector_creation = enum.auto()  # TODO?

    # related to internal politics:
    government_reform = enum.auto()
    species_rights_reform = enum.auto()  # TODO this would be cool!
    capital_relocation = enum.auto()  # TODO
    planetary_unrest = enum.auto()

    # diplomacy and war:
    opened_borders = enum.auto()  # TODO
    first_contact = enum.auto()  # TODO
    non_aggression_pact = enum.auto()
    defensive_pact = enum.auto()
    formed_federation = enum.auto()  # TODO

    closed_borders = enum.auto()
    insult = enum.auto()
    rivalry_declaration = enum.auto()
    sent_war_declaration = enum.auto()
    received_war_declaration = enum.auto()
    peace = enum.auto()


@enum.unique
class LeaderAchievementType(enum.Enum):
    # All leaders:
    was_faction_leader = 5

    # Scientists:
    researched_technology = 0

    # Admirals:
    won_fleet_battle = 1

    # Generals:
    won_planet_invasion = 2

    # Rulers:
    was_ruler = 3
    negotiated_peace_treaty = 4
    passed_edict = 6
    embraced_tradition = 7
    achieved_ascension = 8
    reformed_government = 9

    # Governors:
    governed_sector = 10
    built_megastructure = 11
    colonized_planet = 12

    special_event = 99


def date_to_days(date_str: str) -> float:
    y, m, d = map(int, date_str.split("."))
    return (y - 2200) * 360 + (m - 1) * 30 + d - 1


def days_to_date(days: float) -> str:
    days = int(days)
    year_offset = days // 360
    days -= 360 * year_offset
    month_offset = days // 30
    year = 2200 + year_offset
    month = 1 + month_offset
    day = days - 30 * month_offset + 1
    return f"{year}.{month}.{day}"


def get_last_modified_time(path: pathlib.Path) -> int:
    return path.stat().st_mtime


def get_known_games(game_name_prefix: str = "") -> List[str]:
    files = sorted(DB_PATH.glob(f"{game_name_prefix}*.db"),
                   key=get_last_modified_time, reverse=True)
    return [fname.stem for fname in files]


def get_available_games_dict() -> Dict[str, str]:
    """ Returns a dictionary mapping game id to the name of the game's player country. """
    games = {}
    for game_name in get_known_games():
        with get_db_session(game_name) as session:
            game = session.query(Game).one_or_none()
            if game is None:
                continue
            games[game_name] = game.player_country_name
    return games


def get_gamestates_since(game_name, date):
    with get_db_session(game_name) as session:
        game = session.query(Game).one()
        for gs in session.query(GameState).filter(GameState.game == game, GameState.date > date).order_by(GameState.date).all():
            yield gs


class Game(Base):
    """Root object representing an entire game."""
    __tablename__ = 'gametable'
    game_id = Column(Integer, primary_key=True)
    game_name = Column(String(50))
    player_country_name = Column(String(50))

    systems = relationship("System", back_populates="game", cascade="all,delete,delete-orphan")
    countries = relationship("Country", back_populates="game", cascade="all,delete,delete-orphan")
    species = relationship("Species", back_populates="game", cascade="all,delete,delete-orphan")
    game_states = relationship("GameState", back_populates="game", cascade="all,delete,delete-orphan")
    wars = relationship("War", back_populates="game", cascade="all,delete,delete-orphan")
    leaders = relationship("Leader", back_populates="game", cascade="all,delete,delete-orphan")

    def __repr__(self):
        return f"Game(game_id={self.game_id}, game_name={self.game_name})"


class System(Base):
    __tablename__ = 'systemtable'
    system_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))

    system_id_in_game = Column(Integer, index=True)
    original_name = Column(String(80))

    coordinate_x = Column(Float)
    coordinate_y = Column(Float)

    game = relationship("Game", back_populates="systems")
    ownership = relationship("SystemOwnership", back_populates="system", cascade="all,delete,delete-orphan")

    # this could probably be done better....
    hyperlanes_one = relationship("HyperLane", foreign_keys=lambda: [HyperLane.system_one_id])
    hyperlanes_two = relationship("HyperLane", foreign_keys=lambda: [HyperLane.system_two_id])

    historical_events = relationship("HistoricalEvent", back_populates="system", cascade="all,delete,delete-orphan")

    def get_owner_country_id_at(self, time_in_days: int) -> int:
        if not self.ownership:
            return -1
        # could be slow, but fine for now
        most_recent_owner = -1
        most_recent_owner_end = -1
        for ownership in self.ownership:
            if most_recent_owner_end <= ownership.start_date_days <= time_in_days:
                most_recent_owner_end = ownership.end_date_days
                most_recent_owner = ownership.country.country_id
        return most_recent_owner

    def __str__(self):
        return f'System "{self.original_name}" @ {self.coordinate_x}, {self.coordinate_y}'


class SystemOwnership(Base):
    __tablename__ = 'systemownershiptable'
    system_ownership_id = Column(Integer, primary_key=True)

    system_id = Column(ForeignKey(System.system_id), index=True)
    owner_country_id = Column(ForeignKey("countrytable.country_id"), index=True)

    start_date_days = Column(Integer, index=True)
    end_date_days = Column(Integer, index=True)

    system = relationship("System", back_populates="ownership")
    country = relationship("Country", back_populates="owned_systems")

    def __str__(self):
        return f"SystemOwnership of {self.system.original_name}: {self.start_date_days} - {self.end_date_days} -- {self.country.country_name}"


class HyperLane(Base):
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


class GameState(Base):
    """Represents the state of the game at a specific moment."""
    __tablename__ = 'gamestatetable'
    gamestate_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    date = Column(Integer, index=True)  # Days since 2200.1.1

    # Player budget: detailed information
    mineral_income_base = Column(Float, default=0.0)
    mineral_income_production = Column(Float, default=0.0)
    mineral_income_trade = Column(Float, default=0.0)
    mineral_income_enclaves = Column(Float, default=0.0)
    mineral_income_mission = Column(Float, default=0.0)
    mineral_income_sectors = Column(Float, default=0.0)
    mineral_income_other = Column(Float, default=0.0)
    mineral_spending_pop = Column(Float, default=0.0)
    mineral_spending_ship = Column(Float, default=0.0)
    mineral_spending_trade = Column(Float, default=0.0)
    mineral_spending_enclaves = Column(Float, default=0.0)
    mineral_spending_mission = Column(Float, default=0.0)
    mineral_spending_other = Column(Float, default=0.0)

    food_income_base = Column(Float, default=0.0)
    food_income_production = Column(Float, default=0.0)
    food_income_trade = Column(Float, default=0.0)
    food_income_enclaves = Column(Float, default=0.0)
    food_income_sectors = Column(Float, default=0.0)
    food_income_other = Column(Float, default=0.0)
    food_spending = Column(Float, default=0.0)
    food_spending_trade = Column(Float, default=0.0)
    food_spending_enclaves = Column(Float, default=0.0)
    food_spending_sectors = Column(Float, default=0.0)
    food_spending_other = Column(Float, default=0.0)

    energy_income_base = Column(Float, default=0.0)
    energy_income_production = Column(Float, default=0.0)
    energy_income_trade = Column(Float, default=0.0)
    energy_income_enclaves = Column(Float, default=0.0)
    energy_income_sectors = Column(Float, default=0.0)
    energy_income_other = Column(Float, default=0.0)
    energy_income_mission = Column(Float, default=0.0)
    energy_spending_army = Column(Float, default=0.0)
    energy_spending_building = Column(Float, default=0.0)
    energy_spending_pop = Column(Float, default=0.0)
    energy_spending_ship = Column(Float, default=0.0)
    energy_spending_station = Column(Float, default=0.0)
    energy_spending_colonization = Column(Float, default=0.0)
    energy_spending_starbases = Column(Float, default=0.0)
    energy_spending_mission = Column(Float, default=0.0)
    energy_spending_trade = Column(Float, default=0.0)
    energy_spending_enclaves = Column(Float, default=0.0)
    energy_spending_other = Column(Float, default=0.0)

    game = relationship("Game", back_populates="game_states")
    country_data = relationship("CountryData", back_populates="game_state", cascade="all,delete,delete-orphan")


class Country(Base):
    __tablename__ = 'countrytable'
    country_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))

    is_player = Column(Boolean)
    country_name = Column(String(80))
    country_id_in_game = Column(Integer)
    first_player_contact_date = Column(Integer)
    country_type = Column(String(50))

    game = relationship("Game", back_populates="countries")
    governments = relationship("Government", back_populates="country", cascade="all,delete,delete-orphan")
    country_data = relationship("CountryData", back_populates="country", cascade="all,delete,delete-orphan")
    political_factions = relationship("PoliticalFaction", back_populates="country", cascade="all,delete,delete-orphan")
    war_participation = relationship("WarParticipant", back_populates="country", cascade="all,delete,delete-orphan")
    owned_systems = relationship(SystemOwnership, back_populates="country", cascade="all,delete,delete-orphan")
    leaders = relationship("Leader", back_populates="country", cascade="all,delete,delete-orphan")
    historical_events = relationship("HistoricalEvent", back_populates="country", cascade="all,delete,delete-orphan")

    def get_government_for_date(self, date_in_days) -> "Government":
        # could be slow, but fine for now
        for gov in self.governments:
            if gov.start_date_days <= date_in_days <= gov.end_date_days:
                return gov


class Government(Base):
    """
    Representation of a country's government, as specified by the country name, the government type (flavor text, e.g. "executive committee"),
    the governing authority (e.g. "Democracy"), the date range, as well as the governing ethics and civics.
    """
    __tablename__ = 'govtable'
    gov_id = Column(Integer, primary_key=True)

    country_id = Column(ForeignKey(Country.country_id), index=True)

    start_date_days = Column(Integer, index=True)
    end_date_days = Column(Integer, index=True)

    gov_name = Column(String(100))
    gov_type = Column(String(100))
    authority = Column(Enum(GovernmentAuthority))

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
        civics = {self.ethics_1, self.ethics_2, self.ethics_3, self.ethics_4, self.ethics_5}
        return civics - {None}

    def get_reform_description_dict(self, old_gov: "Government") -> Dict[str, str]:
        reform_dict = {}
        if old_gov.authority != self.authority:
            old_authority = str(old_gov.authority)
            new_authority = str(self.authority)
            reform_dict["Authority"] = [f"From {old_authority} to {new_authority}"]

        if old_gov.gov_type != self.gov_type:
            ogt = game_info.convert_id_to_name(old_gov.gov_type, remove_prefix="gov")
            ngt = game_info.convert_id_to_name(self.gov_type, remove_prefix="gov")
            reform_dict["Type"] = [f"From {ogt} to {ngt}"]

        new_civics = self.civics - old_gov.civics
        removed_civics = old_gov.civics - self.civics
        reform_dict["Removed Civics"] = sorted(game_info.convert_id_to_name(c, remove_prefix="civic") for c in removed_civics)
        reform_dict["Adopted Civics"] = sorted(game_info.convert_id_to_name(c, remove_prefix="civic") for c in new_civics)

        new_ethics = self.ethics - old_gov.ethics
        removed_ethics = old_gov.ethics - self.ethics
        reform_dict["Abandoned Ethics"] = sorted(game_info.convert_id_to_name(e, remove_prefix="ethic") for e in removed_ethics)
        reform_dict["Embraced Ethics"] = sorted(game_info.convert_id_to_name(e, remove_prefix="ethic") for e in new_ethics)
        return {k: v for k, v in reform_dict.items() if v}

    def __str__(self):
        return f"{self.authority} {self.gov_type} {self.civics} {self.ethics}"


class CountryData(Base):
    """
    Contains the state of the country at a specific moment, specifically basic data about economy, science and military,
    as well as diplomatic attitudes towards the player counter.

    Child objects contain data about demographics and political factions.
    """
    __tablename__ = 'countrydatatable'
    country_data_id = Column(Integer, primary_key=True)
    country_id = Column(ForeignKey(Country.country_id), index=True)
    game_state_id = Column(ForeignKey(GameState.gamestate_id), index=True)

    military_power = Column(Float)
    fleet_size = Column(Float)
    tech_progress = Column(Integer)
    exploration_progress = Column(Integer)
    owned_planets = Column(Integer)
    controlled_systems = Column(Integer)

    # Basic economic data
    mineral_income = Column(Float, default=0.0)
    mineral_spending = Column(Float, default=0.0)
    food_income = Column(Float, default=0.0)
    food_spending = Column(Float, default=0.0)
    energy_income = Column(Float, default=0.0)
    energy_spending = Column(Float, default=0.0)
    unity_income = Column(Float, default=0.0)
    unity_spending = Column(Float, default=0.0)
    influence_income = Column(Float, default=0.0)
    influence_spending = Column(Float, default=0.0)
    society_research = Column(Float, default=0.0)
    physics_research = Column(Float, default=0.0)
    engineering_research = Column(Float, default=0.0)

    # Diplomacy towards player
    attitude_towards_player = Column(Enum(Attitude))
    has_research_agreement_with_player = Column(Boolean)
    has_sensor_link_with_player = Column(Boolean)
    has_rivalry_with_player = Column(Boolean)
    has_defensive_pact_with_player = Column(Boolean)
    has_migration_treaty_with_player = Column(Boolean)
    has_federation_with_player = Column(Boolean)
    has_non_aggression_pact_with_player = Column(Boolean)
    has_closed_borders_with_player = Column(Boolean)
    has_communications_with_player = Column(Boolean)

    country = relationship("Country", back_populates="country_data")
    game_state = relationship("GameState", back_populates="country_data")
    pop_counts = relationship("PopCount", back_populates="country_data", cascade="all,delete,delete-orphan")
    faction_support = relationship("FactionSupport", back_populates="country_data", cascade="all,delete,delete-orphan")

    def __repr__(self):
        return f"CountryData(country_name={self.country_name}, game_state={self.gamestate_id}, military_power={self.military_power}, fleet_size={self.fleet_size}, tech_progress={self.tech_progress}, exploration_progress={self.exploration_progress}, owned_planets={self.owned_planets})"


class Species(Base):
    """Represents a species in a game. Not tied to any specific time."""
    __tablename__ = 'speciestable'
    species_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    species_id_in_game = Column(Integer)

    species_name = Column(String(80))
    parent_species_id_in_game = Column(Integer)
    is_robotic = Column(Boolean)

    game = relationship("Game", back_populates="species")

    def __repr__(self):
        return f"Species(species_id={self.species_id}, game_id={self.game_id}, species_name={self.species_name})"


class PopCount(Base):
    """Contains the number of members of a single species in a single country."""
    __tablename__ = 'popcounttable'
    pc_id = Column(Integer, primary_key=True)
    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)
    species_id = Column(ForeignKey(Species.species_id), index=True)
    pop_count = Column(Integer)

    country_data = relationship("CountryData", back_populates="pop_counts")
    species = relationship("Species")

    def __repr__(self):
        return f"PopCount(country_data_id={self.country_data_id}, species_name={self.species_name}, pop_count={self.pop_count})"


class PoliticalFaction(Base):
    """Represents a single political faction in a game. Not tied to any specific time."""
    __tablename__ = 'factiontable'
    faction_id = Column(Integer, primary_key=True)

    country_id = Column(ForeignKey(Country.country_id), index=True)

    faction_name = Column(String(80))
    faction_id_in_game = Column(Integer)
    ethics = Column(Enum(PopEthics))

    country = relationship("Country", back_populates="political_factions")
    faction_support = relationship("FactionSupport", back_populates="faction")
    historical_events = relationship("HistoricalEvent", back_populates="faction", cascade="all,delete,delete-orphan")

    def __repr__(self):
        return f"PoliticalFaction(faction_id={self.faction_id}, country_id={self.country_id}, faction_name={self.faction_name})"


class FactionSupport(Base):
    """Contains data about the status of a political faction at a specific time."""
    __tablename__ = 'factionsupporttable'

    fs_id = Column(Integer, primary_key=True)
    country_data_id = Column(ForeignKey(CountryData.country_data_id), index=True)
    faction_id = Column(ForeignKey(PoliticalFaction.faction_id))

    members = Column(Integer)
    support = Column(Float)
    happiness = Column(Float)

    country_data = relationship("CountryData", back_populates="faction_support")
    faction = relationship("PoliticalFaction", back_populates="faction_support")

    def __repr__(self):
        return f"FactionSupport(fs_id={self.fs_id}, faction_id={self.faction_id}, country_data_id={self.country_data_id}, members={self.members}, happiness={self.happiness}, support={self.support})"


class War(Base):
    __tablename__ = 'wartable'
    war_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))

    war_id_in_game = Column(Integer)
    name = Column(String(100))
    start_date_days = Column(Integer, index=True)
    end_date_days = Column(Integer, index=True)

    attacker_war_exhaustion = Column(Float)
    defender_war_exhaustion = Column(Float)

    outcome = Column(Enum(WarOutcome))

    game = relationship("Game", back_populates="wars")
    combat = relationship("Combat", back_populates="war", cascade="all,delete,delete-orphan")
    participants = relationship("WarParticipant", back_populates="war", cascade="all,delete,delete-orphan")

    def __repr__(self):
        return f"War(war_id={self.war_id}, game_id={self.game_id}, start_date_days={self.start_date_days}, name={self.name})"


class WarParticipant(Base):
    __tablename__ = 'warparticipanttable'
    warparticipant_id = Column(Integer, primary_key=True)

    war_id = Column(ForeignKey(War.war_id), index=True)
    country_id = Column(ForeignKey(Country.country_id), index=True)
    is_attacker = Column(Boolean)
    war_goal = Column(Enum(WarGoal))

    war = relationship("War", back_populates="participants")
    country = relationship("Country", back_populates="war_participation")
    combat_participation = relationship("CombatParticipant", back_populates="war_participant")

    def __repr__(self):
        return f"WarParticipant(war_id={self.war_id}, country_id={self.country_id}, is_attacker={self.is_attacker})"


class Combat(Base):
    __tablename__ = "combattable"
    combat_id = Column(Integer, primary_key=True)

    system_id = Column(ForeignKey(System.system_id))
    war_id = Column(ForeignKey(War.war_id))

    date = Column(Integer, index=True)

    attacker_victory = Column(Boolean)
    attacker_war_exhaustion = Column(Float)
    defender_war_exhaustion = Column(Float)

    planet = Column(String(50))

    combat_type = Column(Enum(CombatType))

    system = relationship("System")
    war = relationship("War", back_populates="combat")
    attackers = relationship("CombatParticipant", primaryjoin="and_(Combat.combat_id==CombatParticipant.combat_id, CombatParticipant.is_attacker==True)")
    defenders = relationship("CombatParticipant", primaryjoin="and_(Combat.combat_id==CombatParticipant.combat_id, CombatParticipant.is_attacker==False)")

    def __str__(self):
        loc = ""
        if self.planet:
            loc += f'planet "{self.planet}"'
        if self.system:
            if loc:
                loc += " in the "
            loc += f'"{self.system.original_name}" system'

        defenders = ", ".join(f'"{cp.war_participant.country.country_name}"' for cp in self.defenders)
        if self.defender_war_exhaustion > 0:
            defenders += f" ({self.defender_war_exhaustion} exhaustion)"

        attackers = ", ".join(f'"{cp.war_participant.country.country_name}"' for cp in self.attackers)
        if self.attacker_war_exhaustion > 0:
            attackers += f" ({self.attacker_war_exhaustion} exhaustion)"

        if self.combat_type == CombatType.armies:
            result = f"{days_to_date(self.date)}: {str(self.combat_type)}: "
            if self.attacker_victory:
                result += f"{attackers} succeeded in invasion of {loc} against {defenders}"
            else:
                result += f"{defenders} defended against invasion of {loc} against {attackers}"
        elif self.combat_type == CombatType.ships:
            result = f"{days_to_date(self.date)}: {str(self.combat_type)}: "
            if self.attacker_victory:
                result += f"{attackers} defeated {defenders} in the {loc}"
            else:
                result += f"{attackers} were defeated by {defenders} in the {loc}"
        else:
            result = f"{days_to_date(self.date)}: {str(self.combat_type)} {loc}: "
            if self.attacker_victory:
                result += f"{attackers} defeated {defenders}"
            else:
                result += f"{attackers} were defeated by {defenders}"

        return result


class CombatParticipant(Base):
    __tablename__ = 'combatparticipant'
    combat_participant_id = Column(Integer, primary_key=True)

    combat_id = Column(ForeignKey(Combat.combat_id), index=True)
    war_participant_id = Column(ForeignKey(WarParticipant.warparticipant_id), index=True)

    is_attacker = Column(Boolean)

    war_participant = relationship("WarParticipant", back_populates="combat_participation")
    combat = relationship("Combat")

    def __repr__(self):
        return f"CombatParticipant(combat_id={self.combat_id}, war_participant_id={self.war_participant_id}, is_attacker={self.is_attacker})"


class Leader(Base):
    __tablename__ = "leadertable"

    leader_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    country_id = Column(ForeignKey(Country.country_id))
    leader_id_in_game = Column(Integer, index=True)

    leader_name = Column(String(80))

    species_id = Column(ForeignKey(Species.species_id))
    leader_class = Column(Enum(LeaderClass))
    gender = Column(Enum(LeaderGender))
    leader_agenda = Column(Enum(LeaderAgenda))

    date_hired = Column(Integer)  # The date when this leader was first encountered
    date_born = Column(Integer)  # estimated birthday
    last_date = Column(Integer)  # estimated death / dismissal
    is_active = Column(Boolean, index=True)

    game = relationship("Game", back_populates="leaders")
    country = relationship("Country", back_populates="leaders")
    species = relationship("Species")
    historical_events = relationship("HistoricalEvent", back_populates="leader", cascade="all,delete,delete-orphan")
    achievements = relationship("LeaderAchievement", back_populates="leader", cascade="all,delete,delete-orphan")

    def __repr__(self):
        return f"Leader(leader_id_in_game={self.leader_id_in_game}, leader_name={self.leader_name}, leader_class={self.leader_class}, gender={self.gender}, leader_agenda={self.leader_agenda}, date_hired={days_to_date(self.date_hired)}, date_born={days_to_date(self.date_born)})"


class ColonizedPlanet(Base):
    __tablename__ = "colonizedplanettable"
    planet_id = Column(Integer, primary_key=True)

    planet_name = Column(String(50))
    planet_id_in_game = Column(Integer, index=True)
    system_id = Column(ForeignKey(System.system_id))
    colonization_completed = Column(Boolean)

    historical_events = relationship("HistoricalEvent", back_populates="planet", cascade="all,delete,delete-orphan")
    system = relationship("System")


class HistoricalEventDescription(Base):
    __tablename__ = "historicaleventdescriptiontable"
    historical_event_description_id = Column(Integer, primary_key=True)

    text = Column(String(80), index=True)


class HistoricalEvent(Base):
    """
    This class represents arbitrary historical events for the text ledger.
    The event_type specifies which event is encoded, depending on which
    certain columns may or may not be null.
    """
    __tablename__ = "historicaleventtable"
    historical_event_id = Column(Integer, primary_key=True)

    event_type = Column(Enum(HistoricalEventType), index=True)
    country_id = Column(ForeignKey(Country.country_id), index=True)

    # Any of the following columns may be undefined, depending on the event type.
    war_id = Column(ForeignKey(War.war_id), nullable=True)
    leader_id = Column(ForeignKey(Leader.leader_id), nullable=True, index=True)
    system_id = Column(ForeignKey(System.system_id), nullable=True)
    planet_id = Column(ForeignKey(ColonizedPlanet.planet_id), nullable=True, index=True)
    faction_id = Column(ForeignKey(PoliticalFaction.faction_id), nullable=True)
    description_id = Column(ForeignKey(HistoricalEventDescription.historical_event_description_id), nullable=True)

    start_date_days = Column(Integer, index=True)
    end_date_days = Column(Integer)

    war = relationship("War")
    country = relationship("Country", back_populates="historical_events")
    leader = relationship("Leader", back_populates="historical_events")
    system = relationship("System", back_populates="historical_events")
    planet = relationship("ColonizedPlanet", back_populates="historical_events")
    faction = relationship("PoliticalFaction", back_populates="historical_events")
    description = relationship("HistoricalEventDescription")

    def __str__(self):
        start_date = days_to_date(self.start_date_days)
        end_date = days_to_date(self.end_date_days)
        if self.event_type == HistoricalEventType.government_reform:
            old_gov = self.leader.country.get_government_for_date(self.start_date_days - 1)
            new_gov = self.leader.country.get_government_for_date(self.start_date_days)
            reform_dict = new_gov.get_reform_description_dict(old_gov)
            reform_lines = []
            for cat, reforms in reform_dict.items():
                reform_lines.append(f"{cat}: " + ", ".join(reforms))
            ref = ". ".join(reform_lines)
            text = f"{start_date}: Government reform: \n{ref}"
        else:
            text = f"{start_date} - {end_date}: {str(self.event_type)}"

        return text


class LeaderAchievement(Base):
    __tablename__ = "leaderachievementtable"

    leader_achievement_id = Column(Integer, primary_key=True)
    leader_id = Column(ForeignKey(Leader.leader_id))

    start_date_days = Column(Integer, index=True)
    end_date_days = Column(Integer, index=True)
    achievement_type = Column(Enum(LeaderAchievementType), index=True)
    achievement_description = Column(String(80))

    leader = relationship("Leader", back_populates="achievements")

    def __str__(self):
        start_date = days_to_date(self.start_date_days)
        end_date = days_to_date(self.end_date_days)
        if self.achievement_type == LeaderAchievementType.was_ruler:
            achievement_text = f'{start_date} - {end_date}: Ruled the {self.achievement_description} with "{self.leader.leader_agenda}" agenda'
        elif self.achievement_type == LeaderAchievementType.negotiated_peace_treaty:
            achievement_text = f"{end_date}: Negotiated peace in the {self.achievement_description}"
        elif self.achievement_type == LeaderAchievementType.passed_edict:
            name = game_info.convert_id_to_name(self.achievement_description)
            achievement_text = f'{start_date} - {end_date}: Issued "{name}" edict'
        elif self.achievement_type == LeaderAchievementType.embraced_tradition:
            tradition = game_info.convert_id_to_name(self.achievement_description, remove_prefix="tr")
            achievement_text = f'{end_date}: Adopted "{tradition}" tradition'
        elif self.achievement_type == LeaderAchievementType.achieved_ascension:
            perk = game_info.convert_id_to_name(self.achievement_description, remove_prefix="ap")
            achievement_text = f'{end_date}: "{perk}" ascension.'
        elif self.achievement_type == LeaderAchievementType.researched_technology:
            tech = game_info.convert_id_to_name(self.achievement_description, remove_prefix="tech")
            achievement_text = f'{end_date}: Researched "{tech}" technology'
        elif self.achievement_type == LeaderAchievementType.was_faction_leader:
            achievement_text = f'{start_date} - {end_date}: Leader of the "{self.achievement_description}" faction'
        elif self.achievement_type == LeaderAchievementType.governed_sector:
            achievement_text = f'{start_date} - {end_date}: Governed "{self.achievement_description}" sector.'
        elif self.achievement_type == LeaderAchievementType.built_megastructure:
            achievement_text = f'{start_date}: Finished construction of "{self.achievement_description}".'
        elif self.achievement_type == LeaderAchievementType.colonized_planet:
            achievement_text = f'{start_date} - {end_date}: Colonization of planet "{self.achievement_description}".'
        elif self.achievement_type == LeaderAchievementType.reformed_government:
            old_gov = self.leader.country.get_government_for_date(self.start_date_days - 1)
            new_gov = self.leader.country.get_government_for_date(self.start_date_days)
            reform_dict = new_gov.get_reform_description_dict(old_gov)
            reform_lines = []
            for cat, reforms in reform_dict.items():
                reform_lines.append(f"{cat}: " + ", ".join(reforms))
            ref = ". ".join(reform_lines)
            achievement_text = f"{start_date}: Government reform: \n{ref}"
        else:
            achievement_text = f"{start_date} - {end_date}: {self.achievement_description}"
        return achievement_text
