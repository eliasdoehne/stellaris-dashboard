"""Which parts of a save are large, and which of them the import pipeline reads.

    uv run tools/perf/section_stats.py [--every 8] [--nested FILTER.json] [--trace TRACE.json]

Prints, averaged over the selected saves:
* the share of gamestate bytes and of parsed values (≈ Python objects before string sharing) of
  each top-level section, and whether the code reads the section;
* with --nested: the share of values that survive the top-level and the nested projection;
* with --trace: the largest second-level keys (section/*/key) that the traced pipeline never touched.
"""

import argparse
import json

from common import TOP_LEVEL_SECTIONS, corpus_saves, load_filter, rp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=8)
    ap.add_argument("--nested", default=None)
    ap.add_argument("--trace", default=None)
    ap.add_argument("--depth", type=int, default=4)
    args = ap.parse_args()
    saves = corpus_saves(args.every)
    nested = load_filter(args.nested) if args.nested else None
    accessed = set(json.load(open(args.trace))["accessed"]) if args.trace else None

    n = len(saves)
    share_bytes, share_values = {}, {}
    kept_top = kept_nested = 0.0
    kept_nested_by_section = {}
    for save in saves:
        stats = rp.save_file_stats(save, args.depth)
        _, total_bytes, total_values = stats[""]
        for path, (_count, nbytes, values) in stats.items():
            share_bytes[path] = share_bytes.get(path, 0.0) + nbytes / total_bytes / n
            share_values[path] = share_values.get(path, 0.0) + values / total_values / n
        top = rp.save_file_stats(save, 1, TOP_LEVEL_SECTIONS)
        kept_top += top[""][2] / total_values / n
        if nested is not None:
            proj = rp.save_file_stats(save, 1, nested)
            kept_nested += proj[""][2] / total_values / n
            for path, (_c, _b, values) in proj.items():
                if path:
                    kept_nested_by_section[path] = kept_nested_by_section.get(path, 0.0) + values / total_values / n

    print(f"{n} saves\n")
    print(f"{'top-level section':38s} {'bytes %':>8s} {'values %':>9s}  read" + ("  values % kept by nested filter" if nested else ""))
    top_paths = [p for p in share_bytes if p.count("/") == 1]
    read_b = read_v = 0.0
    for p in sorted(top_paths, key=lambda p: -share_bytes[p]):
        is_read = p[1:] in TOP_LEVEL_SECTIONS
        if is_read:
            read_b += share_bytes[p]
            read_v += share_values[p]
        if share_bytes[p] >= 0.001 or is_read:
            extra = f"  {100 * kept_nested_by_section.get(p, 0.0):6.2f}" if nested else ""
            print(f"{p[1:]:38s} {100 * share_bytes[p]:8.2f} {100 * share_values[p]:9.2f}  {'yes' if is_read else 'no '}{extra}")
    print(f"\nsections the code reads: {100 * read_b:.1f}% of bytes, {100 * read_v:.1f}% of values")
    print(f"values kept by the top-level projection (measured): {100 * kept_top:.1f}%")
    if nested is not None:
        print(f"values kept by the nested projection (measured, filter from the trace): {100 * kept_nested:.1f}%")

    if accessed is not None:
        print("\nlargest keys below the read sections that the traced pipeline never touched (bytes % of gamestate):")
        rows = []
        for p, b in share_bytes.items():
            comps = p.split("/")
            if len(comps) < 3 or comps[1] not in TOP_LEVEL_SECTIONS:
                continue
            parent = p.rpartition("/")[0]
            # report the shallowest untouched path only
            if p not in accessed and (parent in accessed or parent.count("/") == 1):
                rows.append((b, p))
        for b, p in sorted(rows, reverse=True)[:40]:
            print(f"  {100 * b:6.2f}  {p}")
        print(f"  total untouched (these paths): {100 * sum(b for b, _ in rows):.1f}% of bytes")


if __name__ == "__main__":
    main()
