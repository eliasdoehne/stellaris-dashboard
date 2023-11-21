import json
import re
import logging

logger = logging.getLogger(__name__)
# Regex explanation: https://regex101.com/r/l76XGd/1
loc_re = re.compile(r'\s*(?P<key>\S+?):\d*\s*"(?P<value>.*)"\s*(#.*)?')

class NameRenderer:
    default_name = "Unknown name"

    def __init__(self, localization_files):
        self.localization_files = localization_files
        self.name_mapping = None

    def load_name_mapping(self):
        """
        Load a mapping of all name keys to their corresponding templates / localized values.

        Localization files can be passed in, by default the dashboard tries to locate them from
        """
        self.name_mapping = {"global_event_country": "Global event country"}
        for p in self.localization_files:
            # manually parse yaml, yaml.safe_load doesnt seem to work
            with open(p, "rt", encoding="utf-8") as f:
                for line in f:
                    try:
                        re_match = loc_re.match(line)
                        if re_match:
                            self.name_mapping[ re_match.group('key') ] = re_match.group('value')
                        else:
                            if not line.startswith( ('#', "  ", " #", "  #", "   #", "\ufeffl_english:", "l_english:", "\n", " \n" ) ):
                                # This error prints the offending line as numbers because not only did we encounter whitespace, we encountered the Zero Width No-Break Space (BOM)
                                logger.debug(f"Unexpected unmatched localisation line found. Characters (as integers) follow: {[ord(x) for x in line]}")
                    except Exception as e:
                        logger.warning(f"Caught exception reading localisation files: {e}")
        # Add missing format that is similar to but not the same as adj_format in practice
        if "%ADJECTIVE%" not in self.name_mapping:
          self.name_mapping["%ADJECTIVE%"] = "$adjective$ $1$"
        # Alternate format with no template (meant to be concatenated?). Incomplete solution.
#        if "%ADJ%" not in self.name_mapping:
#          self.name_mapping["%ADJ%"] = "$1$"
        if "%LEADER_1%" not in self.name_mapping:
          self.name_mapping["%LEADER_1%"] = "$1$ $2$"
        if "%LEADER_2%" not in self.name_mapping:
          self.name_mapping["%LEADER_2%"] = "$1$ $2$"

    def render_from_json(self, name_json: str):
        try:
            json_dict = json.loads(name_json)
        except (json.JSONDecodeError, TypeError):
            return str(name_json)
        rendered = self.render_from_dict(json_dict)
        if rendered == self.default_name:
            logger.warning(
                "Failed to resolve a name, please check if you configured localization files."
            )
            logger.warning(f"Instructions can be found in README.md")
            logger.warning(f"Failed name: {name_json!r}")
        return rendered

    def render_from_dict(self, name_dict: dict) -> str:
        if not isinstance(name_dict, dict):
            logger.warning(f"Expected name template dictionary, received {name_dict}")
            return str(name_dict)

        key = name_dict.get("key", self.default_name)
        if name_dict.get("literal") == "yes":
            return key

        render_template = self.name_mapping.get(key, key)
        # The %ADJ% template is odd. See GitHub #90
        if render_template == "%ADJ%":
            render_template = "$1$"
            if "variables" in name_dict and "value" in name_dict["variables"][0] and "key" in name_dict["variables"][0]["value"] and "$1$" not in self.name_mapping.get(name_dict["variables"][0]["value"]["key"], ""):
                name_dict["variables"][0]["value"]["key"] += " $1$"

        substitution_values = []
        if "value" in name_dict:
            return self.render_from_dict(name_dict["value"])

        for var in name_dict.get("variables", []):
            if "key" not in var or "value" not in var:
                continue
            var_key = var.get("key")
            substitution_values.append((var_key, self.render_from_dict(var["value"])))

        # try all combinations of escaping identifiers to substitute the variables
        for subst_key, subst_value in substitution_values:
            for lparen, rparen in [
                ("<", ">"),
                ("[", "]"),
                ("$", "$"),
                ("%", "%"),
            ]:
                render_template = render_template.replace(
                    f"{lparen}{subst_key}{rparen}", subst_value
                )
        return render_template


global_renderer: NameRenderer = None


def render_name(json_str: str):
    global global_renderer
    if global_renderer is None:
        from stellarisdashboard import config

        global_renderer = NameRenderer(config.CONFIG.localization_files)
        global_renderer.load_name_mapping()
    return global_renderer.render_from_json(json_str)


COLONIZABLE_PLANET_CLASSES_PLANETS = {
    "pc_desert",
    "pc_arid",
    "pc_savannah",
    "pc_tropical",
    "pc_continental",
    "pc_ocean",
    "pc_tundra",
    "pc_arctic",
    "pc_alpine",
    "pc_gaia",
    "pc_nuked",
    "pc_machine",
}
COLONIZABLE_PLANET_CLASSES_MEGA_STRUCTURES = {
    "pc_ringworld_habitable",
    "pc_habitat",
}

# Planet classes for the planetary diversity mod
# (see https://steamcommunity.com/workshop/filedetails/discussion/1466534202/3397295779078104093/)
COLONIZABLE_PLANET_CLASSES_PD_PLANETS = {
    "pc_antarctic",
    "pc_deadcity",
    "pc_retinal",
    "pc_irradiated_terrestrial",
    "pc_lush",
    "pc_geocrystalline",
    "pc_marginal",
    "pc_irradiated_marginal",
    "pc_marginal_cold",
    "pc_crystal",
    "pc_floating",
    "pc_graveyard",
    "pc_mushroom",
    "pc_city",
    "pc_archive",
    "pc_biolumen",
    "pc_technoorganic",
    "pc_tidallylocked",
    "pc_glacial",
    "pc_frozen_desert",
    "pc_steppe",
    "pc_hadesert",
    "pc_boreal",
    "pc_sandsea",
    "pc_subarctic",
    "pc_geothermal",
    "pc_cascadian",
    "pc_swamp",
    "pc_mangrove",
    "pc_desertislands",
    "pc_mesa",
    "pc_oasis",
    "pc_hajungle",
    "pc_methane",
    "pc_ammonia",
}
COLONIZABLE_PLANET_CLASSES = (
    COLONIZABLE_PLANET_CLASSES_PLANETS
    | COLONIZABLE_PLANET_CLASSES_MEGA_STRUCTURES
    | COLONIZABLE_PLANET_CLASSES_PD_PLANETS
)

DESTROYED_BY_WEAPONS_PLANET_CLASSES = {
    "pc_shattered",
    "pc_shielded",
    "pc_ringworld_shielded",
    "pc_habitat_shielded",
    "pc_ringworld_habitable_damaged",
}
DESTROYED_BY_EVENTS_AND_CRISES_PLANET_CLASSES = {
    "pc_egg_cracked",
    "pc_shrouded",
    "pc_ai",
    "pc_infested",
    "pc_gray_goo",
}
DESTROYED_PLANET_CLASSES = (
    DESTROYED_BY_WEAPONS_PLANET_CLASSES | DESTROYED_BY_EVENTS_AND_CRISES_PLANET_CLASSES
)


def is_destroyed_planet(planet_class):
    return planet_class in DESTROYED_PLANET_CLASSES


def is_colonizable_planet(planet_class):
    return planet_class in COLONIZABLE_PLANET_CLASSES


def is_colonizable_megastructure(planet_class):
    return planet_class in COLONIZABLE_PLANET_CLASSES_MEGA_STRUCTURES


LOWERCASE_WORDS = {"the", "in", "of", "for", "is", "over", "under"}
WORD_REPLACEMENT = {
    "Ai": "AI",
    "Ftl": "FTL",
    "Tb": "Tile Blocker",
}


def convert_id_to_name(object_id: str, remove_prefix="") -> str:
    words = [word for word in object_id.split("_") if word != remove_prefix]
    words = [
        word.capitalize() if word not in LOWERCASE_WORDS else word for word in words
    ]
    words = [WORD_REPLACEMENT.get(word, word) for word in words]
    return " ".join(words)
