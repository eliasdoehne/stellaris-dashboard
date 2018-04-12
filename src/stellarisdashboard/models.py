import contextlib
import enum
import pathlib
import threading
import logging

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


def get_known_games():
    return [fname.stem for fname in DB_PATH.glob("*.db")]


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


def get_game_names_matching(game_name_prefix):
    for fname in DB_PATH.glob(f"{game_name_prefix}*"):
        yield fname.stem


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

    def get_owner_at(self, time_in_days):
        # could be slow, but fine for now
        for ownership in self.ownership:
            if ownership.start_date_days <= time_in_days <= ownership.end_date_days:
                return ownership.country
        return None

    def __str__(self):
        return f'System "{self.original_name}" @ {self.coordinate_x}, {self.coordinate_y}'


class SystemOwnership(Base):
    __tablename__ = 'systemownershiptable'
    system_ownership_id = Column(Integer, primary_key=True)

    system_id = Column(ForeignKey(System.system_id), index=True)
    owner_country_id = Column(ForeignKey("countrytable.country_id"), index=True)

    start_date_days = Column(Integer)
    end_date_days = Column(Integer)

    system = relationship("System", back_populates="ownership")
    country = relationship("Country", back_populates="owned_systems")


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

    game = relationship("Game", back_populates="countries")
    governments = relationship("Government", back_populates="country", cascade="all,delete,delete-orphan")
    country_data = relationship("CountryData", back_populates="country", cascade="all,delete,delete-orphan")
    political_factions = relationship("PoliticalFaction", back_populates="country", cascade="all,delete,delete-orphan")
    war_participation = relationship("WarParticipant", back_populates="country", cascade="all,delete,delete-orphan")
    owned_systems = relationship(SystemOwnership, back_populates="country", cascade="all,delete,delete-orphan")


class Government(Base):
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


class CountryData(Base):
    """
    Contains the state of the country at a specific moment, specifically basic data about economy, science and military.
    Children objects contain data about demographics and political factions.
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
    victories = relationship("CombatVictory", back_populates="war_participant")

    def __repr__(self):
        return f"WarParticipant(war_id={self.war_id}, country_id={self.country_id}, is_attacker={self.is_attacker})"


class CombatVictory(Base):
    __tablename__ = 'combatvictorytable'
    combat_victory_id = Column(Integer, primary_key=True)

    date = Column(Integer, index=True)
    war_participant_id = Column(ForeignKey(WarParticipant.warparticipant_id), index=True)
    inflicted_war_exhaustion = Column(Float)
    combat_type = Column(Enum(CombatType))

    attacker_victory = Column(Boolean)

    system = Column(String(80))
    planet = Column(String(80))

    war_participant = relationship("WarParticipant", back_populates="victories")

    def __repr__(self):
        return f"CombatVictory(combat_victory_id={self.combat_victory_id}, war_participant_id={self.war_participant_id}, date={self.date}, inflicted_war_exhaustion={self.inflicted_war_exhaustion}, combat_type={self.combat_type})"


class Leader(Base):
    __tablename__ = "leadertable"

    leader_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))

    leader_id_in_game = Column(Integer)

    leader_name = Column(String(80))

    species_id = Column(ForeignKey(Species.species_id))
    leader_class = Column(Enum(LeaderClass))
    gender = Column(Enum(LeaderGender))
    leader_agenda = Column(Enum(LeaderAgenda))

    date_hired = Column(Integer)  # The date when this leader was first encountered
    date_born = Column(Integer)  # estimated birthday
    last_date = Column(Integer)  # estimated death / dismissal

    game = relationship("Game", back_populates="leaders")
    species = relationship("Species")
    achievements = relationship("LeaderAchievement", back_populates="leader", cascade="all,delete,delete-orphan")

    def __repr__(self):
        return f"Leader(leader_id_in_game={self.leader_id_in_game}, leader_name={self.leader_name}, leader_class={self.leader_class}, gender={self.gender}, leader_agenda={self.leader_agenda}, date_hired={days_to_date(self.date_hired)}, date_born={days_to_date(self.date_born)})"


class LeaderAchievement(Base):
    __tablename__ = "leaderachievementtable"

    leader_achievement_id = Column(Integer, primary_key=True)
    leader_id = Column(ForeignKey(Leader.leader_id))

    start_date_days = Column(Integer, index=True)
    end_date_days = Column(Integer, index=True)
    achievement_type = Column(Enum(LeaderAchievementType))
    achievement_description = Column(String(80))

    leader = relationship("Leader", back_populates="achievements")

    def __str__(self):
        start_date = days_to_date(self.start_date_days)
        end_date = days_to_date(self.end_date_days)
        if self.achievement_type == LeaderAchievementType.was_ruler:
            achievement_text = f"{start_date} - {end_date}: Ruled the {self.achievement_description} with agenda \"{self.leader.leader_agenda}\""
        elif self.achievement_type == LeaderAchievementType.negotiated_peace_treaty:
            achievement_text = f"{end_date}: Negotiated peace in the {self.achievement_description}"
        elif self.achievement_type == LeaderAchievementType.passed_edict:
            achievement_text = f'{end_date}: Passed edict "{self.achievement_description}"'
        elif self.achievement_type == LeaderAchievementType.embraced_tradition:
            tradition = game_info.convert_id_to_name(self.achievement_description, remove_prefix="tr")
            achievement_text = f"{end_date}: Embraced tradition \"{tradition}\""
        elif self.achievement_type == LeaderAchievementType.achieved_ascension:
            perk = game_info.convert_id_to_name(self.achievement_description, remove_prefix="ap")
            achievement_text = f"{end_date}: Ascension: {perk}"
        elif self.achievement_type == LeaderAchievementType.researched_technology:
            perk = game_info.convert_id_to_name(self.achievement_description, remove_prefix="tech")
            achievement_text = f"{end_date}: Researched \"{perk}\""
        elif self.achievement_type == LeaderAchievementType.was_faction_leader:
            achievement_text = f"{start_date} - {end_date}: Leader of the \"{self.achievement_description}\" faction."
        else:
            achievement_text = f"{start_date} - {end_date}: {self.achievement_description}"
        return achievement_text
