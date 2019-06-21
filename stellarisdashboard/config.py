import logging
import multiprocessing as mp  # only to get the cpu count
import pathlib
import platform
import sys
import traceback

import yaml

import dataclasses

LOG_LEVELS = {"INFO": logging.INFO, "DEBUG": logging.DEBUG}
CPU_COUNT = mp.cpu_count()

LOG_FORMAT = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')


def initialize_logger():
    # Add a stream handler for stdout output
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    stdout_ch = logging.StreamHandler(sys.stdout)
    stdout_ch.setLevel(logging.INFO)
    stdout_ch.setFormatter(LOG_FORMAT)
    root_logger.addHandler(stdout_ch)


def _add_file_handler():
    if CONFIG.log_to_file:
        root_logger = logging.getLogger()
        file_ch = logging.FileHandler(CONFIG.base_output_path / "logs.txt")
        file_ch.setLevel(logging.WARN)
        file_ch.setFormatter(LOG_FORMAT)
        root_logger.addHandler(file_ch)


initialize_logger()
logger = logging.getLogger(__name__)


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
        return pathlib.Path.home() / "Documents/Paradox Interactive/Stellaris/save games/"
    elif platform.system() == "Linux":
        return pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"
    else:
        return pathlib.Path.home() / "Documents/Paradox Interactive/Stellaris/save games/"


def _get_default_base_output_path():
    return pathlib.Path.cwd() / "output"


DEFAULT_SETTINGS = dict(
    save_file_path=_get_default_save_path(),
    steam_username="",
    base_output_path=_get_default_base_output_path(),
    threads=_get_default_thread_count(),
    port=28053,
    polling_interval=0.5,
    save_file_delay=0.5,
    max_file_read_attempts=3,
    check_version=True,
    colormap="viridis",
    log_level="INFO",
    show_everything=False,
    filter_events_by_type=True,
    only_show_default_empires=True,
    save_name_filter="",
    read_non_player_countries=False,
    budget_pop_stats_frequency=1,
    read_only_every_nth_save=1,
    plot_time_resolution=200,
    normalize_stacked_plots=False,
    use_two_y_axes_for_budgets=False,
    log_to_file=False,
    plot_width=1150,
    plot_height=640,
)


@dataclasses.dataclass
class Config:
    """ Stores the settings for the dashboard. """
    save_file_path: pathlib.Path = None
    steam_username: str = None
    base_output_path: pathlib.Path = None
    threads: int = None

    port: int = None
    colormap: str = None
    log_level: str = None
    plot_height: int = None
    plot_width: int = None

    polling_interval: float = None
    save_file_delay: float = None
    max_file_read_attempts: int = None

    check_version: bool = None
    show_everything: bool = None
    read_non_player_countries: bool = None
    budget_pop_stats_frequency: int = None
    only_show_default_empires: bool = None
    filter_events_by_type: bool = None

    normalize_stacked_plots: bool = None
    use_two_y_axes_for_budgets: bool = None

    save_name_filter: str = None
    read_only_every_nth_save: int = None
    plot_time_resolution: int = None

    debug_mode: bool = False
    log_to_file: bool = False

    PATH_KEYS = {
        "base_output_path",
        "save_file_path",
    }
    BOOL_KEYS = {
        "check_version",
        "filter_events_by_type",
        "show_everything",
        "read_non_player_countries",
        "only_show_default_empires",
        "normalize_stacked_plots",
        "use_two_y_axes_for_budgets",
        "log_to_file",
    }
    INT_KEYS = {
        "port",
        "read_only_every_nth_save",
        "plot_time_resolution",
        "budget_pop_stats_frequency",
        "threads",
        "plot_width",
        "plot_height",
        "max_file_read_attempts",
    }
    FLOAT_KEYS = {
        "polling_interval",
        "save_file_delay",
    }
    STR_KEYS = {
        "steam_username",
        "colormap",
        "save_name_filter",
        "log_level",
    }
    DEPRECATED_KEYS = {
        "extract_system_ownership",
        "only_read_player_history",
    }
    ALL_KEYS = PATH_KEYS | BOOL_KEYS | INT_KEYS | FLOAT_KEYS | STR_KEYS

    def apply_dict(self, settings_dict):
        logger.info("Updating settings")
        for key, val in settings_dict.items():
            if key in Config.DEPRECATED_KEYS:
                logger.info(f'Ignoring deprecated setting {key} with value {val}.')
                continue
            if key not in Config.ALL_KEYS:
                logger.info(f'Ignoring unknown setting {key} with value {val}.')
                continue
            old_val = self.__dict__.get(key)
            if key in Config.BOOL_KEYS:
                val = self._preprocess_bool(val)

            if key in Config.PATH_KEYS:
                if val == "":
                    val = DEFAULT_SETTINGS[key]
                else:
                    val = pathlib.Path(val)
                if key == "base_output_path":
                    try:
                        if not val.exists():
                            logger.info(f'Creating new {key} directory at {val}')
                            val.mkdir(parents=True)
                        elif not val.is_dir():
                            logger.warning(f'Ignoring path setting {key} with value {val}, as the provided value exists and is not a directory!')
                            continue
                    except Exception:
                        logger.warning(f"Error during path creation while updating {key} option with value {val}:")
                        logger.error(traceback.format_exc())
                        logger.info(f"Ignoring setting {key} with value {val}.")
                        continue

            self.__setattr__(key, val)
            if val != old_val:
                logger.info(f'Updated setting {key.ljust(28)} {str(old_val).rjust(8)} -> {str(val).ljust(8)}')

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
            f"  only_show_default_empires: {repr(self.only_show_default_empires)}",
        ]
        return "\n".join(lines)

    def _preprocess_bool(self, val):
        if isinstance(val, bool):
            return val
        elif val == "true":
            return True
        elif val == "false":
            return False
        raise ValueError(f"Expected either true or false for bool value, received {val}.")


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


def update_log_level():
    level = LOG_LEVELS.get(CONFIG.log_level, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)


# Initialize the Config object with the default settings
CONFIG = Config()
_apply_existing_settings(CONFIG)

# Initialize output paths
if not CONFIG.base_output_path.exists():
    (CONFIG.base_output_path / "db").mkdir(parents=True)
    (CONFIG.base_output_path / "output").mkdir(parents=True)
update_log_level()
_add_file_handler()
