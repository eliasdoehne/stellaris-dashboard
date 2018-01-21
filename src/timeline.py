from collections import namedtuple


class StellarisDate(namedtuple("Date", ["year", "month", "day"])):

    def __sub__(self, other):
        assert isinstance(other, StellarisDate)
        return self.day + 30 * (self.month + 12 * self.year)

    @classmethod
    def from_string(cls, datestring):
        y, m, d = datestring.split(".")
        return cls(y, m, d)


class Timeline:
    def __init__(self):
        self.save_times = []
        self.empire_data = {}
        self.galaxy_data = {}
        self._gamestate = None
        self.player_country = None
        self.species_list = None

    def add_data(self, gamestate):
        self._gamestate = gamestate
        save_date = StellarisDate.from_string(gamestate["date"])
        self.save_times.append(
            save_date
        )
        self.player_country = gamestate["player"][0]["country"]

        self.species_list = []
        self.empire_data[save_date] = {
            "empire_demographics": self._extract_empire_demographics(),
        }

        if not self.galaxy_data:
            self.galaxy_data = self._extract_galaxy_data()
        self._gamestate = None

    def _extract_empire_demographics(self):
        """
        Extract information about the player empire's demographics

        :return:
        """
        empire_demographics = {
            "owned_planets": 0,
            "pops_per_species": {},
        }
        self.species_list = self._gamestate["species"]
        pop_data = self._gamestate["pop"]
        for planet_id in self._gamestate["country"][self.player_country]["owned_planets"]:
            empire_demographics["owned_planets"] += 1
            planet_data = self._gamestate["planet"][planet_id]

            for tile_id, tile_data in planet_data["tiles"].items():
                pop_id = tile_data.get("pop")
                if pop_id:
                    pop_species_index = pop_data[pop_id]["species_index"]
                    if pop_species_index not in empire_demographics["pops_per_species"]:
                        empire_demographics["pops_per_species"][pop_species_index] = 0
                    empire_demographics["pops_per_species"][pop_species_index] += 1
        return empire_demographics

    def _extract_galaxy_data(self):
        """
        Extract some static information about the galaxy that should not change (too much) during
        the playthrough.

        :return:
        """
        galaxy_data = {
            "planet_type_distribution": {},
            "planet_tiles_distribution": {},
        }

        colonizable_planet_classes = {
            "pc_arid", "pc_continental", "pc_alpine",
            "pc_desert", "pc_ocean", "pc_arctic",
            "pc_savannah", "pc_tropical", "pc_tundra",
            "pc_gaia", "pc_nuked", "pc_machine",
            "pc_ringworld_habitable", "pc_habitat",
        }

        for planet_id, planet_data in self._gamestate["planet"].items():
            planet_class = planet_data["planet_class"]
            if planet_class not in galaxy_data["planet_type_distribution"]:
                galaxy_data["planet_type_distribution"][planet_class] = 0
            galaxy_data["planet_type_distribution"][planet_class] += 1

            if planet_class in colonizable_planet_classes:
                if planet_class not in galaxy_data["planet_tiles_distribution"]:
                    galaxy_data["planet_tiles_distribution"][planet_class] = 0
                for tile in planet_data["tiles"].values():
                    if tile.get("active") == "yes":
                        galaxy_data["planet_tiles_distribution"][planet_class] += 1
        return galaxy_data
