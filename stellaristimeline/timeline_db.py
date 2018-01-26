import logging

from stellaristimeline import models


class TimelineExtractor:
    def __init__(self):
        self.gamestate_dict = None
        self.game = None
        self._session = None

    def process_gamestate(self, game_name, gamestate_dict):
        self.gamestate_dict = gamestate_dict
        self._session = models.SessionFactory()
        try:
            self.game = self._session.query(models.Game).filter(models.Game.game_name == game_name).first()
            if self.game is None:
                logging.info(f"Adding new game {game_name} to database.")
                self.game = models.Game(game_name=game_name)
                self._session.add(self.game)
            self._extract_galaxy_data()
            self._session.commit()
        except Exception as e:
            self._session.rollback()
            raise e
        finally:
            self._session.close()

        self.gamestate_dict = None

    def _extract_galaxy_data(self):
        date = self.gamestate_dict["date"]

        gs = models.GameState(game=self.game, date=date)
        self._session.add(gs)

        for country_id, country_data in self.gamestate_dict["country"].items():
            if not isinstance(country_data, dict):
                continue  # can be "none", apparently
            if country_data["type"] != "default":
                continue  # Enclaves, Leviathans, etc ....

            country_state = models.CountryState(
                country_name=country_data["name"],
                game_state=gs,
                military_power=country_data["military_power"],
                fleet_size=country_data["fleet_size"],
                tech_progress=len(country_data["tech_status"]["technology"]),
                exploration_progress=len(country_data["surveyed"]),
                owned_planets=len(country_data["owned_planets"]),
            )
            self._session.add(country_state)

            demographics = {}
            pop_data = self.gamestate_dict["pop"]
            for planet_id in country_data["owned_planets"]:
                planet_data = self.gamestate_dict["planet"][planet_id]
                for pop_id in planet_data.get("pop", []):
                    if pop_id not in pop_data:
                        logging.warning(f"Reference to non-existing pop with id {pop_id} on planet {planet_id}")
                    pop_species_index = pop_data[pop_id]["species_index"]
                    if pop_species_index not in demographics:
                        demographics[pop_species_index] = 0
                    if pop_data[pop_id]["growth_state"] == 1:
                        demographics[pop_species_index] += 1

            for pop_species_index, pop_count in demographics.items():
                species_name = self.gamestate_dict["species"][pop_species_index]["name"]
                pop_count = models.PopCount(
                    country_state=country_state,
                    species_name=species_name,
                    pop_count=pop_count,
                )
                self._session.add(pop_count)


if __name__ == '__main__':
    from stellaristimeline.save_parser import SaveFileParser

    te = TimelineExtractor()

    p = SaveFileParser("/home/elias/Documents/projects/stellaris-timeline/saves/lokkenmechanists_1256936305/autosave_2200.11.01.sav")
    te.process_gamestate("lokkenmechanists_1256936305", p.parse_save())

    p = SaveFileParser("/home/elias/Documents/projects/stellaris-timeline/saves/lokkenmechanists_1256936305/autosave_2200.08.01.sav")
    te.process_gamestate("lokkenmechanists_1256936305", p.parse_save())

    p = SaveFileParser("/home/elias/Documents/projects/stellaris-timeline/saves/lokkenmechanists_1256936305/2200.01.01.sav")
    te.process_gamestate("lokkenmechanists_1256936305", p.parse_save())

    session = models.SessionFactory()
    print("OUTPUT")
    for g in session.query(models.Game).all():
        for gs in g.game_states:
            print(f"{gs}")
            for cs in sorted(gs.country_states, key=lambda x: x.country_name):
                print(f"  {cs}")
                for pc in sorted(cs.pop_counts, key=lambda x: x.species_name):
                    print(f"    {pc.species_name}  {pc.pop_count}")
