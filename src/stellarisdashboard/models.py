import enum
import pathlib

import sqlalchemy
from sqlalchemy import Column, Integer, String, ForeignKey, Float, Boolean, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

from stellarisdashboard import config

DB_FILE = config.CONFIG.base_output_path / "timeline.db"
engine = sqlalchemy.create_engine(f'sqlite:///{DB_FILE}', echo=False)
SessionFactory = sessionmaker(bind=engine)

Base = declarative_base()


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
        return self in {Attitude.friendly, Attitude.loyal}

    def reveals_technology_info(self):
        return self.reveals_military_info() or (self in {Attitude.protective, Attitude.disloyal})

    def reveals_economy_info(self):
        return self.reveals_technology_info() or (self in {Attitude.cordial})

    def reveals_demographic_info(self):
        return self.reveals_economy_info() or (self in {Attitude.neutral})

    def is_known(self):
        return self != Attitude.unknown


@enum.unique
class WarEventType(enum.Enum):
    ships = 0
    # TODO what other kinds are there?


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
    session = SessionFactory()
    try:
        game = session.query(Game).all()
        for g in game:
            if g.game_name.startswith(game_name_prefix):
                yield g.game_name
    finally:
        session.close()


def get_gamestates_since(game_name, date):
    session = SessionFactory()
    try:
        game = session.query(Game).filter(Game.game_name == game_name).one()
        for gs in session.query(GameState).filter(GameState.game == game, GameState.date > date).order_by(GameState.date).all():
            yield gs
    finally:
        session.close()


class Game(Base):
    """Root object representing an entire game."""
    __tablename__ = 'gametable'
    game_id = Column(Integer, primary_key=True)
    game_name = Column(String(50))
    player_country_name = Column(String(50))

    countries = relationship("Country", back_populates="game", cascade="all,delete,delete-orphan")
    species = relationship("Species", back_populates="game", cascade="all,delete,delete-orphan")
    game_states = relationship("GameState", back_populates="game", cascade="all,delete,delete-orphan")
    wars = relationship("War", back_populates="game", cascade="all,delete,delete-orphan")

    def __repr__(self):
        return f"Game(game_id={self.game_id}, game_name={self.game_name})"


class GameState(Base):
    """Represents the state of the game at a specific moment."""
    __tablename__ = 'gamestatetable'
    gamestate_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    date = Column(Integer, index=True)  # Days since 2200.1.1

    mineral_income_base = Column(Float, default=0.0)
    mineral_income_production = Column(Float, default=0.0)
    mineral_income_trade = Column(Float, default=0.0)
    mineral_income_mission = Column(Float, default=0.0)
    mineral_spending_pop = Column(Float, default=0.0)
    mineral_spending_ship = Column(Float, default=0.0)
    mineral_spending_trade = Column(Float, default=0.0)
    mineral_spending_mission = Column(Float, default=0.0)

    food_income_base = Column(Float, default=0.0)
    food_income_production = Column(Float, default=0.0)
    food_income_trade = Column(Float, default=0.0)
    food_spending = Column(Float, default=0.0)
    food_spending_trade = Column(Float, default=0.0)

    energy_income_base = Column(Float, default=0.0)
    energy_income_production = Column(Float, default=0.0)
    energy_income_trade = Column(Float, default=0.0)
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
    country_data = relationship("CountryData", back_populates="country", cascade="all,delete,delete-orphan")
    political_factions = relationship("PoliticalFaction", back_populates="country", cascade="all,delete,delete-orphan")
    war_participation = relationship("WarParticipant", back_populates="country", cascade="all,delete,delete-orphan")


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

    has_research_agreement_with_player = Column(Boolean)
    has_sensor_link_with_player = Column(Boolean)
    attitude_towards_player = Column(Enum(Attitude))

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
    game_id = Column(ForeignKey(Game.game_id), index=True, primary_key=True)

    start_date_days = Column(Integer, index=True)
    name = Column(String(100))

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

    war = relationship("War", back_populates="participants")
    country = relationship("Country", back_populates="war_participation")
    warevents = relationship("WarEvent", back_populates="war_participant")

    def __repr__(self):
        return f"WarParticipant(war_id={self.war_id}, country_id={self.country_id}, is_attacker={self.is_attacker})"


class WarEvent(Base):
    __tablename__ = 'wareventtable'
    warevent_id = Column(Integer, primary_key=True)

    date = Column(Integer, index=True)
    war_participant_id = Column(ForeignKey(WarParticipant.warparticipant_id), index=True)
    war_exhaustion = Column(Float)

    war_participant = relationship("WarParticipant", back_populates="warevents")

    def __repr__(self):
        return f"WarEvent(warevent_id={warevent_id}, war_participant_id={self.war_participant_id}, date={self.date}, war_exhaustion={self.war_exhaustion})"


Base.metadata.create_all(engine)
