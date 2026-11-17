"""Record which parts of the parsed gamestate the import pipeline actually reads.

The parsed dict is rebuilt from dict / list subclasses that log every access, and is then imported
through the normal pipeline (cli.f_import_gamestate) into a throw-away database.

Output (JSON): {"accessed": [paths], "whole": [paths]} where a path is the `/`-joined key path with
integer keys written as `*` and list levels as `[]` (the same notation as rust_parser.save_file_stats).
"accessed" paths were handed out to the pipeline; "whole" paths were used in a way that needs their
entire subtree (comparison, repr, mutation).

This is a dynamic trace: code paths that the traced saves do not trigger are not seen, so the result
is a lower bound of what the pipeline reads.

    uv run tools/perf/trace_access.py OUT.json SAVE [SAVE ...]
"""

import json
import pathlib
import sys
import tempfile

ACCESSED = set()
WHOLE = set()


def _child(path, key):
    return path + ("/*" if isinstance(key, int) else "/" + str(key))


class TDict(dict):
    __slots__ = ("_p",)

    def _all(self):
        p = self._p
        ACCESSED.update({_child(p, k) for k in dict.keys(self)})

    def __getitem__(self, key):
        ACCESSED.add(_child(self._p, key))
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        ACCESSED.add(_child(self._p, key))
        return dict.get(self, key, default)

    def __contains__(self, key):
        ACCESSED.add(_child(self._p, key))
        return dict.__contains__(self, key)

    def __iter__(self):
        self._all()
        return dict.__iter__(self)

    def keys(self):
        self._all()
        return dict.keys(self)

    def values(self):
        self._all()
        return dict.values(self)

    def items(self):
        self._all()
        return dict.items(self)

    def __len__(self):
        self._all()
        return dict.__len__(self)

    def _whole(self):
        WHOLE.add(self._p)

    def __eq__(self, other):
        self._whole()
        return dict.__eq__(self, other)

    def __ne__(self, other):
        self._whole()
        return dict.__ne__(self, other)

    __hash__ = None

    def __repr__(self):
        self._whole()
        return dict.__repr__(self)

    def copy(self):
        self._whole()
        return dict.copy(self)

    def pop(self, *a):
        self._whole()
        return dict.pop(self, *a)

    def setdefault(self, *a):
        self._whole()
        return dict.setdefault(self, *a)

    def update(self, *a, **k):
        self._whole()
        return dict.update(self, *a, **k)

    def __or__(self, other):
        self._whole()
        return dict.__or__(self, other)

    def __ror__(self, other):
        self._whole()
        return dict.__ror__(self, other)

    def __reduce_ex__(self, protocol):
        self._whole()
        return dict.__reduce_ex__(self, protocol)


class TList(list):
    __slots__ = ("_p",)

    def _touch(self):
        ACCESSED.add(self._p + "/[]")

    def __getitem__(self, i):
        self._touch()
        return list.__getitem__(self, i)

    def __iter__(self):
        self._touch()
        return list.__iter__(self)

    def __reversed__(self):
        self._touch()
        return list.__reversed__(self)

    def __len__(self):
        self._touch()
        return list.__len__(self)

    def __contains__(self, x):
        self._touch()
        return list.__contains__(self, x)

    def index(self, *a):
        self._touch()
        return list.index(self, *a)

    def count(self, *a):
        self._touch()
        return list.count(self, *a)

    def _whole(self):
        WHOLE.add(self._p)

    def __eq__(self, other):
        self._whole()
        return list.__eq__(self, other)

    def __ne__(self, other):
        self._whole()
        return list.__ne__(self, other)

    __hash__ = None

    def __repr__(self):
        self._whole()
        return list.__repr__(self)

    def __add__(self, other):
        self._whole()
        return list.__add__(self, other)

    def __radd__(self, other):
        self._whole()
        return other + list(self)

    def copy(self):
        self._whole()
        return list.copy(self)

    def __reduce_ex__(self, protocol):
        self._whole()
        return list.__reduce_ex__(self, protocol)


def wrap(obj, path=""):
    t = type(obj)
    if t is dict:
        d = TDict()
        d._p = path
        for k, v in obj.items():
            dict.__setitem__(d, k, wrap(v, _child(path, k)))
        return d
    if t is list:
        lst = TList()
        lst._p = path
        child = path + "/[]"
        list.extend(lst, [wrap(v, child) for v in obj])
        return lst
    return obj


def main():
    out, saves = sys.argv[1], [pathlib.Path(s) for s in sys.argv[2:]]
    import rust_parser

    rp = getattr(rust_parser, "rust_parser", rust_parser)
    from stellarisdashboard import cli, config

    config.initialize()
    with tempfile.TemporaryDirectory() as tmp:
        config.CONFIG.base_output_path = pathlib.Path(tmp)
        config.CONFIG.debug_mode = True
        for save in saves:
            gamestate = wrap(rp.parse_save_file_fast(str(save)))
            before = len(ACCESSED)
            tle = cli.f_import_gamestate(save.parent.name, gamestate, save)
            print(
                f"{save.parent.name}/{save.name}: {len(ACCESSED) - before} new paths,"
                f" {len(ACCESSED)} total, failures: {len(getattr(tle, 'failures', []))}",
                flush=True,
            )
            del gamestate
    with open(out, "w") as f:
        json.dump({"accessed": sorted(ACCESSED), "whole": sorted(WHOLE), "saves": [str(s) for s in saves]}, f, indent=0)


if __name__ == "__main__":
    main()
