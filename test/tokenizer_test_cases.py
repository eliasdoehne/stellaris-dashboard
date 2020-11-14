VALID_TOKEN_SEQUENCES = [
    ("", []),
    # Single tokens
    ("{", [("{", 1)]),
    ("=", [("=", 1)]),
    ("}", [("}", 1)]),
    ("=", [("=", 1)]),
    ("foo", [("foo", 1)]),
    ("123", [("123", 1)]),
    ("3.141", [("3.141", 1)]),
    ('"quoted string"', [('"quoted string"', 1)]),
    # escaped strings: (Need two \\ to make a single backslash literal in python)
    (f'"qstr \\"with\\" escaped quotes"', [(f'"qstr \\"with\\" escaped quotes"', 1)]),
    (
        f'"qstr \\"with\\" escaped quotes and \nnewline"',
        [(f'"qstr \\"with\\" escaped quotes and \nnewline"', 1)],
    ),
    # Simple token sequences:
    ("pi=3.141", [("pi", 1), ("=", 1), ("3.141", 1)]),
    ("empty={}", [("empty", 1), ("=", 1), ("{", 1), ("}", 1)]),
    (
        "empty_with_linebreak={\n}",
        [("empty_with_linebreak", 1), ("=", 1), ("{", 1), ("}", 2)],
    ),
    # non-empty object
    (
        "obj={\nx=1 y=2\n}",
        [
            ("obj", 1),
            ("=", 1),
            ("{", 1),
            ("x", 2),
            ("=", 2),
            ("1", 2),
            ("y", 2),
            ("=", 2),
            ("2", 2),
            ("}", 3),
        ],
    ),
    # non-empty object with weird whitespace
    (
        "obj =  {\t\nx\t=\t \t1 \t \t\t\n\t\t\ty\t \t=\t \t2\n}\t",
        [
            ("obj", 1),
            ("=", 1),
            ("{", 1),
            ("x", 2),
            ("=", 2),
            ("1", 2),
            ("y", 3),
            ("=", 3),
            ("2", 3),
            ("}", 4),
        ],
    ),
    # qstring containing other tokens
    ('"qstr with {=}0 1.0 other tokens"', [('"qstr with {=}0 1.0 other tokens"', 1)]),
]
