import logging
import multiprocessing as mp  # only to get the cpu count
import pathlib
import platform
import sys
import traceback
from collections import defaultdict
from typing import List, Dict

import yaml

import dataclasses

LOG_LEVELS = {"INFO": logging.INFO, "DEBUG": logging.DEBUG}
CPU_COUNT = mp.cpu_count()

LOG_FORMAT = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def initialize_logger():
    # Add a stream handler for stdout output
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    stdout_ch = logging.StreamHandler(sys.stdout)
    stdout_ch.setLevel(logging.INFO)
    stdout_ch.setFormatter(LOG_FORMAT)
    root_logger.addHandler(stdout_ch)
    if mp.current_process().name != "MainProcess":
        root_logger.setLevel(logging.ERROR)
        stdout_ch.setLevel(logging.ERROR)


CONFIG = None
logger = None


def _get_default_thread_count():
    if CPU_COUNT < 4:
        threads = 1
    elif CPU_COUNT == 4:
        threads = 2
    else:
        threads = max(1, CPU_COUNT // 2 - 1)
    return threads


def _get_default_save_path():
    # according to https://stellaris.paradoxwikis.com/Save-game_editing
    if platform.system() == "Windows":
        return (
            pathlib.Path.home() / "Documents/Paradox Interactive/Stellaris/save games/"
        )
    elif platform.system() == "Linux":
        return (
            pathlib.Path.home()
            / ".local/share/Paradox Interactive/Stellaris/save games/"
        )
    else:
        return (
            pathlib.Path.home() / "Documents/Paradox Interactive/Stellaris/save games/"
        )


def _get_default_base_output_path():
    return pathlib.Path.cwd() / "output"


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
}
GALAXY_MAP_TAB = "Galaxy Map"

DEFAULT_SETTINGS = dict(
    save_file_path=_get_default_save_path(),
    mp_username="",
    base_output_path=_get_default_base_output_path(),
    threads=_get_default_thread_count(),
    port=28053,
    polling_interval=0.5,
    check_version=True,
    log_level="INFO",
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
)


@dataclasses.dataclass
class Config:
    """ Stores the settings for the dashboard. """

    save_file_path: pathlib.Path = None
    mp_username: str = None
    base_output_path: pathlib.Path = None
    threads: int = None

    port: int = None
    log_level: str = None
    plot_height: int = None
    plot_width: int = None

    polling_interval: float = None

    check_version: bool = None
    filter_events_by_type: bool = None
    show_everything: bool = None
    read_all_countries: bool = None
    show_all_country_types: bool = None

    save_name_filter: str = None
    skip_saves: int = None
    plot_time_resolution: int = None

    log_to_file: bool = False
    debug_mode: bool = False

    tab_layout: Dict[str, List[str]] = None

    PATH_KEYS = {
        "base_output_path",
        "save_file_path",
    }
    BOOL_KEYS = {
        "check_version",
        "filter_events_by_type",
        "show_everything",
        "read_all_countries",
        "show_all_country_types",
        "log_to_file",
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
        "mp_username",
        "save_name_filter",
        "log_level",
    }
    DICT_KEYS = {"tab_layout"}
    ALL_KEYS = PATH_KEYS | BOOL_KEYS | INT_KEYS | FLOAT_KEYS | STR_KEYS | DICT_KEYS

    def apply_dict(self, settings_dict):
        logger.info("Updating settings")
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
            if key == "tab_layout":
                val = self._process_tab_layout(val)
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

    def _process_tab_layout(self, layout_dict):
        if not isinstance(layout_dict, dict):
            logger.error(f"Invalid tab layout configuration: {layout_dict}")
            logger.info(f"Falling back to default tab layout.")
            return DEFAULT_SETTINGS["tab_layout"]
        processed = defaultdict(list)
        for tab, plot_list in layout_dict.items():
            if tab == GALAXY_MAP_TAB:
                logger.warning(f"Ignoring tab {tab}, it is reserved for the galaxy map")
                pass
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
            yaml.dump(settings_dict, f, default_flow_style=False)

    def get_dict(self):
        result = dict(**DEFAULT_SETTINGS)
        for key, val in self.__dict__.items():
            if key in Config.ALL_KEYS:
                if key in CONFIG.PATH_KEYS:
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


def _apply_existing_settings(config: Config):
    settings = dict(DEFAULT_SETTINGS)
    settings_file = _get_settings_file_path()
    if settings_file.exists() and settings_file.is_file():
        logger.info(f"Reading settings from {settings_file}...")
        with open(settings_file, "r") as f:
            settings.update(yaml.load(f, Loader=yaml.SafeLoader))
    config.apply_dict(settings)


def _get_settings_file_path() -> pathlib.Path:
    return pathlib.Path.cwd() / "config.yml"


def initialize():
    global CONFIG
    global logger
    initialize_logger()
    logger = logging.getLogger(__name__)
    CONFIG = Config()
    _apply_existing_settings(CONFIG)

    # Initialize output paths
    if not CONFIG.base_output_path.exists():
        (CONFIG.base_output_path / "db").mkdir(parents=True)
        (CONFIG.base_output_path / "output").mkdir(parents=True)

    configure_logger()


def configure_logger():
    global logger
    logger.setLevel(LOG_LEVELS.get(CONFIG.log_level, logging.INFO))
    if CONFIG.log_to_file:
        logger = logging.getLogger()
        file_ch = logging.FileHandler(CONFIG.base_output_path / "log.txt")
        file_ch.setLevel(logging.WARN)
        file_ch.setFormatter(LOG_FORMAT)
        logger.addHandler(file_ch)
