import pathlib
import sqlalchemy
from sqlalchemy import Column, Integer, String, ForeignKey, Float, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

BASE_DIR = pathlib.Path.home() / ".local/share/stellaristimeline/"
# engine = sqlalchemy.create_engine(f'sqlite:///:memory:', echo=False)
engine = sqlalchemy.create_engine(f'sqlite:///foo.db', echo=False)
SessionFactory = sessionmaker(bind=engine)

Base = declarative_base()


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
    country_name = Column(String(20), index=True)
    military_power = Column(Float)
    fleet_size = Column(Float)
    tech_progress = Column(Integer)
    exploration_progress = Column(Integer)
    owned_planets = Column(Integer)
    is_player = Column(Boolean)

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
