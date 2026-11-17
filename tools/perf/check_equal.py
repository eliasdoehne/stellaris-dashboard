"""Check that the prototype parser returns the same Python object as the current parser.

    uv run tools/perf/check_equal.py [--every N] [--nested FILTER.json]

For every save: `rust_parser.parse_save_file` (unchanged nom parser) is compared with `==` against
every mode of `parse_save_file_fast`; the projected results are compared against the full result
restricted to the selected keys.
"""

import argparse
import sys

from common import TOP_LEVEL_SECTIONS, corpus_saves, first_differences, load_filter, restrict, rp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--nested", default=None)
    ap.add_argument("--start", type=int, default=0, help="skip this many saves (to resume a run)")
    args = ap.parse_args()
    nested = load_filter(args.nested) if args.nested else None

    saves = corpus_saves(args.every)[args.start :]
    checks = failures = 0
    for i, save in enumerate(saves):
        base = rp.parse_save_file(save)
        candidates = {
            "direct+cache+libdeflate": (lambda: rp.parse_save_file_fast(save), base),
            "direct, no cache, zip inflate": (
                lambda: rp.parse_save_file_fast(save, mode="direct", cache=False, fast_inflate=False),
                base,
            ),
            "tape+cache": (lambda: rp.parse_save_file_fast(save, mode="tape"), base),
            "tape, no cache": (lambda: rp.parse_save_file_fast(save, mode="tape", cache=False), base),
            "legacy tree": (lambda: rp.parse_save_file_fast(save, mode="value"), base),
        }
        top = {k: base[k] for k in TOP_LEVEL_SECTIONS if k in base}
        candidates["top-level projection (direct)"] = (lambda: rp.parse_save_file_fast(save, TOP_LEVEL_SECTIONS), top)
        candidates["top-level projection (tape)"] = (
            lambda: rp.parse_save_file_fast(save, TOP_LEVEL_SECTIONS, mode="tape"),
            top,
        )
        if nested is not None:
            candidates["nested projection (direct)"] = (
                lambda: rp.parse_save_file_fast(save, nested),
                restrict(base, nested),
            )
        bad = []
        for name, (fn, expected) in candidates.items():
            checks += 1
            got = fn()
            if got != expected:
                diffs = first_differences(expected, got)
                if diffs:
                    failures += 1
                    bad.append(f"{name}: " + "; ".join(diffs))
                else:
                    bad.append(f"{name}: differs only by NaN != NaN")
            del got
        status = "OK" if not bad else "DIFFERENT " + " | ".join(bad)
        print(f"[{i + 1}/{len(saves)}] {save.split('/')[-2][:24]}/{save.split('/')[-1]}: {status}", flush=True)
    print(f"{len(saves)} saves, {checks} comparisons, {failures} with real differences")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
