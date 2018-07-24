import logging
import multiprocessing as mp  # only to get the cpu count
import pathlib
import platform
import sys
import yaml

import dataclasses

from stellarisdashboard import default_paths

LOG_LEVELS = {"INFO": logging.INFO, "DEBUG": logging.DEBUG}
CPU_COUNT = mp.cpu_count()


def initialize_logger():
    # Add a stream handler for stdout output
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    root_logger.addHandler(ch)


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
    if platform.system() == "Linux":
        return pathlib.Path.home() / default_paths.linux_save_path  # ".local/share/Paradox Interactive/Stellaris/save games/"
    elif platform.system() == "Windows":
        return pathlib.Path.home() / default_paths.win_save_path
    return None


def _get_default_base_output_path():
    system = platform.system()
    if system == "Linux":
        return pathlib.Path.home() / default_paths.linux_output_path
    elif system == "Windows":
        return pathlib.Path.home() / default_paths.win_output_path
    return None


DEFAULT_SETTINGS = dict(
    threads=_get_default_thread_count(),
    port=28053,
    check_version=True,
    colormap="viridis",
    log_level="INFO",
    show_everything=False,
    only_show_default_empires=True,
    extract_system_ownership=True,
    save_name_filter="",
    read_only_every_nth_save=1,
    debug_mode=False,
)


@dataclasses.dataclass
class Config:
    save_file_path: pathlib.Path = None
    base_output_path: pathlib.Path = None
    threads = None

    port: int = None
    colormap: str = None
    log_level: str = None

    check_version: bool = None
    debug_mode = None
    show_everything: bool = None
    only_show_default_empires: bool = None
    extract_system_ownership: bool = None

    save_name_filter: str = None
    read_only_every_nth_save: int = None

    PATH_KEYS = {
        "base_output_path",
        "save_file_path",
    }
    BOOL_KEYS = {
        "check_version",
        "extract_system_ownership",
        "only_show_default_empires",
        "show_everything",
    }
    INT_KEYS = {
        "port",
        "read_only_every_nth_save",
        "threads",
    }
    STR_KEYS = {
        "colormap",
        "save_name_filter",
        "log_level",
    }
    ALL_KEYS = PATH_KEYS | BOOL_KEYS | INT_KEYS | STR_KEYS

    def apply_dict(self, settings_dict):
        logger.info("Updating settings")
        for key, val in settings_dict.items():
            if key not in Config.ALL_KEYS:
                logger.warning(f'Ignoring undefined option {key} with value {val}.')
                continue
            logger.info(f'Applying parameter {key} -> "{val}"')
            if key in Config.BOOL_KEYS:
                val = self._preprocess_bool(val)
            if key in Config.PATH_KEYS:
                logger.warning(f'Ignoring path option {key}. Change paths by editing default_paths.py instead!')
                continue
            self.__setattr__(key, val)

    def get_dict(self):
        result = dict(**DEFAULT_SETTINGS)
        for key, val in self.__dict__.items():
            if key in Config.ALL_KEYS and key not in Config.PATH_KEYS:
                result[key] = val
        return result

    def write_to_file(self):
        fname = _get_settings_file_path()
        if fname.exists() and not fname.is_file():
            raise ValueError("Settings file {fname} is not a file!")
        logger.info(f"Writing settings to {fname}")
        with open(fname, "w") as f:
            settings_dict = self.get_dict()  # {k: v for (k, v) in self.get_dict().items() if k not in Config.PATH_KEYS}
            yaml.dump(settings_dict, f)

    def __str__(self):
        lines = [
            "Configuration:",
            f"  save_file_path: {self.save_file_path}",
            f"  base_output_path: {self.base_output_path}",
            f"  threads: {self.threads}",
            f"  show_everything: {self.show_everything}",
            f"  only_show_default_empires: {self.only_show_default_empires}",
            f"  extract_system_ownership: {self.extract_system_ownership}",
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

    def _preprocess_path(self, path: str):
        if path.startswith("$HOME/"):
            return pathlib.Path.home() / path[len("$HOME/"):]
        else:
            return pathlib.Path(path)


def _get_settings_file_path() -> pathlib.Path:
    this_dir = pathlib.Path(__file__).parent
    settings_file = pathlib.Path(this_dir / "config.yml")
    return settings_file


def _apply_existing_config(config: Config):
    settings_file = _get_settings_file_path()
    if not settings_file.exists() or not settings_file.is_file():
        logger.info(f"No settings file found. Using default settings.")
        config.apply_dict(DEFAULT_SETTINGS)
        return

    logger.info(f"Reading settings file {settings_file}...")
    with open(settings_file, "r") as f:
        settings = yaml.load(f)
    config.apply_dict(settings)


def update_log_level():
    level = LOG_LEVELS.get(CONFIG.log_level, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)


# Initialize the Config object with the default settings
CONFIG = Config(
    save_file_path=_get_default_save_path(),
    base_output_path=_get_default_base_output_path(),
)
CONFIG.apply_dict(DEFAULT_SETTINGS)
_apply_existing_config(CONFIG)

# Initialize output path
if not CONFIG.base_output_path.exists():
    CONFIG.base_output_path.mkdir()
    (CONFIG.base_output_path / "db").mkdir()
    (CONFIG.base_output_path / "output").mkdir()
update_log_level()

CONFIG.write_to_file()
print(CONFIG)
