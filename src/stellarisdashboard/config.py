import pathlib
import dataclasses
import logging
import platform

logger = logging.getLogger(__name__)
CONFIG_FILE = pathlib.Path(__file__).parent / "config.ini"


@dataclasses.dataclass
class Config:
    save_file_path: pathlib.Path = None
    base_output_path: pathlib.Path = None
    threads: int = 1
    colormap: str = "viridis"

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


def get_save_path():
    return CONFIG.save_file_path


def get_base_path():
    return CONFIG.base_output_path


def _get_default_save_path():
    if platform.system() == "Linux":
        return pathlib.Path.home() / ".local/share/Paradox Interactive/Stellaris/save games/"
    elif platform.system() == "Linux":
        return pathlib.Path.home() / "\Documents\Paradox Interactive\Stellaris\save"
    return None


def _get_default_base_output_path():
    if platform.system() == "Linux":
        return pathlib.Path.home() / ".local/share/stellaristimeline/"

    return None


CONFIG = Config(
    save_file_path=_get_default_save_path(),
    base_output_path=_get_default_base_output_path(),
)


def _apply_config_ini():
    logger.info("Reading config.ini file...")
    ini_comment_symbol = ";"
    if pathlib.Path(CONFIG_FILE).exists() and pathlib.Path(CONFIG_FILE).is_file():
        with open(CONFIG_FILE, "r") as f:
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
            if not key or not value:
                logger.warning(f"Ignoring bad configuration option {key} with value {value}.")
            if key == "threads":
                value = int(value)
            elif key in {"save_file_path", "base_output_path"}:
                value = pathlib.Path(value)
                if not value.exists():
                    logger.info(f"Path {value} of type {key} does not exist yet.")
                    value.mkdir()
            if hasattr(CONFIG, key):
                setattr(CONFIG, key, value)
            else:
                logger.warning(f'Unrecognized configuration option {key} with value {value}')


_apply_config_ini()
if not CONFIG.is_valid():
    raise ValueError("Configuration is missing some options: \n{CONFIG}")
