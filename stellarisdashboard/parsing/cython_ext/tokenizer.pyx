#cython: language_level=3
from cpython cimport bool

def tokenizer(str gamestate, bool debug=False):
    cdef int token_start, end_index, current_position, line_number, len_gs
    cdef str c
    cdef bint in_word, in_quoted_string

    line_number = 1
    in_word = False
    in_quoted_string = False
    token_start = 0
    current_position = 0
    end_index = token_start + 1
    len_gs = len(gamestate)

    while current_position < len_gs:
        c = gamestate[current_position]
        #Second part checks if this quotation mark should be escaped.
        if c == '"' and gamestate[current_position - 1] != '\\':
            if in_quoted_string:  # Closing quote
                end_index = current_position + 1
                yield gamestate[token_start:end_index], line_number
                in_word = False
                token_start = end_index
            else:  # Opening quote
                in_word = True
                token_start = current_position
            in_quoted_string = not in_quoted_string
        elif in_quoted_string:
            pass
        elif c == " " or c == "\t" or c == "\n":
            if in_word:
                in_word = False
                end_index = current_position
                yield gamestate[token_start:end_index], line_number
                token_start = end_index + 1
            else:
                token_start += 1
            if debug and c == "\n":
                line_number += 1
        elif c == "{" or c == "}" or c == "=":
            if in_word:
                in_word = False
                end_index = current_position
                yield gamestate[token_start:end_index], line_number
            yield gamestate[current_position:current_position + 1], line_number
            token_start = current_position + 1
        else:
            in_word = True
        current_position += 1

    if in_word:
        yield gamestate[token_start:len_gs], line_number
