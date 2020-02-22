import re

WHITESPACE = re.compile(r"[\t\n ]*")
EQ = re.compile(r"=")
BR_OPEN = re.compile(r"{")
BR_CLOSE = re.compile(r"}")
Q_STR = re.compile(r'"((\\")|[^"])*"')
IDENTIFIER = re.compile(r"[0-9_]*[a-zA-Z_]+[a-zA-Z0-9_:]*")
FLOAT = re.compile(r"-?[0-9]+\.[0-9]*")
INT = re.compile(r"-?[0-9]+")

REG_EXES = [
    BR_OPEN,
    BR_CLOSE,
    EQ,
    Q_STR,
    IDENTIFIER,
    FLOAT,
    INT,
]


def tokenizer(gamestate: str, debug=False):
    N = len(gamestate)
    start_index = 0
    line_number = 1
    while start_index < N:
        m_ws = WHITESPACE.match(gamestate, pos=start_index)
        if m_ws:
            match_str = m_ws.group(0)
            if debug:
                for c in match_str:
                    if c == "\n":
                        line_number += 1
            start_index += len(match_str)
        for regex in REG_EXES:
            match = regex.match(gamestate, pos=start_index)
            if match:
                match_str = match.group(0)
                yield match_str, line_number
                start_index += len(match_str)
                break
