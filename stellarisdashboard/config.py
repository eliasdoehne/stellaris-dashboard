import dataclasses
import itertools
import json
import logging
import pathlib
import platform
import re
import sys
import traceback
from collections import defaultdict
from typing import List, Dict, Any

import yaml

LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

LOG_FORMAT = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

CONFIG: "Config" = None
logger: logging.Logger = None


def initialize_logger():
    # Add a stream handler for stdout output
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    stdout_ch = logging.StreamHandler(sys.stdout)
    stdout_ch.setLevel(logging.INFO)
    stdout_ch.setFormatter(LOG_FORMAT)
    root_logger.addHandler(stdout_ch)


def _get_default_stellaris_user_data_path():
    # according to https://stellaris.paradoxwikis.com/Save-game_editing
    home = pathlib.Path.home()
    if platform.system() == "Windows":
        one_drive_path = home / "OneDrive/Documents/Paradox Interactive/Stellaris/"
        non_one_drive_path = home / "OneDrive/Documents/Paradox Interactive/Stellaris/"
        if one_drive_path.exists():
            return one_drive_path
        else:
            return non_one_drive_path
    elif platform.system() == "Linux":
        return home / ".local/share/Paradox Interactive/Stellaris/"
    else:
        return home / "Documents/Paradox Interactive/Stellaris/"


def _get_default_save_path():
    return _get_default_stellaris_user_data_path() / "save games"


def _get_default_stellaris_install_path():
    for p in [
        pathlib.Path("C:/Program Files (x86)/Steam/steamapps/common/Stellaris/"),
        (pathlib.Path.home() / ".steam/steamapps/common/Stellaris/").absolute(),
        (pathlib.Path.home() / ".steam/root/steamapps/common/Stellaris/").absolute(),
    ]:
        if p.exists():
            return p
    return pathlib.Path(__file__).parent


def _get_default_base_output_path():
    return pathlib.Path.cwd() / "output"


GALAXY_MAP_TAB = "Galaxy Map"
MARKET_TAB = "Markets"
DEFAULT_TAB_LAYOUT = {
    "Budget": [
        "energy_budget",
        "mineral_budget",
        "consumer_goods_budget",
        "alloys_budget",
        "food_budget",
        "influence_budget",
        "unity_budget",
        "volatile_motes_budget",
        "exotic_gases_budget",
        "rare_crystals_budget",
        "living_metal_budget",
        "zro_budget",
        "dark_matter_budget",
        "nanites_budget",
    ],
    "Economy": [
        "net_energy_income_graph",
        "net_mineral_income_graph",
        "net_alloys_income_graph",
        "net_consumer_goods_income_graph",
        "net_food_income_graph",
    ],
    "Demographics": [
        "species_distribution_graph",
        "job_distribution_graph",
        "ethos_distribution_graph",
        "strata_distribution_graph",
        "faction_distribution_graph",
        "planet_pop_distribution_graph",
    ],
    "Pops": [
        "species_happiness_graph",
        "species_crime_graph",
        "species_power_graph",
        "job_happiness_graph",
        "job_crime_graph",
        "job_power_graph",
        "ethos_happiness_graph",
        "ethos_crime_graph",
        "ethos_power_graph",
        "faction_approval_graph",
        "faction_happiness_graph",
        "faction_support_graph",
        "faction_crime_graph",
        "faction_power_graph",
        "strata_happiness_graph",
        "strata_crime_graph",
        "strata_power_graph",
    ],
    "Planets": [
        "planet_count_graph",
        "planet_migration_graph",
        "planet_stability_graph",
        "planet_happiness_graph",
        "planet_amenities_graph",
        "planet_housing_graph",
        "planet_crime_graph",
        "planet_power_graph",
    ],
    "Science": [
        "technology_progress_graph",
        "survey_progress_graph",
        "research_output_graph",
        "research_output_by_category_graph",
    ],
    "Military": ["fleet_size_graph", "military_power_graph", "fleet_composition_graph"],
    "Victory": [
        "victory_rank_graph",
        "victory_score_graph",
        "victory_economy_score_graph",
    ],
    MARKET_TAB: [],  # filled dynamically based on resource config
}
DEFAULT_MARKET_RESOURCES = [
    # all available resources, in the order in which they are defined in the game files
    # common/strategic_resources/00_strategic_resources.txt
    # Put None as price of non-tradeable resources (These must still be added because ordering matters)
    {"name": "time", "base_price": None},
    {"name": "energy", "base_price": None},
    {"name": "minerals", "base_price": 1},
    {"name": "food", "base_price": 1},
    {"name": "physics_research", "base_price": None},
    {"name": "society_research", "base_price": None},
    {"name": "engineering_research", "base_price": None},
    {"name": "influence", "base_price": None},
    {"name": "unity", "base_price": None},
    {"name": "consumer_goods", "base_price": 2},
    {"name": "alloys", "base_price": 4},
    {"name": "volatile_motes", "base_price": 10},
    {"name": "exotic_gases", "base_price": 10},
    {"name": "rare_crystals", "base_price": 10},
    {"name": "sr_living_metal", "base_price": 20},
    {"name": "sr_zro", "base_price": 20},
    {"name": "sr_dark_matter", "base_price": 20},
    {"name": "nanites", "base_price": None},
    {"name": "minor_artifacts", "base_price": None},
    {"name": "menace", "base_price": None},
]
DEFAULT_MARKET_FEE = [{"date": "2200.01.01", "fee": 0.3}]


DEFAULT_SETTINGS = dict(
    save_file_path=_get_default_save_path(),
    stellaris_install_path=_get_default_stellaris_install_path(),
    stellaris_user_data_path=_get_default_stellaris_user_data_path(),
    stellaris_language="l_english",
    mp_username="",
    base_output_path=_get_default_base_output_path(),
    threads=1,
    host="127.0.0.1",
    port=28053,
    polling_interval=0.5,
    check_version=True,
    log_level="INFO",
    include_id_in_names=True,  # TODO handle duplicate keys in legend entries in a better way
    show_everything=False,
    filter_events_by_type=True,
    show_all_country_types=False,
    save_name_filter="",
    plot_time_resolution=500,
    read_all_countries=False,
    skip_saves=0,
    log_to_file=False,
    plot_width=1150,
    plot_height=640,
    tab_layout=DEFAULT_TAB_LAYOUT,
    market_resources=DEFAULT_MARKET_RESOURCES,
    market_fee=DEFAULT_MARKET_FEE,
    production=False,
)


@dataclasses.dataclass
class Config:
    """Stores the settings for the dashboard."""

    save_file_path: pathlib.Path = None
    stellaris_install_path: pathlib.Path = None
    stellaris_user_data_path: pathlib.Path = None
    stellaris_language: str = None
    mp_username: str = None
    base_output_path: pathlib.Path = None
    threads: int = None

    port: int = None
    host: str = None
    log_level: str = None
    plot_height: int = None
    plot_width: int = None

    polling_interval: float = None

    check_version: bool = None
    filter_events_by_type: bool = None
    show_everything: bool = None
    read_all_countries: bool = None
    show_all_country_types: bool = None
    include_id_in_names: bool = None

    save_name_filter: str = None
    skip_saves: int = None
    plot_time_resolution: int = None

    log_to_file: bool = False
    debug_mode: bool = False

    tab_layout: Dict[str, List[str]] = None
    market_resources: List[Dict[str, Any]] = None
    market_fee: List[Dict[str, float]] = None

    production: bool = False

    EXISTING_PATH_KEYS = {
        # these paths should already exist
        # if not, the setting is reset to default
        # this helps with issues related to OneDrive
        "save_file_path",
        "stellaris_install_path",
        "stellaris_user_data_path",
    }
    PATH_KEYS = EXISTING_PATH_KEYS | {
        "base_output_path",
    }
    BOOL_KEYS = {
        "check_version",
        "filter_events_by_type",
        "show_everything",
        "read_all_countries",
        "show_all_country_types",
        "log_to_file",
        "include_id_in_names",
        "production",
    }
    INT_KEYS = {
        "port",
        "plot_time_resolution",
        "skip_saves",
        "threads",
        "plot_width",
        "plot_height",
    }
    FLOAT_KEYS = {
        "polling_interval",
    }
    STR_KEYS = {
        "host",
        "mp_username",
        "save_name_filter",
        "log_level",
        "stellaris_language",
    }
    DICT_KEYS = {
        "tab_layout",
    }
    LIST_KEYS = {"market_resources", "market_fee"}
    ALL_KEYS = (
        PATH_KEYS | BOOL_KEYS | INT_KEYS | FLOAT_KEYS | STR_KEYS | DICT_KEYS | LIST_KEYS
    )

    def apply_dict(self, settings_dict):
        logger.info("Updating settings")
        tab_layout = self._preprocess_tab_layout(settings_dict)
        settings_dict["tab_layout"] = tab_layout
        for key, val in settings_dict.items():
            if key not in Config.ALL_KEYS:
                logger.info(f"Ignoring unknown setting {key} with value {val}.")
                continue
            old_val = self.__dict__.get(key)
            if key in Config.BOOL_KEYS:
                val = self._preprocess_bool(val)
            if key in Config.PATH_KEYS:
                val = self._process_path_keys(key, val)
                if val is None:
                    continue

            self.__setattr__(key, val)
            if val != old_val:
                logger.info(
                    f"Updated setting {key.ljust(28)} {str(old_val).rjust(8)} -> {str(val).ljust(8)}"
                )

    def _process_path_keys(self, key, val):
        if val == "":
            val = DEFAULT_SETTINGS[key]
        else:
            val = pathlib.Path(val)
            if key in Config.EXISTING_PATH_KEYS and not val.exists():
                val = DEFAULT_SETTINGS[key]
        if key == "base_output_path":
            try:
                if not val.exists():
                    logger.info(f"Creating new {key} directory at {val}")
                    val.mkdir(parents=True)
                elif not val.is_dir():
                    logger.warning(
                        f"Ignoring setting {key} with value {val}: Path exists and is not a directory"
                    )
                    return
            except Exception:
                logger.warning(
                    f"Error during path creation while updating {key} with value {val}:"
                )
                logger.error(traceback.format_exc())
                logger.info(f"Ignoring setting {key} with value {val}.")
                return
        return val

    def _preprocess_tab_layout(self, settings_dict):
        layout_dict = settings_dict.get("tab_layout", DEFAULT_TAB_LAYOUT)
        if not isinstance(layout_dict, dict):
            logger.error(f"Invalid tab layout configuration: {layout_dict}")
            logger.info(f"Falling back to default tab layout.")
            return DEFAULT_SETTINGS["tab_layout"]
        processed = defaultdict(list)
        for tab, plot_list in layout_dict.items():
            if tab == GALAXY_MAP_TAB:
                logger.warning(f"Ignoring tab {tab}, it is reserved for the galaxy map")
                continue
            if tab == MARKET_TAB:
                logger.warning(
                    f"Ignoring values for tab {tab}, it is filled dynamically"
                )
                processed[tab] = []
                continue
            if not isinstance(plot_list, list):
                logger.warning(f"Ignoring invalid graph list for tab {tab}")
                pass
            for g in plot_list:
                if not isinstance(g, str):
                    logger.warning(f"Ignoring invalid graph ID {g}")
                    pass
                processed[tab].append(g)
        return dict(processed)

    def write_to_file(self):
        fname = _get_settings_file_path()
        if fname.exists() and not fname.is_file():
            raise ValueError(f"Settings file {fname} exists and is not a file!")
        logger.info(f"Writing settings to {fname}")
        with open(fname, "w") as f:
            settings_dict = self.get_dict()
            yaml.dump(settings_dict, f, default_flow_style=False, sort_keys=False)

    def get_dict(self):
        result = dict(**DEFAULT_SETTINGS)
        for key, val in self.__dict__.items():
            if key in Config.ALL_KEYS:
                if key in Config.PATH_KEYS:
                    val = str(val)
                result[key] = val
        return result

    def get_adjustable_settings_dict(self):
        return dataclasses.asdict(self)

    def __str__(self):
        lines = [
            "Configuration:",
            f"  save_file_path: {repr(self.save_file_path)}",
            f"  base_output_path: {repr(self.base_output_path)}",
            f"  threads: {repr(self.threads)}",
            f"  show_everything: {repr(self.show_everything)}",
            f"  show_all_country_types: {repr(self.show_all_country_types)}",
        ]
        return "\n".join(lines)

    def _preprocess_bool(self, val):
        if isinstance(val, bool):
            return val
        elif val == "true":
            return True
        elif val == "false":
            return False
        raise ValueError(
            f"Expected either true or false for bool value, received {val}."
        )

    @property
    def db_path(self) -> pathlib.Path:
        path = self.base_output_path / "db/"
        if not path.exists():
            path.mkdir(parents=True)
        return path

    @property
    def game_data_dirs(self):
        dlc_load_path = self.stellaris_user_data_path / "dlc_load.json"
        try:
            with open(dlc_load_path, "r") as f:
                dlc_load = json.load(f)
                mod_descriptor_paths = [
                    self.stellaris_user_data_path / relative_mod_descriptor_path
                    for relative_mod_descriptor_path in dlc_load.get("enabled_mods", [])
                ]
                mod_data_paths = [
                    _get_mod_data_dir(descriptor_path)
                    for descriptor_path in mod_descriptor_paths
                    if descriptor_path.exists()
                ]
                return [self.stellaris_install_path] + [
                    mod_data_path
                    for mod_data_path in mod_data_paths
                    if mod_data_path is not None
                ]
        except Exception as e:
            logger.warning(f"Failed to read enabled mods from {dlc_load_path}. Using only base game data.")
            logger.warning(e)
            return [self.stellaris_install_path]

    @property
    def localization_files(self):
        YAML_SUFFIXES = ("yaml", "yml")
        files = list(
            path
            for path in itertools.chain(
                *(
                    (data_dir / "localisation").glob(f"**/*.{yaml_suffix}")
                    for data_dir in self.game_data_dirs
                    for yaml_suffix in YAML_SUFFIXES
                )
            )
            if _localization_file_matches_language(path, self.stellaris_language)
        )
        logger.info(
            f"Loaded {len(files)} localization files from {self.game_data_dirs}"
        )
        return files


def _get_mod_data_dir(mod_descriptor_path: pathlib.Path):
    try:
        with open(mod_descriptor_path, "r") as f:
            for line in f:
                match = re.match(r'^\s*path\s*=\s*"(.*)"\s*$', line)
                if match:
                    return pathlib.Path(match.group(1))
    except Exception as e:
        logger.warn(f"Failed to read mod path from {mod_descriptor_path}")
        logger.warn(e)
    return None


def _localization_file_matches_language(file_path: pathlib.Path, language: str):
    with open(file_path, "r", encoding="utf-8") as f:
        first_line = f.readline()
        match = re.match(rf"^\ufeff\s*{language}\s*:\s*$", first_line)
        return match is not None


def _apply_existing_settings(config: Config):
    settings = dict(DEFAULT_SETTINGS)
    settings_file = _get_settings_file_path()
    if settings_file.exists() and settings_file.is_file():
        logger.info(f"Reading settings from {settings_file}...")
        with open(settings_file, "r") as f:
            file_settings = yaml.load(f, Loader=yaml.SafeLoader) or {}
            settings.update(file_settings)
    config.apply_dict(settings)


def _get_settings_file_path() -> pathlib.Path:
    return pathlib.Path.cwd() / "config.yml"


def initialize():
    global CONFIG
    if CONFIG is not None:
        return
    global logger
    initialize_logger()
    logger = logging.getLogger()
    CONFIG = Config()
    _apply_existing_settings(CONFIG)

    # Initialize output paths
    if not CONFIG.base_output_path.exists():
        (CONFIG.base_output_path / "db").mkdir(parents=True)
        (CONFIG.base_output_path / "output").mkdir(parents=True)

    configure_logger()
    CONFIG.write_to_file()


def configure_logger():
    global logger
    logger.setLevel(LOG_LEVELS.get(CONFIG.log_level, logging.INFO))
    for h in logger.handlers:
        h.setLevel(LOG_LEVELS.get(CONFIG.log_level, logging.INFO))
    if CONFIG.log_to_file:
        file_ch = logging.FileHandler(CONFIG.base_output_path / "log.txt")
        file_ch.setLevel(LOG_LEVELS.get(CONFIG.log_level, logging.WARN))
        file_ch.setFormatter(LOG_FORMAT)
        logger.addHandler(file_ch)


initialize()
