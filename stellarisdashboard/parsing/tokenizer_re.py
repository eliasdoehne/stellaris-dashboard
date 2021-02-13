import re

from stellarisdashboard.parsing.errors import StellarisFileFormatError

WHITESPACE = re.compile(r"[\t\n ]+")
EQ_OR_BR = re.compile(r"[={}]")
Q_STR = re.compile(r'"((\\")|[^"])*"')
IDENTIFIER = re.compile(r"[a-zA-Z0-9_:.]+")
FLOAT = re.compile(r"-?[0-9]+\.[0-9]*")
INT = re.compile(r"-?[0-9]+")

REG_EXES = [
    EQ_OR_BR,
    Q_STR,
    IDENTIFIER,  # Match the largest possible string first, the invoker of tokenizer will retest subsets INT and FLOAT for exact matches
    FLOAT,
    INT,
]

def tokenizer(gamestate: str, debug=False):
    global line_number
    N = len(gamestate)
    start_index = 0
    line_number = 1
    while start_index < N:
        last_start_index = start_index
        m_ws = WHITESPACE.match(gamestate, pos=start_index)
        if m_ws:
            match_str = m_ws.group(0)
            line_number += match_str.count("\n")
            start_index += len(match_str)
        for regex in REG_EXES:
            match = regex.match(gamestate, pos=start_index)
            if match:
                match_str = match.group(0)
                yield match_str, line_number
                start_index += len(match_str)
                break
        if last_start_index == start_index:
            end = start_index + 50 if start_index + 50 < N else N
            raise StellarisFileFormatError(f'Stuck looking for next token at offset {start_index} [{gamestate[start_index:end]}]')
