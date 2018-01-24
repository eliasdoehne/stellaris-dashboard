import logging
from collections import namedtuple, defaultdict

COLONIZABLE_PLANET_CLASSES = {
    "pc_arid", "pc_continental", "pc_alpine",
    "pc_desert", "pc_ocean", "pc_arctic",
    "pc_savannah", "pc_tropical", "pc_tundra",
    "pc_gaia", "pc_nuked", "pc_machine",
    "pc_ringworld_habitable", "pc_habitat",
}

COLD_CLIMATE = "cold"
TEMPERATE_CLIMATE = "temperate"
DRY_CLIMATE = "dry"
OTHER_CLIMATE = "other"
GAIA_CLIMATE = "gaia"
CLIMATE_CLASSIFICATION = {
    "pc_alpine": COLD_CLIMATE,
    "pc_tundra": COLD_CLIMATE,
    "pc_arctic": COLD_CLIMATE,

    "pc_continental": TEMPERATE_CLIMATE,
    "pc_ocean": TEMPERATE_CLIMATE,
    "pc_tropical": TEMPERATE_CLIMATE,

    "pc_arid": DRY_CLIMATE,
    "pc_desert": DRY_CLIMATE,
    "pc_savannah": DRY_CLIMATE,

    "pc_nuked": OTHER_CLIMATE,
    "pc_machine": OTHER_CLIMATE,

    "pc_gaia": GAIA_CLIMATE,
    "pc_ringworld_habitable": GAIA_CLIMATE,
    "pc_habitat": GAIA_CLIMATE,
}

PLANET_CLIMATES = [COLD_CLIMATE, TEMPERATE_CLIMATE, DRY_CLIMATE, GAIA_CLIMATE, OTHER_CLIMATE]


class StellarisDate(namedtuple("Date", ["year", "month", "day"])):
    def __sub__(self, other):
        """
        Get the difference between two dates in days.

        :param other:
        :return:
        """
        assert isinstance(other, StellarisDate)
        return self.in_days() - other.in_days()

    def in_days(self):
        return self.day + 30 * (self.month + 12 * self.year)

    @classmethod
    def from_string(cls, datestring):
        y, m, d = datestring.split(".")
        return cls(int(y), int(m), int(d))

    def __repr__(self):
        return f"{self.year}.{self.month}.{self.day}"


class GameStateInfo:
    def __init__(self):
        self.date = None
        self.game_name = None
        self.player_country = None
        self.species_list = None
        self.galaxy_data = None
        self.country_data = None
        self.exploration_data = None
        self.owned_planets = None
        self.tech_progress = None
        self.demographics_data = None
        self._game_state = None

    def initialize(self, gamestate):
        self._game_state = gamestate
        self.date = StellarisDate.from_string(gamestate["date"])
        self.game_name = gamestate["name"]
        self.player_country = gamestate["player"][0]["country"]
        self._extract_galaxy_data()
        self._extract_empire_info()
        self._game_state = None

    def _extract_galaxy_data(self):
        """
        Extract some static information about the galaxy that should not change (too much) during
        the playthrough.

        :return:
        """
        self.galaxy_data = {
            "planet_class_distribution": {},
            "planet_tiles_distribution": {},
        }

        for planet_id, planet_data in self._game_state["planet"].items():
            planet_class = planet_data["planet_class"]
            if planet_class not in self.galaxy_data["planet_class_distribution"]:
                self.galaxy_data["planet_class_distribution"][planet_class] = 0
            self.galaxy_data["planet_class_distribution"][planet_class] += 1

            if planet_class in COLONIZABLE_PLANET_CLASSES:
                if planet_class not in self.galaxy_data["planet_tiles_distribution"]:
                    self.galaxy_data["planet_tiles_distribution"][planet_class] = 0
                for tile in planet_data["tiles"].values():
                    if tile.get("active") == "yes":
                        self.galaxy_data["planet_tiles_distribution"][planet_class] += 1

    def _extract_empire_info(self):
        """
        Extract information about the player empire's demographics

        :return:
        """
        self.country_data = {}
        self.demographics_data = {}
        self.tech_progress = {}
        self.owned_planets = {}
        self.exploration_data = {}
        self.species_list = self._game_state["species"]
        pop_data = self._game_state["pop"]
        for country_id, country_data in self._game_state["country"].items():
            if not isinstance(country_data, dict):
                continue  # can be "none", apparently
            if country_data["type"] != "default":
                continue  # Enclaves, Leviathans, etc ....

            self.country_data[country_id] = {
                "name": country_data["name"],
                "military_power": country_data["military_power"],
                "fleet_size": country_data["fleet_size"],
                "tech_progress": country_data["tech_status"]["technology"],
                "exploration_progress": len(country_data["surveyed"]),
            }
            if not country_data["budget"]:
                self.country_data[country_id]["budget"] = defaultdict(None)
            else:
                self.country_data[country_id]["budget"] = country_data["budget"]["last_month"]
            self.demographics_data[country_id] = {}
            self.owned_planets[country_id] = 0
            for planet_id in country_data["owned_planets"]:
                self.owned_planets[country_id] += 1
                planet_data = self._game_state["planet"][planet_id]
                for pop_id in planet_data.get("pop", []):
                    if pop_id not in pop_data:
                        logging.warning(f"Reference to non-existing pop with id {pop_id} on planet {planet_id}")
                    pop_species_index = pop_data[pop_id]["species_index"]
                    if pop_species_index not in self.demographics_data[country_id]:
                        self.demographics_data[country_id][pop_species_index] = 0
                    self.demographics_data[country_id][pop_species_index] += 1

    def __str__(self):
        return f"{self.game_name} - {self.date}"


class Timeline:
    def __init__(self):
        self.game_name = None
        self.time_line = {}

    def add_data(self, gamestateinfo):
        if self.game_name is None:
            self.game_name = gamestateinfo.game_name
        else:
            if self.game_name != gamestateinfo.game_name:
                logging.error(f"Ignoring game state for {gamestateinfo.game_name}, expected {self.game_name}")

        if gamestateinfo.date in self.time_line:
            logging.error(f"Ignoring duplicate entry for {gamestateinfo.date}")
        self.time_line[gamestateinfo.date] = gamestateinfo
