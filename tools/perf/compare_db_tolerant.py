"""Resolved comparison of two game DBs where floats may differ by a relative 1e-9 (summation order).

    python tools/perf/compare_db_tolerant.py <reference.db> <candidate.db> tools/perf
"""
import collections, math, pathlib, sys, tempfile
sys.path.insert(0, sys.argv[3])
import compare_db as cd

def norm(v):
    # replace floats by a coarse key; exactness is checked afterwards with isclose
    if isinstance(v, float): return ("f",)
    if isinstance(v, tuple): return tuple(norm(x) for x in v)
    return v

def close(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    return a == b

with tempfile.TemporaryDirectory() as tmp:
    a = cd.Db(pathlib.Path(sys.argv[1]), pathlib.Path(tmp) / "a")
    b = cd.Db(pathlib.Path(sys.argv[2]), pathlib.Path(tmp) / "b")
    bad = 0; noisy = 0
    for t in a.tables:
        ca, cb = collections.Counter(a.resolved_rows(t)), collections.Counter(b.resolved_rows(t))
        oa, ob = list((ca - cb).elements()), list((cb - ca).elements())
        if not oa and not ob: continue
        groups = collections.defaultdict(list)
        for r in ob: groups[norm(r)].append(r)
        unmatched = 0
        for r in oa:
            cands = groups.get(norm(r), [])
            for i, c in enumerate(cands):
                if close(r, c):
                    cands.pop(i); break
            else:
                unmatched += 1
        left = unmatched + sum(len(v) for v in groups.values())
        noisy += len(oa) - unmatched
        if left:
            bad += left; print(f"  {t}: {left} rows differ beyond float tolerance")
    print(f"rows differing only by float noise: {noisy}; rows differing otherwise: {bad}")
