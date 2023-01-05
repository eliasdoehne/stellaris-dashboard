import abc
import collections
import concurrent.futures
import enum
import itertools
import json
import logging
import multiprocessing as mp
import os
import pathlib
import signal
import sys
import time
import traceback
import typing
import zipfile
from collections import namedtuple
from typing import (
    Any,
    Dict,
    Tuple,
    Set,
    Iterable,
    List,
    TypeVar,
    Iterator,
    Deque,
    Optional,
)

from stellarisdashboard import config
from stellarisdashboard.parsing.tokenizer_re import INT, FLOAT

logger = logging.getLogger(__name__)
try:
    # try to load the cython-compiled C-extension
    from stellarisdashboard.parsing.cython_ext import tokenizer

except ImportError as import_error:
    logger.warning(
        f'Cython extensions not available, using slow parser. Error message: "{import_error}"'
    )
    from stellarisdashboard.parsing import tokenizer_re as tokenizer

FilePosition = namedtuple("FilePosition", "line col")


class StellarisFileFormatError(Exception):
    pass


T = TypeVar("T")


def m_or_c_time(f: pathlib.Path):
    stat = f.stat()
    return max(stat.st_mtime, stat.st_ctime)


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
            f for f in prefiltered_files if m_or_c_time(f) > self._last_checked_time
        )
        self._last_checked_time = time.time()
        return modified_files


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

    def get_gamestates_and_check_for_new_files(
        self,
    ) -> Iterable[Tuple[str, Optional[Dict[str, Any]]]]:
        while self._pending_results:
            # results should be returned in order => only yield results from the head of the queue
            if self._pending_results[0][1].ready():
                fname, result, submit_time = self._pending_results.popleft()
                logger.info(
                    f"Parsed save file {fname} in {time.time() - submit_time} seconds."
                )
                logger.info(f"Retrieving gamestate for {fname}")
                try:
                    yield fname.parent.stem, result.get()
                except KeyboardInterrupt:
                    raise
                except Exception:
                    logger.error(f"Error while reading save file {fname}:")
                    logger.error(traceback.format_exc())
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

    def get_gamestates_and_check_for_new_files(
        self,
    ) -> Iterable[Tuple[str, Optional[Dict[str, Any]]]]:
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
    import rust_parser

    def fix_dict(d):
        x = {}
        for k, v in d.items():
            try:
                x[int(k)] = v
            except:
                x[k] = v
        return x

    return json.loads(
        rust_parser.parse_save_file(str(filename.absolute())),
        object_hook=fix_dict,
    )

class TokenType(enum.Enum):
    BRACE_OPEN = enum.auto()
    BRACE_CLOSE = enum.auto()
    EQUAL = enum.auto()
    INTEGER = enum.auto()
    FLOAT = enum.auto()
    STRING = enum.auto()
    EOF = enum.auto()

    def is_literal(self):
        return (
            self == TokenType.STRING
            or self == TokenType.INTEGER
            or self == TokenType.FLOAT
        )


Token = namedtuple("Token", ["token_type", "value", "pos"])


def token_stream(gamestate, tokenizer=tokenizer.tokenizer):
    """
    Take each value obtained from the tokenizer and wrap it in an appropriate
    Token object.

    :param gamestate: Gamestate dictionary
    :param tokenizer: tokenizer function. Passed as an argument to allow either the cython one or the regex tokenizer
    :return:
    """
    for token_info in tokenizer(gamestate, debug=config.CONFIG.debug_mode):
        value, line_number = token_info
        if value == "=":
            yield Token(TokenType.EQUAL, "=", line_number)
        elif value == "}":
            yield Token(TokenType.BRACE_CLOSE, "}", line_number)
        elif value == "{":
            yield Token(TokenType.BRACE_OPEN, "{", line_number)
        else:
            token_type = None
            if INT.fullmatch(value):
                value = int(value)
                token_type = TokenType.INTEGER
            elif FLOAT.fullmatch(value):
                value = float(value)
                token_type = TokenType.FLOAT

            if token_type is None:
                value = value.strip('"')
                token_type = TokenType.STRING
            yield Token(token_type, value, line_number)
    yield Token(TokenType.EOF, None, -1)


def _track_nesting_depth(f) -> typing.Callable:
    """Decorator for tracking the nesting/recursion depth in the parser."""

    def inner(*args, **kwargs):
        self = args[0]
        self._current_nesting_depth += 1
        result = f(*args, **kwargs)
        self._current_nesting_depth -= 1
        return result

    return inner


class SaveFileParser:
    def __init__(self, max_nesting_depth=100):
        self.gamestate_dict = None
        self.save_filename = None
        self._token_stream = None
        self._lookahead_token = None
        self._current_nesting_depth = 0
        self.max_nesting_depth = max_nesting_depth

    def parse_save(self, filename):
        """
        Parse a single save file to a gamestate dictionary.

        Gamestate dictionaries are generally organized as follows:
        On the top level, there are some categories ("species", "leader", "war", "country" etc),
        which usually map either to lists of objects or a "dictionary". Each object may be a constant
        string or numeric value, or a composite object which can be represented as a dictionary.

        Sometimes in the game files, lists of objects are represented by duplicate keys in the dictionary,
        e.g.
        outer_key = {
            ...
            duplicate_key = 1,
            duplicate_key = 2,
            ...
        }
        In this case, the parser returns the object as the python dict
        {"outer_key": {"duplicate_key": [1, 2]}},
        collecting the values of the duplicate keys into a single list.

        :return: A dictionary representing the gamestate of the savefile.
        """
        self.save_filename = filename
        logger.info(f"Parsing Save File {self.save_filename}...")
        with zipfile.ZipFile(self.save_filename) as save_zip:
            gamestate = save_zip.read("gamestate")
            try:
                # default encoding guess based on EU4 wiki https://eu4.paradoxwikis.com/Save-game_editing#File_locations
                gamestate = gamestate.decode("cp1252")
            except UnicodeError:
                # attempt UTF-8, ignoring any further errors
                gamestate = gamestate.decode("utf-8", errors="ignore")
        self.parse_from_string(gamestate)
        return self.gamestate_dict

    def parse_from_string(self, s: str):
        if self._current_nesting_depth != 0:
            logger.warning("Found current_nesting_depth != 0, resetting it.")
        self._current_nesting_depth = 0
        self._token_stream = token_stream(s)
        self.gamestate_dict = self._parse_key_value_pair_list(self._next_token())
        return self.gamestate_dict

    def _parse_key_value_pair(self):
        key_token = self._lookahead()
        if key_token.token_type not in [
            TokenType.STRING,
            TokenType.INTEGER,
            TokenType.EQUAL,
        ]:
            raise StellarisFileFormatError(
                f"Line {key_token.pos}: Expected a string or Integer as key, found {key_token}"
            )
        if key_token.token_type == TokenType.EQUAL:
            # Workaround to handle this edge case/bug: "event_id=scope={" which happens for some ancient relics
            # Change it effectively to this: "event_id="scope" unknown_key={"
            key = "unknown_key"
        else:
            key = self._parse_literal()
        eq_token = self._next_token()
        if eq_token.token_type == TokenType.EQUAL:
            value = self._parse_value()
            return key, value
        else:
            raise StellarisFileFormatError(
                f"Line {eq_token.pos}: Expected = token, found {eq_token} (key was {key_token})"
            )

    def _parse_value(self):
        next_token = self._lookahead()
        if next_token.token_type.is_literal():
            value = self._parse_literal()
        elif next_token.token_type == TokenType.BRACE_OPEN:
            value = self._parse_composite_game_object_or_list()
        else:
            raise StellarisFileFormatError(
                f"Line {next_token.pos}: Expected literal or {{ token for composite object or list, found {next_token}"
            )
        return value

    @_track_nesting_depth
    def _parse_composite_game_object_or_list(self):
        brace = self._next_token()
        if brace.token_type != TokenType.BRACE_OPEN:
            raise StellarisFileFormatError(
                f"Line {brace.pos}: Expected {{ token, found {brace}"
            )
        if self._current_nesting_depth > self.max_nesting_depth:
            return self._skip_nested_object()

        tt = self._lookahead().token_type
        if (
            tt == TokenType.BRACE_OPEN
        ):  # indicates that this is a list since composite objects and lists cannot be keys
            result = self._parse_list()
        elif tt == TokenType.BRACE_CLOSE:  # immediate closing brace => empty list
            self._next_token()
            result = []
        else:
            token = self._next_token()
            next_token = self._lookahead()
            if next_token.token_type == TokenType.EQUAL:
                result = self._parse_key_value_pair_list(token)
            elif (
                next_token.token_type.is_literal()
                or next_token.token_type == TokenType.BRACE_CLOSE
                or next_token.token_type == TokenType.BRACE_OPEN
            ):
                result = self._parse_list(token.value)
            else:
                raise StellarisFileFormatError(f"Unexpected token: {next_token}")
        return result

    def _parse_key_value_pair_list(self, first_key_token):
        eq_token = self._next_token()
        if eq_token.token_type != TokenType.EQUAL:
            raise StellarisFileFormatError(
                f"Line {eq_token.pos}: Expected =, found {eq_token}"
            )

        next_token = self._lookahead()
        if next_token.token_type.is_literal():
            first_value = self._parse_literal()
        elif next_token.token_type == TokenType.BRACE_OPEN:
            first_value = self._parse_composite_game_object_or_list()
        else:
            raise StellarisFileFormatError(
                f"Line {next_token.pos}: Expected literal or {{, found {eq_token}"
            )
        result = {first_key_token.value: first_value}
        next_token = self._lookahead()
        handled_duplicate_keys = set()
        while (
            next_token.token_type != TokenType.BRACE_CLOSE
            and next_token.token_type != TokenType.EOF
        ):
            key, value = self._parse_key_value_pair()
            convert_to_list = key in result and key not in handled_duplicate_keys
            self._add_key_value_pair_or_convert_to_list(
                result, key, value, convert_to_list=convert_to_list
            )
            if convert_to_list:
                handled_duplicate_keys.add(key)
            next_token = self._lookahead()
        self._next_token()
        return result

    def _parse_list(self, first_value=None):
        result = []
        if first_value is not None:
            result.append(first_value)
        while self._lookahead().token_type != TokenType.BRACE_CLOSE:
            val = self._parse_value()
            result.append(val)
        self._next_token()  # consume the final }
        return result

    def _parse_literal(self):
        token = self._next_token()
        if not token.token_type.is_literal():
            raise StellarisFileFormatError(
                f"Line {token.pos}: Expected literal, found {token}"
            )
        return token.value

    @staticmethod
    def _add_key_value_pair_or_convert_to_list(obj, key, value, convert_to_list=False):
        if key in obj:
            if convert_to_list:
                obj[key] = [obj[key]]
            obj[key].append(value)
        else:
            assert not convert_to_list
            obj[key] = value

    def _lookahead(self) -> Token:
        if self._lookahead_token is None:
            self._lookahead_token = self._next_token()
        return self._lookahead_token

    def _next_token(self) -> Token:
        if self._lookahead_token is None:
            token = next(self._token_stream)
        else:
            token = self._lookahead_token
            self._lookahead_token = None
        return token

    def _skip_nested_object(self):
        logger.info(
            "Skipping deeply nested object, probably caused by another mod. Everything should still work."
        )
        brace_count = 1  # "{" token is parsed outside the function
        while brace_count > 0:
            t = self._next_token()
            if t.token_type == TokenType.BRACE_OPEN:
                brace_count += 1
            if t.token_type == TokenType.BRACE_CLOSE:
                brace_count -= 1

        return []
