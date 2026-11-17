"""Turn an access trace (trace_access.py) into a nested section filter for parse_save_file_fast.

    uv run tools/perf/nested_filter.py TRACE.json OUT.json [--depth 4]

The filter keeps a path if the traced pipeline touched it. A touched path without touched children,
or one that was used as a whole, is kept completely. Below `--depth` path components everything is
kept. Top-level sections that the code reads (common.TOP_LEVEL_SECTIONS) but the trace did not
touch are kept completely.

The result is only as complete as the trace: it is meant for estimating what a nested projection
would save, not for production use.
"""

import argparse
import json

from common import TOP_LEVEL_SECTIONS


def merge(a, b):
    if a is None or b is None:
        return None
    out = dict(a)
    for k, v in b.items():
        out[k] = merge(out[k], v) if k in out else v
    return out


def build(children, whole, path, depth, max_depth):
    kids = children.get(path)
    if not kids or path in whole or depth >= max_depth:
        return None
    flt = {}
    for comp in sorted(kids):
        sub = build(children, whole, f"{path}/{comp}", depth + 1, max_depth)
        name = "*" if comp in ("*", "[]") else comp
        # `*` (integer keys) and `[]` (list elements) share the filter's default entry
        flt[name] = merge(flt[name], sub) if name in flt else sub
    return flt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("out")
    ap.add_argument("--depth", type=int, default=4)
    args = ap.parse_args()
    trace = json.load(open(args.trace))
    whole = set(trace["whole"])
    children = {}
    for p in trace["accessed"]:
        parent, _, comp = p.rpartition("/")
        children.setdefault(parent, set()).add(comp)
        # make sure all ancestors exist
        while parent:
            grand, _, c = parent.rpartition("/")
            children.setdefault(grand, set()).add(c)
            parent = grand
    flt = build(children, whole, "", 0, args.depth) or {}
    traced_top = set(flt)
    for name in TOP_LEVEL_SECTIONS:
        flt.setdefault(name, None)
    print("top-level keys touched by the trace but not in TOP_LEVEL_SECTIONS:", sorted(traced_top - set(TOP_LEVEL_SECTIONS)))
    print("TOP_LEVEL_SECTIONS not touched by the trace:", sorted(set(TOP_LEVEL_SECTIONS) - traced_top))
    print("paths used as a whole:", sorted(whole))

    def count(f):
        return 1 + sum(count(v) for v in f.values()) if isinstance(f, dict) else 1

    print("filter nodes:", count(flt))
    json.dump({"filter": flt, "depth": args.depth, "trace_saves": trace.get("saves")}, open(args.out, "w"), indent=1)


if __name__ == "__main__":
    main()
