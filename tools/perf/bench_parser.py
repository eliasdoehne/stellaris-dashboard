"""Benchmark the save parser variants on the corpus.

    taskset -c 0 uv run tools/perf/bench_parser.py --every 3 --repeat 2 [--nested FILTER.json] [--out OUT.json]
    taskset -c 0 uv run tools/perf/bench_parser.py --rss VARIANT SAVE     # peak RSS of one parse

Per variant: CPU seconds per save (time.process_time, best of the repeats per save, includes the
garbage collector pass that the new objects trigger), wall seconds, MB/s of gamestate text, and the
time to free the result. With --phases, also the mean of the phase timings reported by the extension.
"""

import argparse
import gc
import json
import resource
import subprocess
import sys
import time

from common import TOP_LEVEL_SECTIONS, corpus_saves, gamestate_size, load_filter, rp

NESTED = None


def variants():
    v = {
        # the current parser: nom -> HashMap tree -> Python
        "baseline": lambda f: rp.parse_save_file(f),
        "baseline (phases timed)": lambda f: rp.parse_save_file_timed(f),
        # Approach 1, cumulative steps
        "A1 lexer": lambda f: rp.parse_save_file_fast(f, mode="value", fast_inflate=False),
        "A2 +tape/C-API build": lambda f: rp.parse_save_file_fast(f, mode="tape", cache=False, fast_inflate=False),
        "A3 +string cache": lambda f: rp.parse_save_file_fast(f, mode="tape", cache=True, fast_inflate=False),
        "A4 +direct build": lambda f: rp.parse_save_file_fast(f, mode="direct", cache=True, fast_inflate=False),
        "A5 +libdeflate": lambda f: rp.parse_save_file_fast(f, mode="direct", cache=True, fast_inflate=True),
        "A6 +GC untrack (experimental)": lambda f: rp.parse_save_file_fast(f, gc_untrack=True),
        "A5 tape variant": lambda f: rp.parse_save_file_fast(f, mode="tape", cache=True, fast_inflate=True),
        # Approach 2
        "P1 lexer+top-level projection": lambda f: rp.parse_save_file_fast(
            f, TOP_LEVEL_SECTIONS, mode="value", fast_inflate=False
        ),
        "A5+P1 top-level projection": lambda f: rp.parse_save_file_fast(f, TOP_LEVEL_SECTIONS),
        # lower bounds
        "lexer only (no output)": lambda f: rp.parse_save_file_fast(f, mode="null"),
    }
    if NESTED is not None:
        v["P2 lexer+nested projection"] = lambda f: rp.parse_save_file_fast(
            f, NESTED, mode="value", fast_inflate=False
        )
        v["A5+P2 nested projection"] = lambda f: rp.parse_save_file_fast(f, NESTED)
    return v


def measure(fn, save):
    c0, w0 = time.process_time(), time.perf_counter()
    result = fn(save)
    c1, w1 = time.process_time(), time.perf_counter()
    phases = dict(rp.last_timings())
    del result
    c2 = time.process_time()
    return c1 - c0, w1 - w0, c2 - c1, phases


def rss(variant, save):
    fn = variants()[variant]
    result = fn(save)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    del result
    print(json.dumps({"variant": variant, "peak_rss_mb": peak}))


def main():
    global NESTED
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=8)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--only", default=None, help="comma separated variant name prefixes")
    ap.add_argument("--nested", default=None, help="nested filter JSON from nested_filter.py")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rss", nargs=2, metavar=("VARIANT", "SAVE"))
    ap.add_argument("--rss-save", default=None, help="also measure peak RSS of each variant on this save")
    args = ap.parse_args()
    if args.nested:
        NESTED = load_filter(args.nested)
    if args.rss:
        return rss(*args.rss)

    saves = corpus_saves(args.every)
    sizes = {s: gamestate_size(s) for s in saves}
    total_mb = sum(sizes.values()) / 1e6
    vs = variants()
    if args.only:
        prefixes = args.only.split(",")
        vs = {k: v for k, v in vs.items() if any(k.startswith(p) for p in prefixes)}
    print(f"{len(saves)} saves, {total_mb:.0f} MB of gamestate text, repeat={args.repeat}", flush=True)

    best = {name: {} for name in vs}
    for rep in range(args.repeat):
        for save in saves:
            for name, fn in vs.items():
                cpu, wall, free, phases = measure(fn, save)
                if name == "baseline":
                    phases = {}  # parse_save_file records no timings
                prev = best[name].get(save)
                if prev is None or cpu < prev[0]:
                    best[name][save] = (cpu, wall, free, phases)
        print(f"repeat {rep + 1} done", flush=True)

    rows = []
    base_cpu = sum(v[0] for v in best["baseline"].values()) if "baseline" in best else None
    for name, per_save in best.items():
        cpu = sum(v[0] for v in per_save.values())
        wall = sum(v[1] for v in per_save.values())
        free = sum(v[2] for v in per_save.values())
        phases = {}
        for v in per_save.values():
            for k, t in v[3].items():
                phases[k] = phases.get(k, 0.0) + t
        row = {
            "variant": name,
            "saves": len(per_save),
            "cpu_s_per_save": cpu / len(per_save),
            "wall_s_per_save": wall / len(per_save),
            "free_s_per_save": free / len(per_save),
            "mb_per_s": total_mb / cpu,
            "speedup": base_cpu / cpu if base_cpu else None,
            "phases_s_per_save": {k: t / len(per_save) for k, t in phases.items()},
        }
        if args.rss_save:
            out = subprocess.run(
                [sys.executable, __file__, "--rss", name, args.rss_save]
                + (["--nested", args.nested] if args.nested else []),
                capture_output=True,
                text=True,
                check=True,
            )
            row["peak_rss_mb"] = json.loads(out.stdout.strip().splitlines()[-1])["peak_rss_mb"]
        rows.append(row)

    print(f"\n{'variant':34s} {'saves':>5s} {'CPU s/save':>10s} {'wall s/save':>11s} {'MB/s':>7s} {'speedup':>7s} {'free s':>7s} {'peak RSS MB':>11s}")
    for r in rows:
        sp = f"{r['speedup']:.2f}x" if r["speedup"] else "-"
        rss_mb = f"{r['peak_rss_mb']:.0f}" if "peak_rss_mb" in r else "-"
        print(
            f"{r['variant']:34s} {r['saves']:5d} {r['cpu_s_per_save']:10.3f} {r['wall_s_per_save']:11.3f}"
            f" {r['mb_per_s']:7.1f} {sp:>7s} {r['free_s_per_save']:7.3f} {rss_mb:>11s}"
        )
    print("\nphase timings (wall s/save, from the extension):")
    for r in rows:
        if r["phases_s_per_save"]:
            print(f"  {r['variant']:34s} " + "  ".join(f"{k}={t:.3f}" for k, t in r["phases_s_per_save"].items()))
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"saves": saves, "total_mb": total_mb, "gc_enabled": gc.isenabled(), "rows": rows}, f, indent=1)


if __name__ == "__main__":
    main()
