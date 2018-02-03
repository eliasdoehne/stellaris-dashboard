import pathlib
import sqlalchemy
from sqlalchemy import Column, Integer, String, ForeignKey, Float, Boolean, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import enum

BASE_DIR = pathlib.Path.home() / ".local/share/stellaristimeline/"
engine = sqlalchemy.create_engine(f'sqlite:///foo.db', echo=False)
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

    def reveals_geographic_info(self):
        return self.reveals_demographic_info() or (self in {Attitude.wary})


class Game(Base):
    __tablename__ = 'gametable'
    game_id = Column(Integer, primary_key=True)
    game_name = Column(String(50))

    game_states = relationship("GameState", back_populates="game", order_by=lambda: GameState.date)

    def __repr__(self):
        return f"Game(game_id={self.game_id}, game_name=\"{self.game_name}\")"


class GameState(Base):
    __tablename__ = 'gamestatetable'
    gamestate_id = Column(Integer, primary_key=True)
    game_id = Column(ForeignKey(Game.game_id))
    date = Column(Integer, index=True)  # Days since 2200.1.1

    game = relationship("Game", back_populates="game_states")
    country_states = relationship("CountryState", back_populates="game_state")

    def __repr__(self):
        return f"GameState(gamestate_id={self.gamestate_id}, date={self.date}, game_id={self.game_id})"


class CountryState(Base):
    __tablename__ = 'countrystatetable'
    country_state_id = Column(Integer, primary_key=True)
    gamestate_id = Column(ForeignKey(GameState.gamestate_id))

    country_name = Column(String)
    military_power = Column(Float)
    fleet_size = Column(Float)
    tech_progress = Column(Integer)
    exploration_progress = Column(Integer)
    owned_planets = Column(Integer)

    is_player = Column(Boolean)
    has_research_agreement_with_player = Column(Boolean)
    has_sensor_link_with_player = Column(Boolean)
    attitude_towards_player = Column(Enum(Attitude))

    game_state = relationship("GameState", back_populates="country_states")
    pop_counts = relationship("PopCount", back_populates="country_state")

    def __repr__(self):
        return f"CountryState(country_state_id={self.country_state_id}, country_name=\"{self.country_name}\", game_state={self.gamestate_id}, military_power={self.military_power}, fleet_size={self.fleet_size}, tech_progress={self.tech_progress}, exploration_progress={self.exploration_progress}, owned_planets={self.owned_planets})"


class PopCount(Base):
    __tablename__ = 'popcounttable'
    pc_id = Column(Integer, primary_key=True)
    country_state_id = Column(ForeignKey(CountryState.country_state_id), index=True)
    species_name = Column(String(25))
    pop_count = Column(Integer)

    country_state = relationship("CountryState", back_populates="pop_counts")

    def __repr__(self):
        return f"PopCount(pc_id={self.pc_id}, country_state_id={self.country_state_id}, species_name =\"{self.species_name}\", pop_count={self.pop_count})"


Base.metadata.create_all(engine)
