import json
import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)
# Regex explanation: https://regex101.com/r/qc0QhS/1
loc_re = re.compile(r'\s*(?P<key>\S+?):\d*\s*"(?P<value>.*)"\s*(#.*)?')
var_re = re.compile(r"\$(?P<key>\S+)\$")


class NameRenderer:
    default_name = "Unknown name"

    def __init__(self, localization_files):
        self.localization_files = localization_files
        self.name_mapping: dict[str, str] = {}
        self.adjective_templates: dict[
            str, str
        ] = {}  # map noun suffixes to adjective suffix

    def load_name_mapping(self):
        """
        Load a mapping of all name keys to their corresponding templates / localized values.

        Localization files can be passed in, by default the dashboard tries to locate them from
        """
        self.name_mapping = {"global_event_country": "Global event country"}
        self.adjective_templates = {}
        # manually parse the yaml files, yaml.safe_load doesn't seem to work
        ignored_prefixes = ("#", "\ufeffl_english:", "l_english:")
        for line in self._iter_localization_lines():
            try:
                re_match = loc_re.match(line)
                if re_match:
                    key = re_match.group("key")
                    value = re_match.group("value")
                    if key.startswith("adj_NN"):
                        key = key.removeprefix("adj_NN")
                        value = value.split("|")[0]
                        key = key.lstrip("*")
                        self.adjective_templates[key] = value
                    else:
                        self.name_mapping[key] = value
                else:
                    if not line.startswith(ignored_prefixes):
                        # This error prints the offending line as numbers because not only did we encounter whitespace,
                        # we encountered the Zero Width No-Break Space (BOM)
                        logger.debug(
                            f"Unexpected unmatched localisation line found: {line=!r}"
                        )
            except Exception as e:
                logger.warning(f"Caught exception reading localisation files: {e}")
        # Add missing format that is similar to but not the same as adj_format in practice
        if "%ADJECTIVE%" not in self.name_mapping:
            # Try to get it from game's localization config:
            adj_format = self.name_mapping.get("adj_format", "adj $1$")
            self.name_mapping["%ADJECTIVE%"] = adj_format.replace("adj", "%adjective%")
        # Alternate format with no template (meant to be concatenated?). Incomplete solution.
        #        if "%ADJ%" not in self.name_mapping:
        #          self.name_mapping["%ADJ%"] = "$1$"
        if "%LEADER_1%" not in self.name_mapping:
            self.name_mapping["%LEADER_1%"] = "$1$ $2$"
        if "%LEADER_2%" not in self.name_mapping:
            self.name_mapping["%LEADER_2%"] = "$1$ $2$"

        logger.debug(f"Found adjective templates: {self.adjective_templates}")

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
        render_template = self._preprocess_template(render_template, name_dict)

        if "value" in name_dict:
            return self.render_from_dict(name_dict["value"])

        substitution_values = self._collect_substitution_values(name_dict)
        render_template = self._substitute_variables(
            render_template, substitution_values
        )
        render_template = self._handle_unresolved_variables(render_template)
        return render_template

    def _collect_substitution_values(self, name_dict):
        substitution_values = []
        for var in name_dict.get("variables", []):
            if "key" in var and "value" in var:
                var_key = var.get("key")
                substitution_values.append(
                    (var_key, self.render_from_dict(var["value"]))
                )
        return substitution_values

    def _preprocess_template(self, render_template, name_dict):
        """
        Handle some special keys.
        """
        if render_template == "%ADJ%":
            render_template = "$1$"
            if (
                "variables" in name_dict
                and "value" in name_dict["variables"][0]
                and "key" in name_dict["variables"][0]["value"]
            ):
                # substitute predefined constants
                tmp = name_dict["variables"][0]["value"]["key"]
                if tmp in self.name_mapping:
                    name_dict["variables"][0]["value"]["key"] = self.name_mapping[tmp]
                name_dict["variables"][0]["value"]["key"] += " $1$"
        elif render_template == "%SEQ%":
            render_template = "$fmt$"

        return render_template

    def _substitute_variables(self, render_template, substitution_values):
        if render_template == "%ACRONYM%":
            for key, acronym_base in substitution_values:
                if key == "base":
                    render_template = "".join(
                        s[0].upper() for s in acronym_base.split()
                    )
                    render_template += acronym_base[-1].upper()
        # try all combinations of escaping identifiers
        parentheses = [
            ("<", ">"),
            ("[", "]"),
            ("$", "$"),
            ("%", "%"),
        ]
        for subst_key, subst_value in substitution_values:
            if subst_key == "num":
                try:
                    render_template = render_template.replace(
                        f"$ORD$", self._fmt_ord_number(int(subst_value))
                    )
                except ValueError:
                    ...
            if subst_key == "adjective":
                subst_value = self._fmt_adjective(subst_value)
            for l, r in parentheses:
                render_template = render_template.replace(
                    f"{l}{subst_key}{r}", subst_value, 1
                )
        return render_template

    def _handle_unresolved_variables(self, render_template):
        # remove any identifiers remaining after substitution:
        for pattern in [
            r"\[[0-9]*\]",
            r"\$[0-9]*\$",
            r"%[0-9]*%",
            r"<[0-9]*>",
        ]:
            # in some cases, the template can be left with some unresolved fields. Example from a game:
            # LEADER_2 -> "$1$ $2$", then $2$ = MAM4_CHR_daughterofNagg -> "$1$, daughter of Nagg"
            # This last template contains another $1$ which is never resolved.
            render_template = re.sub(pattern, "", render_template)
        # post-processing: The above issue can cause
        render_template = ", ".join(s.strip() for s in render_template.split(","))
        render_template = ". ".join(s.strip() for s in render_template.split("."))

        # Special case: tradition and technology have same name...
        match_indirect_reference = re.match(r"\$([a-z0-9_]*)\$", render_template)
        if match_indirect_reference:
            second_lookup = match_indirect_reference.group(1)
            render_template = lookup_key(second_lookup)

        # Find variables that were not resolved so far:
        for match in var_re.findall(render_template):
            if match == "ORD":
                continue
            resolved = lookup_key(match)
            render_template = re.sub(f"\${match}\$", resolved, render_template)

        return render_template

    def _fmt_ord_number(self, num: int):
        if num % 10 == 1:
            return f"{num}st"
        if num % 10 == 2:
            return f"{num}nd"
        if num % 10 == 3:
            return f"{num}rd"
        return f"{num}th"

    def _fmt_adjective(self, noun: str) -> str:
        # {'i': '*ian $1$', 'r': '*ran $1$', 'a': '*an $1$', 'e': '*an $1$', 'us': '*an $1$',
        # 'is': '*an $1$', 'es': '*an $1$', 'ss': '*an $1$', 'id': '*an $1$', 'ed': '*an $1$',
        # 'ad': '*an $1$', 'od': '*an $1$', 'ud': '*an $1$', 'yd': '*an $1$'}
        noun_suffix = ""
        adj_template = "*"
        for suffix in sorted(self.adjective_templates, key=len, reverse=True):
            if noun.endswith(suffix):
                noun_suffix = suffix
                adj_template = self.adjective_templates[suffix]
                break
        return adj_template.replace("*", noun.removesuffix(noun_suffix))

    def _iter_localization_lines(self) -> Iterable[str]:
        for p in self.localization_files:
            with open(p, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.lstrip()
                    if line:
                        yield line


global_renderer: NameRenderer = None


def render_name(json_str: str):
    return get_global_renderer().render_from_json(json_str)


def lookup_key(key: str) -> str:
    return get_global_renderer().render_from_dict({"key": key})


def get_global_renderer() -> NameRenderer:
    global global_renderer
    if global_renderer is None:
        from stellarisdashboard import config

        global_renderer = NameRenderer(config.CONFIG.localization_files)
        global_renderer.load_name_mapping()
    return global_renderer


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
