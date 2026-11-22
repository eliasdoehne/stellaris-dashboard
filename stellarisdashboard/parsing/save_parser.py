import concurrent.futures
import itertools
import logging
import pathlib
import sys
import time
from typing import (
    Any,
    Dict,
    Set,
    List,
    Iterator,
    Optional,
    TypeVar,
)

import rust_parser

from stellarisdashboard import config
from stellarisdashboard.parsing import save_copies

logger = logging.getLogger(__name__)

# the default recursion limit was not high enough for pickling some parsed saves (pickle used by futures)
if sys.getrecursionlimit() < 2000:
    sys.setrecursionlimit(2000)

class StellarisFileFormatError(Exception):
    pass


T = TypeVar("T")


class SavePathMonitor:
    """
    Base class for path monitors, which check the save path for new save games.
    """

    def __init__(self, save_parent_dir, game_name_prefix: str = ""):
        self.processed_saves: Set[pathlib.Path] = set()
        self.num_encountered_saves: int = 0
        self.save_parent_dir = pathlib.Path(save_parent_dir)
        self.game_name_prefix = game_name_prefix
        self._last_checked_time = float("-inf")

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
        self.processed_saves |= {
            f for f in self._valid_save_files() if f.stem != "ironman"
        }
        self._last_checked_time = time.time()

    def _valid_save_files(self) -> List[pathlib.Path]:
        prefiltered_files = (
            save_file
            for save_file in self.save_parent_dir.glob("**/*.sav")
            if save_file not in self.processed_saves
            and str(save_file.parent.stem).startswith(self.game_name_prefix)
        )
        modified_files = sorted(
            f
            for f in prefiltered_files
            if self.m_or_c_time(f) > self._last_checked_time
        )
        self._last_checked_time = time.time()
        return modified_files

    @staticmethod
    def m_or_c_time(f: pathlib.Path):
        stat = f.stat()
        return max(stat.st_mtime, stat.st_ctime)


class ContinuousSavePathMonitor(SavePathMonitor):
    """
    SavePathMonitor implementation for the default execution: reports the new save files of the
    save path. The import worker process parses and imports them (cli.f_monitor_saves).
    """

    def get_new_saves(self) -> List[pathlib.Path]:
        """
        All new save files in the order in which they should be imported, after the skip_saves
        filter. Each is reported once: it counts as processed from now on, whatever happens to it.
        """
        new_files = self.get_new_savefiles()
        self.processed_saves.update(f for f in new_files if f.stem != "ironman")
        return new_files


class BatchSavePathMonitor(SavePathMonitor):
    """
    SavePathMonitor implementation for parsing large numbers of saves with
    the CLI command `stellarisdashboardcli --parse-saves` or the import page.

    :param save_files: Parse exactly these files, in this order, instead of searching the save path.
    :param use_worker_processes: Parse in worker processes even with a single thread; by default
        this happens only if more than one thread is configured.
    """

    def __init__(
        self,
        save_parent_dir,
        game_name_prefix: str = "",
        save_files: Optional[List[pathlib.Path]] = None,
        use_worker_processes: bool = False,
    ):
        super().__init__(save_parent_dir, game_name_prefix)
        self._save_files = save_files
        self._use_worker_processes = use_worker_processes

    def get_new_savefiles(self) -> List[pathlib.Path]:
        if self._save_files is not None:
            return list(self._save_files)
        return super().get_new_savefiles()

    def get_gamestates_and_check_for_new_files(self):
        """
        Check the save directory for new files. If any are found, parse them and
        return the results as gamestate dictionaries as they come in. A save that
        cannot be parsed is logged, kept as a failed save and yielded with a gamestate of None.

        Files are processed in chunks to avoid holding too many gamestate dicts in
        memory at a time.

        :return:
        """
        new_files = self.get_new_savefiles()
        if self._use_worker_processes or (config.CONFIG.threads > 1 and len(new_files) > 1):
            all_game_ids = [f.parent.stem for f in new_files]
            chunksize = min(16, int(2 * config.CONFIG.threads))
            # one pool for the whole batch; submitting in chunks still bounds
            # how many parsed gamestate dicts are held in memory at a time
            executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=config.CONFIG.threads
            )
            try:
                for chunk in BatchSavePathMonitor.split_into_chunks(
                    zip(all_game_ids, new_files), chunksize
                ):
                    chunk_game_ids, chunk_files = zip(*chunk)
                    futures = [
                        executor.submit(parse_save, save_file)
                        for save_file in chunk_files
                    ]
                    for i, (game_id, save_file) in enumerate(zip(chunk_game_ids, chunk_files)):
                        result = _get_parse_result(save_file, futures[i].result)
                        futures[i] = None
                        yield game_id, result, save_file
            finally:
                # also runs when the caller stops early: drop the queued parses
                executor.shutdown(wait=True, cancel_futures=True)
        else:
            for save_file in new_files:
                yield save_file.parent.stem, _get_parse_result(
                    save_file, lambda: parse_save(save_file)
                ), save_file
        self.processed_saves.update(f for f in new_files if f.stem != "ironman")

    @staticmethod
    def split_into_chunks(iterable: Iterator[T], chunksize: int) -> Iterator[List[T]]:
        while iterable:
            chunk = list(itertools.islice(iterable, chunksize))
            if not chunk:
                break
            yield chunk


def _get_parse_result(save_file: pathlib.Path, get_result) -> Optional[Dict[str, Any]]:
    """Return the parsed gamestate, or None (after keeping a copy of the save) if parsing failed."""
    try:
        return get_result()
    except KeyboardInterrupt:
        raise
    except Exception as e:
        logger.exception(f"Error while reading save file {save_file}:")
        save_copies.keep_failed_save(
            save_file,
            save_file.parent.stem,
            [save_copies.failure(save_copies.STAGE_PARSE, save_copies.format_exception(e))],
        )
        if config.CONFIG.debug_mode:
            raise
        return None


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
