import pathlib
import dataclasses
import logging
import platform
import multiprocessing as mp
import sys

mp.freeze_support()

LOG_LEVELS = {"INFO": logging.INFO, "DEBUG": logging.DEBUG}


def initialize_logger():
    # Add a stream handler for stdout output
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    root_logger.addHandler(ch)


LAST_UPDATED_GAME_FILE = "last_updated_game.txt"
LAST_UPDATED_GAME = None


def get_last_updated_game():
    global LAST_UPDATED_GAME
    if LAST_UPDATED_GAME is not None:
        return LAST_UPDATED_GAME
    game_name = None
    last_update_file = CONFIG.base_output_path / "db" / LAST_UPDATED_GAME_FILE
    if last_update_file.exists():
        with open(last_update_file, "r") as f:
            game_name = f.readline().strip()
            if game_name:
                LAST_UPDATED_GAME = game_name
                logger.info(f"Initialized last updated game as {game_name} from file...")
            else:
                game_name = None
    return game_name


def set_last_updated_game(game_name: str):
    global LAST_UPDATED_GAME
    if game_name != LAST_UPDATED_GAME:
        LAST_UPDATED_GAME = game_name
        last_update_file = CONFIG.base_output_path / "db" / LAST_UPDATED_GAME_FILE
        if not last_update_file.parent.exists():
            last_update_file.parent.mkdir()
        with open(last_update_file, "w") as f:
            game_name = f.write(game_name)
            logger.info(f"Updated last updated game as {game_name} in file...")
    return game_name


initialize_logger()
logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Config:
    save_file_path: pathlib.Path = None
    base_output_path: pathlib.Path = None
    threads: int = max(1, mp.cpu_count() // 2 - 1)
    port: int = 28053
    colormap: str = "viridis"
    log_level: str = "INFO"

    debug_mode: bool = False
    debug_save_name_filter: str = ""
    debug_only_every_nth_save: int = 1
    debug_only_last_save_file: bool = False

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
        ]
        return "\n".join(lines)


def _get_default_save_path():
    if platform.system() == "Linux":
        return pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"
    elif platform.system() == "Windows":
        return pathlib.Path.home() / "Documents\Paradox Interactive\Stellaris\save games"
    return None


def _get_default_base_output_path():
    system = platform.system()
    if system == "Linux":
        return pathlib.Path.home() / ".local/share/stellaristimeline/"
    elif system == "Windows":
        return pathlib.Path.home() / "Documents/stellaristimeline"
    return None


CONFIG = Config(
    save_file_path=_get_default_save_path(),
    base_output_path=_get_default_base_output_path(),
)


def _apply_config_ini():
    this_dir = pathlib.Path(__file__).parent
    config_file = this_dir / "config.ini"
    if not config_file.exists():
        logger.info(f"No config.ini file found in {this_dir}... Using defaults.")
        return
    logger.info("Reading config.ini file...")
    ini_comment_symbol = ";"
    if pathlib.Path(config_file).exists() and pathlib.Path(config_file).is_file():
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
            logger.info(f"Applying parameter {key} -> {value}")
            key = key.strip()
            value = value.strip()
            if not key or not value:
                logger.warning(f"Ignoring bad configuration option {key} with value {value}.")
            if key == "threads":
                value = int(value)
            elif key == "port":
                value = int(value)
            elif key in {"save_file_path", "base_output_path"}:
                if value.startswith("$HOME/"):
                    value = pathlib.Path.home() / value[len("$HOME/"):]
                else:
                    value = pathlib.Path(value)
                if not value.exists():
                    logger.info(f"Path {value} of type {key} does not exist yet.")
                    value.mkdir()
            if hasattr(CONFIG, key):
                setattr(CONFIG, key, value)
            else:
                logger.warning(f'Ignoring unrecognized config.ini option {key} with value {value}.')

    level = LOG_LEVELS.get(CONFIG.log_level, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)


_apply_config_ini()
if not CONFIG.is_valid():
    raise ValueError(f"Configuration is missing some options: \n{CONFIG}")
