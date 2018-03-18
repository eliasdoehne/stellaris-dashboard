import enum
import logging
import zipfile
from collections import namedtuple

import pathlib
import time
import multiprocessing as mp
from typing import Any, Dict, Tuple, Set

logger = logging.getLogger(__name__)
try:
    import pyximport

    pyximport.install()
    from stellarisdashboard.cython_ext import token_value_stream
except ImportError as e:
    logger.info(f"Cython-error \"{e}\" occurred, using slow parser")
    from stellarisdashboard import token_value_stream_re as token_value_stream

FilePosition = namedtuple("FilePosition", "line col")


class StellarisFileFormatError(Exception): pass


DEBUG = True
if DEBUG:
    logger.info("Running in Debug mode...")


class SavePathMonitor:
    """
    Check the save path for new save games. Found save files are parsed and returned
    as gamestate dictionaries.
    """

    def __init__(self, save_parent_dir, threads=None, game_name_prefix: str = ""):
        self.processed_saves: Set[pathlib.Path] = set()
        self.save_parent_dir = pathlib.Path(save_parent_dir)
        if threads is None:
            threads = max(1, mp.cpu_count() - 2)
        self.threads = threads
        self.game_name_prefix = game_name_prefix
        self.work_pool = None
        if self.threads > 1:
            mp.freeze_support()
            self.work_pool = mp.Pool(threads)
            logger.debug("Initialized parsing worker pool")

    def check_for_new_saves(self) -> Tuple[str, Dict[str, Any]]:
        logger.debug("Checking for new saves")
        new_files = self.valid_save_files()
        self.processed_saves.update(new_files)
        if new_files:
            new_files_str = ", ".join(f.stem for f in new_files[:10])
            logger.debug(f"Found {len(new_files)} new files: {new_files_str}...")
        if self.threads > 1:
            mp.freeze_support()
            results = [(f.parent.stem, self.work_pool.apply_async(parse_save, (f,))) for f in new_files]
            while results:
                for game_name, r in results:
                    if r.ready():
                        if r.successful():
                            yield game_name, r.get()
                        else:
                            try:
                                r.get()
                            except Exception as e:
                                logger.error("Parsing error:")
                                logger.error(e)
                        results.remove((game_name, r))
                time.sleep(0.3)
        else:
            for save_file in new_files:
                yield save_file.parent.stem, parse_save(save_file)

    def mark_all_existing_saves_processed(self):
        self.processed_saves |= set(self.valid_save_files())

    def apply_matching_prefix(self, game_name_prefix: str):
        self.mark_all_existing_saves_processed()
        whitelisted = set()
        for fname in self.processed_saves:
            if fname.parent.stem.startswith(game_name_prefix):
                whitelisted.add(fname)
        self.processed_saves -= whitelisted

    def valid_save_files(self):
        return sorted(save_file for save_file in self.save_parent_dir.glob("**/*.sav")
                      if save_file not in self.processed_saves
                      and "ironman" not in str(save_file)
                      and not str(save_file.parent.stem).startswith("mp")
                      and str(save_file.parent.stem).startswith(self.game_name_prefix))

    def teardown(self):
        if self.threads > 1:
            self.work_pool.close()
            self.work_pool.join()


class TokenType(enum.Enum):
    BRACE_OPEN = enum.auto()
    BRACE_CLOSE = enum.auto()
    EQUAL = enum.auto()
    INTEGER = enum.auto()
    FLOAT = enum.auto()
    STRING = enum.auto()
    EOF = enum.auto()

    @staticmethod
    def is_literal(token_type):
        return token_type == TokenType.STRING or token_type == TokenType.INTEGER or token_type == TokenType.FLOAT


Token = namedtuple("Token", ["token_type", "value", "pos"])


def token_stream(gamestate, tokenizer=token_value_stream.token_value_stream):
    for token_info in tokenizer(gamestate, DEBUG):
        if isinstance(token_info, tuple):
            value, line_number = token_info
        else:
            value = token_info
            line_number = 0
        if value == "=":
            yield Token(TokenType.EQUAL, "=", line_number)
        elif value == "}":
            yield Token(TokenType.BRACE_CLOSE, "}", line_number)
        elif value == "{":
            yield Token(TokenType.BRACE_OPEN, "{", line_number)
        else:
            token_type = None
            if value[0].isdigit():
                try:
                    value = int(value)
                    token_type = TokenType.INTEGER
                except ValueError:
                    try:
                        value = float(value)
                        token_type = TokenType.FLOAT
                    except ValueError:
                        pass

            if token_type is None:
                value = value.strip('"')
                token_type = TokenType.STRING
            yield Token(token_type, value, line_number)
    yield Token(TokenType.EOF, None, line_number)


class SaveFileParser:
    """
    Parse the contents of the gamestate file to a dictionary.
    """

    def __init__(self, filename):
        self.gamestate_dict = None
        self.save_filename = filename
        self._token_stream = None
        self._lookahead_token = None

    def parse_save(self):
        logging.info(f"Parsing Save File {self.save_filename}...")
        start_time = time.time()
        with zipfile.ZipFile(self.save_filename) as save_zip:
            gamestate = save_zip.read("gamestate").decode()
        self._token_stream = token_stream(gamestate)
        self._parse_save()
        end_time = time.time()
        logging.info(f"Parsed save file in {end_time - start_time} seconds.")
        print(f"Parsed save file in {end_time - start_time} seconds.")
        return self.gamestate_dict

    def _parse_save(self):
        self.gamestate_dict = {}
        while self._lookahead().token_type != TokenType.EOF:
            key, value = self._parse_key_value_pair()
            self._add_key_value_pair_or_convert_to_list(self.gamestate_dict, key, value)

    def _parse_key_value_pair(self):
        key_token = self._lookahead()
        if key_token.token_type != TokenType.STRING and key_token.token_type != TokenType.INTEGER:
            raise StellarisFileFormatError(
                f"Line {key_token.pos}: Expected a string or Integer as key, found {key_token}"
            )
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
        if TokenType.is_literal(next_token.token_type):
            value = self._parse_literal()
        elif next_token.token_type == TokenType.BRACE_OPEN:
            value = self._parse_composite_game_object_or_list()
        else:
            raise StellarisFileFormatError(
                f"Line {next_token.pos}: Expected literal or {{ token for composite object or list, found {next_token}"
            )
        return value

    def _parse_composite_game_object_or_list(self):
        brace = self._next_token()
        if brace.token_type != TokenType.BRACE_OPEN:
            raise StellarisFileFormatError(
                "Line {}: Expected {{ token, found {}".format(brace.pos, brace)
            )
        tt = self._lookahead().token_type
        if tt == TokenType.BRACE_OPEN or tt == TokenType.BRACE_CLOSE:  # the first indicates a list of objects (which cannot be keys), the second an empty list
            res = self._parse_object_list()
        else:
            token = self._next_token()
            next_token = self._lookahead()
            if next_token.token_type == TokenType.EQUAL:
                res = self._parse_key_value_pair_list(token)
            elif TokenType.is_literal(next_token.token_type) or next_token.token_type == TokenType.BRACE_CLOSE:
                res = self._parse_object_list(token.value)
            else:
                res = None
        return res

    def _parse_key_value_pair_list(self, first_key_token):
        eq_token = self._next_token()
        if eq_token.token_type != TokenType.EQUAL:
            raise StellarisFileFormatError(
                "Line {}: Expected =, found {}".format(eq_token.pos, eq_token)
            )

        next_token = self._lookahead()
        if TokenType.is_literal(next_token.token_type):
            first_value = self._parse_literal()
        elif next_token.token_type == TokenType.BRACE_OPEN:
            first_value = self._parse_composite_game_object_or_list()
        else:
            raise StellarisFileFormatError(
                f"ERROR"
            )
        res = {first_key_token.value: first_value}
        next_token = self._lookahead()
        while next_token.token_type != TokenType.BRACE_CLOSE:
            key, value = self._parse_key_value_pair()
            self._add_key_value_pair_or_convert_to_list(res, key, value)
            next_token = self._lookahead()
        self._next_token()
        return res

    def _parse_object_list(self, first_value=None):
        res = []
        if first_value is not None:
            res.append(first_value)
        while self._lookahead().token_type != TokenType.BRACE_CLOSE:
            val = self._parse_value()
            res.append(val)
        self._next_token()
        return res

    def _parse_literal(self):
        token = self._next_token()
        if not TokenType.is_literal(token.token_type):
            raise StellarisFileFormatError(
                "Line {}: Expected literal, found {}".format(token.pos, token)
            )
        return token.value

    @staticmethod
    def _add_key_value_pair_or_convert_to_list(obj, key, value):
        if key not in obj:
            obj[key] = value
        else:
            existing_value = obj[key]
            if isinstance(existing_value, list):
                existing_value.append(value)
            else:
                obj[key] = [obj[key], value]

    def _lookahead(self) -> Token:
        if self._lookahead_token is None:
            self._lookahead_token = self._next_token()
        return self._lookahead_token

    def _next_token(self) -> Token:
        if self._lookahead_token is None:
            res = next(self._token_stream)
        else:
            res = self._lookahead_token
            self._lookahead_token = None
        return res


def parse_save(filename):
    return SaveFileParser(filename).parse_save()
