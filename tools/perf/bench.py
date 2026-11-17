"""Benchmark the timeline (database/ORM) stage of the save import.

Usage (from the repository root, pinned to one core):

    taskset -c 1 uv run python tools/perf/bench.py <output_dir> [--corpus DIR] [--profile FILE]
        [--label TEXT] [game-name-prefix ...]

Every save of each selected game is parsed (not timed) and imported in filename order into a
fresh database below <output_dir>. Only ``TimelineExtractor.process_gamestate`` is timed: wall
time and CPU time (``time.process_time``) per save, wall and CPU time per processor, the number
of ``Session._flush`` runs and the number of SQL statements sent to SQLite.

To benchmark another version of the package with the same script, put it first on PYTHONPATH.
"""

import argparse
import collections
import cProfile
import gc
import io
import json
import logging
import os
import pathlib
import pickle
import pstats
import shutil
import time

ap = argparse.ArgumentParser()
ap.add_argument("out", type=pathlib.Path)
ap.add_argument("games", nargs="*")
ap.add_argument("--corpus", type=pathlib.Path, default=pathlib.Path("corpus/4.5.2/saves"))
ap.add_argument("--profile", type=pathlib.Path, help="write cProfile stats of the timeline stage")
ap.add_argument("--label", default="")
ap.add_argument("--max-saves", type=int, default=0)
ap.add_argument(
    "--gc-freeze",
    action="store_true",
    help="gc.freeze() around process_gamestate, to measure that alone on code without it",
)
ap.add_argument(
    "--cache",
    type=pathlib.Path,
    help="directory of pickled gamestates: identical input for every run (dict order included) and no parsing",
)
args = ap.parse_args()

out = args.out
os.environ["STELLARISDASHBOARD_OUTPUT"] = str(out)
from stellarisdashboard import config  # noqa: E402

config.CONFIG.base_output_path = out
shutil.rmtree(out, ignore_errors=True)
out.mkdir(parents=True)

import sqlalchemy  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import stellarisdashboard  # noqa: E402
from stellarisdashboard.parsing import save_parser, timeline  # noqa: E402

print("package:", os.path.dirname(stellarisdashboard.__file__))
print("db path:", config.CONFIG.db_path)
logging.disable(logging.CRITICAL)

counters = collections.Counter()
proc_wall = collections.defaultdict(float)
proc_cpu = collections.defaultdict(float)
proc_selects = collections.Counter()
proc_flushes = collections.Counter()

orig_processors = timeline.TimelineExtractor._data_processors


def wrapped_processors(self):
    for p in orig_processors(self):
        f = p.extract_data_from_gamestate

        def timed(deps, f=f, p=p):
            t, c = time.perf_counter(), time.process_time()
            selects, flushes = counters["selects"], counters["flushes"]
            try:
                return f(deps)
            finally:
                proc_wall[p.ID] += time.perf_counter() - t
                proc_cpu[p.ID] += time.process_time() - c
                proc_selects[p.ID] += counters["selects"] - selects
                proc_flushes[p.ID] += counters["flushes"] - flushes

        p.extract_data_from_gamestate = timed
        yield p


timeline.TimelineExtractor._data_processors = wrapped_processors

orig_flush = Session._flush


def counting_flush(self, *a, **k):
    counters["flushes"] += 1
    return orig_flush(self, *a, **k)


Session._flush = counting_flush


@sqlalchemy.event.listens_for(Engine, "before_cursor_execute")
def _count_statements(conn, cursor, statement, parameters, context, executemany):
    counters["statements"] += 1
    head = statement.lstrip()[:6].upper()
    if head == "SELECT":
        counters["selects"] += 1
    elif head in ("INSERT", "UPDATE", "DELETE"):
        counters["dml"] += 1
        counters["dml_rows"] += len(parameters) if executemany else 1


profiler = cProfile.Profile() if args.profile else None
rows = []
for g in sorted(args.corpus.iterdir()):
    if args.games and not any(g.name.startswith(x) for x in args.games):
        continue
    saves = sorted(g.glob("*.sav"))
    if args.max_saves:
        saves = saves[: args.max_saves]
    for f in saves:
        cached = args.cache / g.name / (f.name + ".pickle") if args.cache else None
        if cached is not None and cached.exists():
            with open(cached, "rb") as fh:
                gs = pickle.load(fh)
        else:
            gs = save_parser.parse_save(f)
            if cached is not None:
                cached.parent.mkdir(parents=True, exist_ok=True)
                with open(cached, "wb") as fh:
                    pickle.dump(gs, fh, protocol=pickle.HIGHEST_PROTOCOL)
        before = dict(counters)
        tle = timeline.TimelineExtractor()
        if profiler:
            profiler.enable()
        t, c = time.perf_counter(), time.process_time()
        if args.gc_freeze:
            gc.freeze()
        tle.process_gamestate(g.name, gs)
        if args.gc_freeze:
            gc.unfreeze()
        wall, cpu = time.perf_counter() - t, time.process_time() - c
        if profiler:
            profiler.disable()
        assert not tle.failures, tle.failures
        rows.append(
            dict(
                game=g.name,
                save=f.name,
                wall=wall,
                cpu=cpu,
                **{k: counters[k] - before.get(k, 0) for k in counters},
            )
        )
        del gs

n = len(rows)
tot_wall = sum(r["wall"] for r in rows)
tot_cpu = sum(r["cpu"] for r in rows)
print(f"label={args.label!r} saves={n} timeline wall={tot_wall:.1f}s cpu={tot_cpu:.1f}s")
print(
    f"per save: wall={tot_wall / n:.3f}s cpu={tot_cpu / n:.3f}s "
    + " ".join(f"{k}={v / n:.0f}" for k, v in sorted(counters.items()))
)
by_game = collections.defaultdict(list)
for r in rows:
    by_game[r["game"]].append(r)
for game, rs in by_game.items():
    k = max(1, len(rs) // 5)
    first = sum(r["cpu"] for r in rs[:k]) / k
    last = sum(r["cpu"] for r in rs[-k:]) / k
    print(
        f"  {game}: saves={len(rs)} wall={sum(r['wall'] for r in rs):.1f}s cpu={sum(r['cpu'] for r in rs):.1f}s "
        f"cpu/save first {k}={first:.3f}s last {k}={last:.3f}s"
    )
print("processors (wall s, cpu s, % of timeline cpu, selects/save, flushes/save):")
for k, v in sorted(proc_cpu.items(), key=lambda kv: -kv[1])[:28]:
    print(
        f"  {k:28s} {proc_wall[k]:8.2f} {v:8.2f} {100 * v / tot_cpu:5.1f}% "
        f"{proc_selects[k] / n:7.0f} {proc_flushes[k] / n:6.0f}"
    )
print(f"  {'(all processors)':35s} {sum(proc_wall.values()):8.2f} {sum(proc_cpu.values()):8.2f}")
json.dump(
    dict(
        label=args.label,
        rows=rows,
        proc_wall=proc_wall,
        proc_cpu=proc_cpu,
        proc_selects=proc_selects,
        proc_flushes=proc_flushes,
        counters=counters,
    ),
    open(out / "bench.json", "w"),
    indent=1,
)
if profiler:
    profiler.dump_stats(args.profile)
    for sort in ("tottime", "cumulative"):
        s = io.StringIO()
        pstats.Stats(profiler, stream=s).sort_stats(sort).print_stats(60)
        print(s.getvalue()[:14000])
