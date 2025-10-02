import abc
import collections
import concurrent.futures
import itertools
import logging
import multiprocessing as mp
import os
import pathlib
import signal
import sys
import time
from typing import (
    Any,
    Dict,
    Tuple,
    Set,
    Iterable,
    List,
    Iterator,
    Deque,
    Optional,
    TypeVar,
)

import rust_parser

from stellarisdashboard import config

logger = logging.getLogger(__name__)

# the default recursion limit was not high enough for pickling some parsed saves (pickle used by futures)
if sys.getrecursionlimit() < 2000:
    sys.setrecursionlimit(2000)

class StellarisFileFormatError(Exception):
    pass


T = TypeVar("T")


class SavePathMonitor(abc.ABC):
    """
    Base class for path monitors, which check the save path for new save games.
    Save files are parsed and returned as gamestate dictionaries by calling the
    get_new_game_states method.
    """

    def __init__(self, save_parent_dir, game_name_prefix: str = ""):
        self.processed_saves: Set[pathlib.Path] = set()
        self.num_encountered_saves: int = 0
        self.save_parent_dir = pathlib.Path(save_parent_dir)
        self.game_name_prefix = game_name_prefix
        self._last_checked_time = float("-inf")

    @abc.abstractmethod
    def get_gamestates_and_check_for_new_files(
        self,
    ) -> Iterable[Tuple[str, Optional[Dict[str, Any]]]]:
        """
        Check the save path for new save files and yield results that are ready. Depending on the implementation,
        it files may be skipped if all parser threads are busy. Results are always returned in the correct order.

        :return: Iterator over (game_name, gamestate) pairs
        """
        pass

    def get_new_savefiles(self) -> List[pathlib.Path]:
        """Get a list of all new, unfiltered save files."""
        new_files = self._valid_save_files()
        new_files = self._apply_filename_filter(new_files)
        new_files = self._apply_skip_savefiles_filter(new_files)
        return new_files

    @staticmethod
    def _apply_filename_filter(new_files: List[pathlib.Path]) -> List[pathlib.Path]:
        if new_files:
            unfiltered_count = len(new_files)
            filter_string = config.CONFIG.save_name_filter
            if filter_string:
                new_files = [
                    f
                    for (i, f) in enumerate(new_files)
                    if f.stem.lower().find(filter_string.lower()) >= 0
                ]
            if filter_string:
                logger.info(
                    f'Applying filename filter: "{config.CONFIG.save_name_filter}", reduced from {unfiltered_count} to {len(new_files)} files.'
                )
        return new_files

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


def _pool_worker_init():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    sys.stdout = open(os.devnull, "w")


class ContinuousSavePathMonitor(SavePathMonitor):
    """
    SavePathMonitor implementation for the default execution. Saves are processed as quickly as possible,
    with the tradeoff that occasionally some save files may be skipped if all threads are busy.
    (unlikely during most normal gameplay if 2-3 threads are allowed)
    """

    def __init__(self, save_parent_dir, game_name_prefix: str = ""):
        super().__init__(save_parent_dir, game_name_prefix)
        self._num_threads = config.CONFIG.threads
        self._pool = mp.Pool(
            processes=config.CONFIG.threads, initializer=_pool_worker_init
        )
        self._pending_results: Deque[
            Tuple[pathlib.PurePath, mp.pool.AsyncResult, float]
        ] = collections.deque()

    def get_gamestates_and_check_for_new_files(self):
        while self._pending_results:
            # results should be returned in order => only yield results from the head of the queue
            if self._pending_results[0][1].ready():
                fname, result, submit_time = self._pending_results.popleft()
                try:
                    yield fname.parent.stem, result.get()
                except KeyboardInterrupt:
                    raise
                except Exception:
                    logger.exception(f"Error while reading save file {fname}:")
            else:
                break

        # Fill the queue with new files
        new_files = self.get_new_savefiles()
        for fname in new_files:
            if len(self._pending_results) >= config.CONFIG.threads:
                break  # Ignore if there are any additional files
            result = self._pool.apply_async(parse_save, args=(fname,))
            submit_time = time.time()
            self._pending_results.append((fname, result, submit_time))

        self.processed_saves.update(f for f in new_files if f.stem != "ironman")

    def shutdown(self):
        self._pool.terminate()
        self._pool.join()


class BatchSavePathMonitor(SavePathMonitor):
    """
    SavePathMonitor implementation for parsing large numbers of saves with
    the CLI command `stellarisdashboardcli --parse-saves`.
    """

    def get_gamestates_and_check_for_new_files(self):
        """
        Check the save directory for new files. If any are found, parse them and
        return the results as gamestate dictionaries as they come in.

        Files are processed in chunks to avoid holding too many gamestate dicts in
        memory at a time.

        :return:
        """
        new_files = self.get_new_savefiles()
        if config.CONFIG.threads > 1 and len(new_files) > 1:
            all_game_ids = [f.parent.stem for f in new_files]
            chunksize = min(16, int(2 * config.CONFIG.threads))
            for chunk in BatchSavePathMonitor.split_into_chunks(
                zip(all_game_ids, new_files), chunksize
            ):
                chunk_game_ids, chunk_files = zip(*chunk)
                with concurrent.futures.ProcessPoolExecutor(
                    max_workers=config.CONFIG.threads
                ) as executor:
                    futures = [
                        executor.submit(parse_save, save_file)
                        for save_file in chunk_files
                    ]
                    for i, (game_id, future) in enumerate(zip(chunk_game_ids, futures)):
                        result = future.result()
                        yield game_id, result
                        futures[i] = None
        else:
            for save_file in new_files:
                yield save_file.parent.stem, parse_save(save_file)
        self.processed_saves.update(f for f in new_files if f.stem != "ironman")

    @staticmethod
    def split_into_chunks(iterable: Iterator[T], chunksize: int) -> Iterator[List[T]]:
        while iterable:
            chunk = list(itertools.islice(iterable, chunksize))
            if not chunk:
                break
            yield chunk


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
