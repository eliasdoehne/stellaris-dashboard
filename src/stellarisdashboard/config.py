import logging
import multiprocessing as mp  # only to get the cpu count
import pathlib
import platform
import sys

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


@dataclasses.dataclass
class Config:
    save_file_path: pathlib.Path = None
    base_output_path: pathlib.Path = None

    # These are the default values
    if CPU_COUNT < 4:
        threads = 1
    elif CPU_COUNT == 4:
        threads = 2
    else:
        threads = max(1, CPU_COUNT // 2 - 1)
    port: int = 28053
    colormap: str = "viridis"
    log_level: str = "INFO"

    check_version = True

    show_everything: bool = False
    only_show_default_empires: bool = True
    extract_system_ownership: bool = True

    debug_mode: bool = False
    save_name_filter: str = ""
    read_only_every_nth_save: int = 1

    BOOL_KEYS = {
        "show_everything",
        "only_show_default_empires",
        "extract_system_ownership",
        "debug_mode",
    }
    INT_KEYS = {
        "threads",
        "port",
        "read_only_every_nth_save",
    }

    def is_valid(self):
        return all([
            self.save_file_path is not None,
            self.base_output_path is not None,
            self.threads is not None,
        ])

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


CONFIG = Config(
    save_file_path=_get_default_save_path(),
    base_output_path=_get_default_base_output_path(),
)


def _apply_config_ini():
    this_dir = pathlib.Path(__file__).parent
    config_file = pathlib.Path(this_dir / "config.ini")
    if not config_file.exists():
        logger.info(f"No config.ini file found in {this_dir}... Using defaults.")
        return
    logger.info("Reading config.ini file...")
    ini_comment_symbol = ";"
    if config_file.exists() and config_file.is_file():
        with open(config_file, "r") as f:
            config_lines = []
            for line in f:
                processed_line = line.strip()
                processed_line = processed_line.split(ini_comment_symbol)[0]
                if not processed_line:
                    continue
                processed_line = processed_line.split("=")
                if len(processed_line) != 2:
                    logger.warning(f'Encountered bad line:\n  "{line}"')
                    continue
                config_lines.append(tuple(processed_line))
        if config_lines:
            logger.info("Applying configuration from config.ini file...")
        for key, value in config_lines:
            key = key.strip()
            value = value.strip()
            logger.info(f'Applying parameter {key} -> "{value}"')
            if not key or not value:
                logger.warning(f"Ignoring bad configuration option {key} with value {value}.")
            if key in Config.INT_KEYS:
                value = int(value)
            elif key in {"save_file_path", "base_output_path"}:
                if value.startswith("$HOME/"):
                    value = pathlib.Path.home() / value[len("$HOME/"):]
                else:
                    value = pathlib.Path(value)
            elif key in Config.BOOL_KEYS:
                if value.lower() == "false":
                    value = False
                else:
                    value = bool(value)
            if hasattr(CONFIG, key):
                setattr(CONFIG, key, value)
            else:
                logger.warning(f'Ignoring unrecognized config.ini option {key} with value {value}.')
    if not CONFIG.base_output_path.exists():
        CONFIG.base_output_path.mkdir()
        (CONFIG.base_output_path / "db").mkdir()
        (CONFIG.base_output_path / "output").mkdir()
    update_log_level()


def update_log_level():
    level = LOG_LEVELS.get(CONFIG.log_level, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)


_apply_config_ini()
if not CONFIG.is_valid():
    raise ValueError(f"Configuration is missing some options: \n{CONFIG}")
print(CONFIG)
