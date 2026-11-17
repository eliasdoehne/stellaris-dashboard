"""Cost of shipping a parsed gamestate between processes: pickle.dumps + pickle.loads (protocol 5).

    taskset -c 0 uv run tools/perf/bench_pickle.py [--every 8] [--repeat 2] [--nested FILTER.json]

The bulk importer parses saves in worker processes and sends the dicts to the main process, which
costs one dumps in the worker and one loads in the main process per save.
"""

import argparse
import pickle
import time

from common import TOP_LEVEL_SECTIONS, corpus_saves, load_filter, rp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=8)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--nested", default=None)
    args = ap.parse_args()
    nested = load_filter(args.nested) if args.nested else None
    variants = {
        "current parser, full dict": lambda f: rp.parse_save_file(f),
        "new parser, full dict (shared strings)": lambda f: rp.parse_save_file_fast(f),
        "new parser, full dict, no string cache": lambda f: rp.parse_save_file_fast(f, cache=False),
        "new parser, top-level projection": lambda f: rp.parse_save_file_fast(f, TOP_LEVEL_SECTIONS),
    }
    if nested is not None:
        variants["new parser, nested projection"] = lambda f: rp.parse_save_file_fast(f, nested)
    saves = corpus_saves(args.every)
    totals = {name: [0.0, 0.0, 0.0] for name in variants}
    for save in saves:
        for name, fn in variants.items():
            obj = fn(save)
            best_d = best_l = float("inf")
            for _ in range(args.repeat):
                t0 = time.process_time()
                blob = pickle.dumps(obj, protocol=5)
                t1 = time.process_time()
                copy = pickle.loads(blob)
                t2 = time.process_time()
                best_d, best_l = min(best_d, t1 - t0), min(best_l, t2 - t1)
                assert copy == obj
                del copy
            totals[name][0] += best_d
            totals[name][1] += best_l
            totals[name][2] += len(blob)
            del obj, blob
    n = len(saves)
    print(f"{n} saves, best of {args.repeat}, CPU seconds per save")
    print(f"{'variant':42s} {'dumps s':>8s} {'loads s':>8s} {'total s':>8s} {'pickle MB':>10s}")
    for name, (d, l, size) in totals.items():
        print(f"{name:42s} {d / n:8.3f} {l / n:8.3f} {(d + l) / n:8.3f} {size / n / 1e6:10.1f}")


if __name__ == "__main__":
    main()
