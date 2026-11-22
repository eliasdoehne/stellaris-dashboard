"""Benchmark the timeline (database/ORM) stage of the save import.

Usage (from the repository root, pinned to one otherwise idle core):

    taskset -c 2 uv run python tools/perf/bench.py <output_dir> [--corpus DIR] [--cache DIR]
        [--profile FILE] [--label TEXT] [--max-saves N] [game-id-prefix ...]

Every save of each selected game of the test corpus (see docs/test-corpus.md) is parsed (not
timed) and imported in filename order into a fresh database below <output_dir> (deleted first).
Only ``TimelineExtractor.process_gamestate`` is timed: wall and CPU time per save and per data
processor, the number of session flushes and the number of SQL statements sent to SQLite.
The results are printed and written to <output_dir>/bench.json; the databases end up in
<output_dir>/db/ and can be compared with tools/perf/compare_db.py.

With --cache DIR, parsed gamestates are pickled to DIR/<game>/<save>.sav.pickle on first use
and read from there afterwards: identical input for every run (dict order included) and no
parsing time. To benchmark another version of the package with the same script, put its
source directory first on PYTHONPATH.

Some processors iterate over sets of strings, whose order depends on Python's string hash
seed, so the primary keys of a few tables (e.g. the descriptions of leader traits) differ
from run to run. Set PYTHONHASHSEED (e.g. to 0) for both runs of a comparison that
includes keys.
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
import tomllib

REPO = pathlib.Path(__file__).resolve().parent.parent.parent


def default_corpus() -> pathlib.Path:
    corpus_toml = REPO / "tools" / "corpus" / "corpus.toml"
    version = tomllib.loads(corpus_toml.read_text())["version"]
    return REPO / "corpus" / version / "saves"


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=pathlib.Path, help="output directory, deleted first")
    ap.add_argument("games", nargs="*", help="game id prefixes (default: all games)")
    ap.add_argument("--corpus", type=pathlib.Path, default=None)
    ap.add_argument(
        "--cache", type=pathlib.Path, help="directory of pickled gamestates"
    )
    ap.add_argument("--profile", type=pathlib.Path, help="write cProfile stats to FILE")
    ap.add_argument("--label", default="")
    ap.add_argument("--max-saves", type=int, default=0, help="first N saves per game")
    ap.add_argument(
        "--gc-freeze",
        action="store_true",
        help="gc.freeze() around process_gamestate (as timeline._gc_frozen does), for "
        "versions that do not do it",
    )
    return ap.parse_args()


def rss_mib() -> int:
    """Resident memory of this process, MiB (Linux)."""
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) >> 10
    return 0


def load_gamestate(save: pathlib.Path, cache: pathlib.Path):
    from stellarisdashboard.parsing import save_parser

    cached = cache / save.parent.name / (save.name + ".pickle") if cache else None
    if cached is not None and cached.exists():
        with open(cached, "rb") as fh:
            return pickle.load(fh)
    gamestate = save_parser.parse_save(save)
    if cached is not None:
        cached.parent.mkdir(parents=True, exist_ok=True)
        with open(cached, "wb") as fh:
            pickle.dump(gamestate, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return gamestate


def main():
    args = parse_args()
    corpus = args.corpus or default_corpus()
    out = args.out
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    from stellarisdashboard import config

    config.CONFIG.base_output_path = out

    import sqlalchemy
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    import stellarisdashboard
    from stellarisdashboard.parsing import timeline

    print("package:", os.path.dirname(stellarisdashboard.__file__))
    print("db path:", config.CONFIG.db_path)
    print("PYTHONHASHSEED:", os.environ.get("PYTHONHASHSEED", "(random)"))
    logging.disable(logging.CRITICAL)

    counters = collections.Counter()
    proc_wall = collections.defaultdict(float)
    proc_cpu = collections.defaultdict(float)
    proc_selects = collections.Counter()
    proc_flushes = collections.Counter()

    orig_processors = timeline.TimelineExtractor._data_processors

    def timed_processors(self):
        for p in orig_processors(self):
            extract = p.extract_data_from_gamestate

            def timed(deps, extract=extract, p=p):
                t, c = time.perf_counter(), time.process_time()
                selects, flushes = counters["selects"], counters["flushes"]
                try:
                    return extract(deps)
                finally:
                    proc_wall[p.ID] += time.perf_counter() - t
                    proc_cpu[p.ID] += time.process_time() - c
                    proc_selects[p.ID] += counters["selects"] - selects
                    proc_flushes[p.ID] += counters["flushes"] - flushes

            p.extract_data_from_gamestate = timed
            yield p

    timeline.TimelineExtractor._data_processors = timed_processors

    # the private Session._flush runs once per flush that has work to do
    orig_flush = Session._flush

    def counting_flush(self, *a, **k):
        counters["flushes"] += 1
        return orig_flush(self, *a, **k)

    Session._flush = counting_flush

    @sqlalchemy.event.listens_for(Engine, "before_cursor_execute")
    def count_statements(conn, cursor, statement, parameters, context, executemany):
        counters["statements"] += 1
        head = statement.lstrip()[:6].upper()
        if head == "SELECT":
            counters["selects"] += 1
        elif head in ("INSERT", "UPDATE", "DELETE"):
            counters["dml"] += 1
            counters["dml_rows"] += len(parameters) if executemany else 1

    profiler = cProfile.Profile() if args.profile else None
    rows = []
    for game in sorted(corpus.iterdir()):
        if args.games and not any(game.name.startswith(x) for x in args.games):
            continue
        saves = sorted(game.glob("*.sav"))
        if args.max_saves:
            saves = saves[: args.max_saves]
        for save in saves:
            gamestate = load_gamestate(save, args.cache)
            before = dict(counters)
            tle = timeline.TimelineExtractor()
            if profiler:
                profiler.enable()
            t, c = time.perf_counter(), time.process_time()
            if args.gc_freeze:
                gc.freeze()
            tle.process_gamestate(game.name, gamestate)
            if args.gc_freeze:
                # as timeline._gc_frozen: without the collection, the import's garbage is frozen
                # again by the next save and memory grows by hundreds of MiB per save
                gc.collect()
                gc.unfreeze()
            wall, cpu = time.perf_counter() - t, time.process_time() - c
            if profiler:
                profiler.disable()
            assert not tle.failures, tle.failures
            assert tle.number_of_parsed_saves == 1, f"{save} was not imported"
            rows.append(
                dict(
                    game=game.name,
                    save=save.name,
                    wall=wall,
                    cpu=cpu,
                    **{k: counters[k] - before.get(k, 0) for k in counters},
                )
            )
            del gamestate
            # memory held after the import, once the gamestate is released
            rows[-1]["rss_mib"] = rss_mib()
            print(
                f"{game.name}/{save.name}: {wall:.2f} s, {rows[-1]['rss_mib']} MiB",
                flush=True,
            )

    n = len(rows)
    tot_wall = sum(r["wall"] for r in rows)
    tot_cpu = sum(r["cpu"] for r in rows)
    print(
        f"label={args.label!r} saves={n} timeline wall={tot_wall:.1f}s cpu={tot_cpu:.1f}s"
    )
    print(
        f"per save: wall={tot_wall / n:.3f}s cpu={tot_cpu / n:.3f}s "
        + " ".join(f"{k}={v / n:.0f}" for k, v in sorted(counters.items()))
    )
    by_game = collections.defaultdict(list)
    for r in rows:
        by_game[r["game"]].append(r)
    print(
        "per game (per save): wall s, cpu s, flushes, selects, statements; max RSS MiB"
    )
    for game, rs in by_game.items():
        m = len(rs)
        print(
            f"  {game:42s} saves={m:3d} {sum(r['wall'] for r in rs) / m:7.3f} "
            f"{sum(r['cpu'] for r in rs) / m:7.3f} "
            + " ".join(
                f"{sum(r.get(k, 0) for r in rs) / m:7.0f}"
                for k in ("flushes", "selects", "statements")
            )
            + f" {max(r['rss_mib'] for r in rs):6d}"
        )
    print("processors (wall s, cpu s, % of timeline cpu, selects/save, flushes/save):")
    for k, v in sorted(proc_cpu.items(), key=lambda kv: -kv[1])[:28]:
        print(
            f"  {k:28s} {proc_wall[k]:8.2f} {v:8.2f} {100 * v / tot_cpu:5.1f}% "
            f"{proc_selects[k] / n:7.0f} {proc_flushes[k] / n:6.0f}"
        )
    print(
        f"  {'(all processors)':28s} {sum(proc_wall.values()):8.2f} "
        f"{sum(proc_cpu.values()):8.2f}"
    )
    with open(out / "bench.json", "w") as fh:
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
            fh,
            indent=1,
        )
    if profiler:
        profiler.dump_stats(args.profile)
        for sort in ("tottime", "cumulative"):
            s = io.StringIO()
            pstats.Stats(profiler, stream=s).sort_stats(sort).print_stats(60)
            print(s.getvalue()[:14000])


if __name__ == "__main__":
    main()
