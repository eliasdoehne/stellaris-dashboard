import enum
import logging
import zipfile
from collections import namedtuple

import time

logging.basicConfig(level=logging.INFO)

FilePosition = namedtuple("FilePosition", "line col")


class StellarisFileFormatError(Exception): pass


class TokenType(enum.Enum):
    BRACE_OPEN = "BR_OPEN"
    BRACE_CLOSE = "BR_CLOSE"
    EQUAL = "EQ"
    INTEGER = "INT"
    FLOAT = "FLOAT"
    STRING = "STR"
    QUOT_STRING = "Q_STR"
    EOF = "EOF"


class Token:
    TYPES = (TokenType.BRACE_OPEN,
             TokenType.BRACE_CLOSE,
             TokenType.EQUAL,
             TokenType.INTEGER,
             TokenType.FLOAT,
             TokenType.STRING,
             TokenType.QUOT_STRING,
             TokenType.EOF)
    LITERAL_TOKENS = {
        TokenType.FLOAT,
        TokenType.INTEGER,
        TokenType.STRING,
        TokenType.QUOT_STRING
    }
    expected_value_type = {
        TokenType.FLOAT: float,
        TokenType.INTEGER: int,
        TokenType.STRING: str,
        TokenType.QUOT_STRING: str
    }

    def __init__(self, token_type, value, pos):
        super(Token, self).__init__()
        self.token_type = token_type
        self.value = value
        self._validate()
        self.pos = pos
        logging.debug(f"        New Token: {self}")

    def _validate(self):
        if self.token_type not in Token.TYPES:
            raise StellarisFileFormatError(
                "Expected one of {} as type, received {}".format(Token.TYPES, self.token_type)
            )

        if self.token_type not in Token.LITERAL_TOKENS:
            if self.value is not None:
                raise StellarisFileFormatError(
                    "Unexpectedly received non-null value {} for token of type {}".format(
                        self.value, self.token_type
                    )
                )
        else:
            expected_type = Token.expected_value_type[self.token_type]
            if not isinstance(self.value, expected_type):
                raise StellarisFileFormatError(
                    "Unexpectedly received non-integer value {} for INT token".format(
                        self.value, self.token_type
                    )
                )
            if self.token_type == TokenType.QUOT_STRING:
                if self.value[0] != "\"" or self.value[-1] != "\"":
                    raise StellarisFileFormatError(
                        "Token of type {} received string value that does not start with \": {}".format(
                            self.token_type, self.value
                        )
                    )

    def __str__(self):
        return "[{}: {} {}]".format(
            self.pos,
            self.token_type,
            str(self.value) + " " if self.token_type in Token.LITERAL_TOKENS else ""
        )


class Tokenizer:
    def __init__(self, gamestate):
        self.gamestate = gamestate
        self.line_number = 0

    def stream(self):
        next_value = []
        currently_in_quoted_string = False
        for char in self.gamestate:
            if char == "\n":
                if next_value and not currently_in_quoted_string:
                    yield self._make_token_from_value_string_and_pos(next_value)
                    next_value = []
                self.line_number += 1
                continue
            if char == '"':
                next_value.append(char)
                if currently_in_quoted_string:
                    yield self._make_token_from_value_string_and_pos(next_value)
                    next_value = []
                currently_in_quoted_string = not currently_in_quoted_string
                continue

            elif currently_in_quoted_string:
                next_value.append(char)
                continue

            if char in [" ", "\t", "\n"]:
                # make a token from the current value if one exists:
                if next_value:
                    yield self._make_token_from_value_string_and_pos(next_value)
                    next_value = []
                else:
                    continue
            elif char in ["{", "}", "="]:
                if next_value:
                    yield self._make_token_from_value_string_and_pos(next_value)
                yield self._make_token_from_value_string_and_pos([char])
                next_value = []
            else:
                next_value.append(char)

        yield Token(TokenType.EOF, None, -1)

    def _make_token_from_value_string_and_pos(self, value):
        value = "".join(value)
        if not isinstance(value, str):
            raise ValueError("Invalid token value {}".format(value))

        token_type = None
        if value == "{":
            token_type = TokenType.BRACE_OPEN
            value = None
        elif value == "}":
            token_type = TokenType.BRACE_CLOSE
            value = None
        elif value == "=":
            token_type = TokenType.EQUAL
            value = None
        else:
            try:
                value = int(value)
                token_type = TokenType.INTEGER
                return Token(token_type, value, self.line_number)
            except ValueError:
                pass

            try:
                value = float(value)
                token_type = TokenType.FLOAT
                return Token(token_type, value, self.line_number)
            except ValueError:
                pass

            if token_type is None:
                if value.startswith("\""):
                    token_type = TokenType.QUOT_STRING
                else:
                    token_type = TokenType.STRING
            return Token(token_type, value, self.line_number)
        return Token(token_type, value, self.line_number)


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
        self.gamestate_dict = {}
        while self._lookahead().token_type != TokenType.EOF:
            key, value = self._parse_key_value_pair()
            logging.debug(f"Adding {key}  ->  {str(value)[:100]}")
            self._add_key_value_pair_or_convert_to_list(self.gamestate_dict, key, value)
        end_time = time.time()
        logging.info(f"Parsed save file in {end_time - start_time} seconds.")
        return self.gamestate_dict

    def _parse_key_value_pair(self):
        logging.debug("Key-Value Pair")
        key_token = self._next_token()
        if key_token.token_type not in {TokenType.STRING, TokenType.QUOT_STRING, TokenType.INTEGER}:
            raise StellarisFileFormatError(
                f"Line {key_token.pos}: Expected a string or Integer as key, found {key_token}"
            )
        key = key_token.value
        eq_token = self._next_token()
        if eq_token.token_type == TokenType.EQUAL:
            value = self._parse_value()
            return key, value
        else:
            raise StellarisFileFormatError(
                f"Line {eq_token.pos}: Expected = token, found {eq_token} (key was {key_token})"
            )

    def _parse_value(self):
        logging.debug("Value")
        next_token = self._lookahead()
        if next_token.token_type in Token.LITERAL_TOKENS:
            value = self._parse_literal()
        elif next_token.token_type == TokenType.BRACE_OPEN:
            value = self._parse_composite_game_object_or_list()
        else:
            raise StellarisFileFormatError(
                f"Line {next_token.pos}: Expected literal or {{ token for composite object or list, found {next_token}"
            )
        return value

    def _parse_composite_game_object_or_list(self):
        logging.debug("Composite object or list")
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
            elif next_token.token_type in Token.LITERAL_TOKENS or next_token.token_type == TokenType.BRACE_CLOSE:
                res = self._parse_object_list(token.value)
            else:
                res = None
        return res

    def _parse_key_value_pair_list(self, first_key_token):
        logging.debug("Key-Value pair list")
        eq_token = self._next_token()
        if eq_token.token_type != TokenType.EQUAL:
            raise StellarisFileFormatError(
                "Line {}: Expected =, found {}".format(eq_token.pos, eq_token)
            )

        next_token = self._lookahead()
        if next_token.token_type in Token.LITERAL_TOKENS:
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
            logging.debug(f"    {key} -> {value}")
            self._add_key_value_pair_or_convert_to_list(res, key, value)
            next_token = self._lookahead()
        self._next_token()
        return res

    def _parse_object_list(self, first_value=None):
        res = []
        if first_value is not None:
            res.append(first_value)
        next_token = self._lookahead()
        while next_token.token_type != TokenType.BRACE_CLOSE:
            logging.debug(f"   - {next_token}")
            if next_token.token_type in Token.LITERAL_TOKENS:
                res.append(self._parse_literal())
            elif next_token.token_type == TokenType.BRACE_OPEN:
                res = self._parse_composite_game_object_or_list()
            else:
                raise StellarisFileFormatError(
                    f"Expected  literal or composite object, got {next_token} token!"
                )
            next_token = self._lookahead()
        self._next_token()
        return res

    def _parse_literal(self):
        logging.debug("Literal")
        token = self._next_token()
        if token.token_type not in Token.LITERAL_TOKENS:
            raise StellarisFileFormatError(
                "Line {}: Expected literal, found {}".format(token.pos, token)
            )
        return token.value

    def _add_key_value_pair_or_convert_to_list(self, obj, key, value):
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
