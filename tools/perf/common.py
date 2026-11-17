"""Shared helpers for the parser benchmarks in tools/perf."""

import glob
import json
import math
import os
import pathlib
import zipfile

import rust_parser

rp = getattr(rust_parser, "rust_parser", rust_parser)

CORPUS = os.environ.get("STELLARIS_CORPUS", "/home/user/stellaris-dashboard/corpus/4.5.2/saves")

# Top-level gamestate keys that stellarisdashboard/parsing/*.py reads (grep for
# `gamestate_dict.get("…")`, `gamestate_dict["…"]` and, in save_format.py, `gamestate.get("…")`),
# confirmed with tools/perf/trace_access.py.
TOP_LEVEL_SECTIONS = sorted(
    """country galactic_object fleet ships leaders federation pop_factions market war truce trade_deal
    starbase_mgr spy_networks species_db situations sectors resolution pop_jobs pop_groups player planets
    megastructures galaxy galactic_community flags first_contacts espionage_operations deposit date
    council_positions bypasses astral_rifts archaeological_sites agreements
    colony districts buildings zones ship_design""".split()
)


def corpus_saves(every=1, offset=0):
    """Every `every`-th save of each game, in a stable order."""
    saves = []
    for game in sorted(glob.glob(os.path.join(CORPUS, "*"))):
        saves.extend(sorted(glob.glob(os.path.join(game, "*.sav")))[offset::every])
    return saves


def gamestate_size(save):
    with zipfile.ZipFile(save) as z:
        return z.getinfo("gamestate").file_size


def load_filter(path):
    """A nested section filter written by nested_filter.py."""
    return json.loads(pathlib.Path(path).read_text())["filter"]


def restrict(obj, flt):
    """What the parser should return for `obj` under the section filter `flt` (same notation as the
    `sections` argument of parse_save_file_fast)."""
    if flt is None or flt is True:
        return obj
    if not isinstance(flt, dict):
        flt = {name: None for name in flt}
    default_given = "*" in flt
    default = flt.get("*")
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k) in flt:
                out[k] = restrict(v, flt[str(k)])
            elif default_given:
                out[k] = restrict(v, default)
        return out
    if isinstance(obj, list):
        return [restrict(v, default) for v in obj] if default_given else obj
    return obj


def first_differences(a, b, path="", limit=5, out=None):
    """Paths at which two parsed objects differ (NaN counts as equal to NaN)."""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append(f"{path}: type {type(a).__name__} != {type(b).__name__}")
    elif isinstance(a, dict):
        for k in a.keys() | b.keys():
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'first' if k in a else 'second'}")
            else:
                first_differences(a[k], b[k], f"{path}/{k}", limit, out)
            if len(out) >= limit:
                break
    elif isinstance(a, (list, tuple)):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                first_differences(x, y, f"{path}[{i}]", limit, out)
    elif isinstance(a, float):
        if not (a == b or (math.isnan(a) and math.isnan(b))):
            out.append(f"{path}: {a!r} != {b!r}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out
