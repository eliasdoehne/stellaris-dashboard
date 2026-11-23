import logging
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import rust_parser

from stellarisdashboard import config

logger = logging.getLogger(__name__)

# the default recursion limit is not high enough for pickling some parsed saves (tools/perf/bench.py --cache)
if sys.getrecursionlimit() < 2000:
    sys.setrecursionlimit(2000)

class StellarisFileFormatError(Exception):
    pass


class SavePathMonitor:
    """
    Checks the save path for new save games. The live save monitor (cli.f_monitor_saves) uses
    this class; the import worker processes parse and import the saves it reports.
    """

    def __init__(self, save_parent_dir, game_name_prefix: str = ""):
        self.processed_saves: Set[pathlib.Path] = set()
        self.num_encountered_saves: int = 0
        self.save_parent_dir = pathlib.Path(save_parent_dir)
        self.game_name_prefix = game_name_prefix
        # (modification time in ns, size) of ironman.sav files last reported, by path
        self._ironman_versions: Dict[pathlib.Path, Tuple[int, int]] = {}

    def get_new_savefiles(self) -> List[pathlib.Path]:
        """Get a list of all new save files, filtered by the skip_saves setting."""
        return self.apply_save_filters(self._valid_save_files())

    def apply_save_filters(self, save_files: List[pathlib.Path]) -> List[pathlib.Path]:
        """Apply the skip_saves setting to the given save files."""
        return self._apply_skip_savefiles_filter(save_files)

    def _apply_skip_savefiles_filter(
        self, new_files: List[pathlib.Path]
    ) -> List[pathlib.Path]:
        if not new_files or config.CONFIG.skip_saves == 0:
            return new_files
        new_files_str = ", ".join(f.stem for f in new_files[:10])
        logger.info(f"Found {len(new_files)} new files: {new_files_str}...")
        filtered_files = []
        for f in new_files:
            self.num_encountered_saves += 1
            if self.num_encountered_saves % (1 + config.CONFIG.skip_saves) == 0:
                filtered_files.append(f)
        logger.info(
            f"Reduced to {len(filtered_files)} files due to skip_saves={config.CONFIG.skip_saves}..."
        )
        return filtered_files

    def mark_all_existing_saves_processed(self) -> None:
        """Ensure that existing files are not re-parsed."""
        self._valid_save_files()

    def _valid_save_files(self) -> List[pathlib.Path]:
        """
        The save files that are new since the last call, sorted. Each file returned counts as seen
        from now on, whether or not the skip_saves filter keeps it.

        This does not use file timestamps, which cannot tell a save that appeared during the scan or
        within the file system's timestamp granularity from an old one. Every save has a name of
        its own and is new until it is in processed_saves. Only ironman.sav, which the game
        overwrites, is told apart by its version (modification time and size).
        """
        new_files = []
        for save_file in self.save_parent_dir.glob("**/*.sav"):
            if save_file in self.processed_saves or not str(save_file.parent.stem).startswith(
                self.game_name_prefix
            ):
                continue
            if save_file.stem == "ironman":
                try:
                    stat = save_file.stat()
                except OSError:
                    continue  # gone or locked; look again next time
                version = (stat.st_mtime_ns, stat.st_size)
                if self._ironman_versions.get(save_file) == version:
                    continue
                self._ironman_versions[save_file] = version
            new_files.append(save_file)
        self.processed_saves.update(f for f in new_files if f.stem != "ironman")
        return sorted(new_files)


class BatchSavePathMonitor(SavePathMonitor):
    """
    SavePathMonitor implementation for importing large numbers of saves with the CLI command
    `stellarisdashboardcli parse-saves` or the import page. It only selects the saves; the import
    worker processes parse and import them (cli.f_parse_saves).

    :param save_files: Report exactly these files, in this order, instead of searching the save path.
    """

    def __init__(
        self,
        save_parent_dir,
        game_name_prefix: str = "",
        save_files: Optional[List[pathlib.Path]] = None,
    ):
        super().__init__(save_parent_dir, game_name_prefix)
        self._save_files = save_files

    def get_new_savefiles(self) -> List[pathlib.Path]:
        if self._save_files is not None:
            return list(self._save_files)
        return super().get_new_savefiles()


def parse_save(filename) -> Dict[str, Any]:
    """
    Parse a single save file.

    :param filename: Path to a .sav file
    :return: The gamestate dictionary
    """

    logger.info(f"Reading save file {filename}.")
    start = time.time()
    parsed = rust_parser.parse_save_file(str(filename.absolute()))
    if not isinstance(parsed, dict):
        raise ValueError(f"Could not parse {filename}")
    dt = time.time() - start
    logger.info(f"Parsed save file {filename} in {dt:.3f} seconds.")
    return parsed
