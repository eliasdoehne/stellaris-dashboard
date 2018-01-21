import enum
import logging
import time
import zipfile
from collections import namedtuple

FilePosition = namedtuple("FilePosition", "line col")


class StellarisFileFormatError(Exception): pass


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


class Tokenizer:
    def __init__(self, gamestate):
        self.gamestate = gamestate
        self.line_number = 0

    def stream(self):
        next_token_start = 0
        in_word = False
        currently_in_quoted_string = False
        token_maker = self._make_token_from_start_and_end_pos
        for current_position, char in enumerate(self.gamestate):
            if char == '"':
                if currently_in_quoted_string:  # Closing quote
                    yield token_maker(next_token_start, current_position + 1)
                    in_word = False
                else:  # Opening quote
                    if in_word:
                        yield token_maker(next_token_start, current_position)
                    in_word = True
                    next_token_start = current_position
                currently_in_quoted_string = not currently_in_quoted_string
            elif currently_in_quoted_string:
                pass
            elif char == " " or char == "\t" or char == "\n":
                # make a token from the preceding value if one exists:
                if in_word:
                    yield token_maker(next_token_start, current_position)
                    in_word = False
                self.line_number += (char == "\n")
            elif char == "{" or char == "}" or char == "=":
                # first make a token from the preceding value if one exists:
                if in_word:
                    yield token_maker(next_token_start, current_position)
                    in_word = False
                yield token_maker(current_position, current_position + 1)
            else:
                if not in_word:
                    in_word = True
                    next_token_start = current_position
        yield Token(TokenType.EOF, None, -1)

    # def get_next_token(self, start_index):

    def _make_token_from_start_and_end_pos(self, start, end):
        value = self.gamestate[start:end]
        if not value:
            raise ValueError(f"Received empty token value {value}")

        token_type = None
        if value == "{":
            token_type = TokenType.BRACE_OPEN
        elif value == "}":
            token_type = TokenType.BRACE_CLOSE
        elif value == "=":
            token_type = TokenType.EQUAL
        else:
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
        token = Token(token_type, value, self.line_number)
        # print(f"L {self.line_number}  {start} - {end}  {token}")
        return token


class SaveFileParser:
    """
    Parse an extracted gamestate file to a nested dictionary. 
    The grammar is as follows:
    
    save      :== kv-pair save | EOF
    kv-pair   :== key EQ value
    key       :== STR  |  Q_STR  |  INTEGER
    value     :== literal
                | BOPEN obj-list
    obj-list  :== value obj-list'
                | key kv-list
                | BCLOSE
    obj-list' :== value obj-list' | BCLOSE
    kv-list   :== EQ value kv-list'
    kv-list'  :== kv-pair kv-list' | BCLOSE
    literal   :== Q_STR | INTEGER | FLOAT
    """

    def __init__(self, filename):
        self._nesting_level = 0
        self.gamestate_dict = None
        self.save_filename = filename
        self.tokenizer = None
        self._token_stream = None
        self._lookahead_token = None

    def parse_save(self):
        logging.info(f"Parsing Save File {self.save_filename}...")
        start_time = time.time()
        with zipfile.ZipFile(self.save_filename) as save_zip:
            gamestate = save_zip.read("gamestate").decode()
        self.tokenizer = Tokenizer(gamestate)
        self._token_stream = self.tokenizer.stream()
        self._parse_save()
        end_time = time.time()
        logging.info(f"Parsed save file in {end_time - start_time} seconds.")
        return self.gamestate_dict

    def _parse_save(self):
        self.gamestate_dict = {}
        while self._lookahead().token_type != TokenType.EOF:
            key, value = self._parse_key_value_pair()
            # logging.debug(f"Adding {key}  ->  {str(value)[:100]}")
            self._add_key_value_pair_or_convert_to_list(self.gamestate_dict, key, value)

    def _parse_key_value_pair(self):
        # logging.debug("Key-Value Pair")
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
        # logging.debug("Value")
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
        # logging.debug("Composite object or list")
        brace = self._next_token()
        if brace.token_type != TokenType.BRACE_OPEN:
            raise StellarisFileFormatError(
                "Line {}: Expected {{ token, found {}".format(brace.pos, brace)
            )
        if self._lookahead().token_type in [TokenType.BRACE_OPEN, TokenType.BRACE_CLOSE]:
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
        # logging.debug("Key-Value pair list")
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
        # logging.debug("Literal")
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

    def print_token_stream(self):
        for t in self._token_stream:
            print(t)

    def _lookahead(self):
        if self._lookahead_token is None:
            self._lookahead_token = self._next_token()
        return self._lookahead_token

    def _next_token(self):
        if self._lookahead_token is None:
            res = next(self._token_stream)
        else:
            res = self._lookahead_token
            self._lookahead_token = None
        return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    with open("data/test_dummy_save.sav", "r") as f:
        dummy_gamestate = f.read()
    parser = SaveFileParser(None)
    parser.tokenizer = Tokenizer(dummy_gamestate)
    parser._token_stream = parser.tokenizer.stream()
    parser._parse_save()
    print(parser.gamestate_dict)
