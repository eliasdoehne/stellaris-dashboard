"""Split the self time of a cProfile dump (bench.py --profile) by where it is spent.

Usage:  python tools/perf/profile_split.py <stats file>

Self time of Python functions is assigned by their file; self time of built-in functions
(dict.get, isinstance, sqlite3 calls, ...) is assigned to the group of the function calling
them, except for the sqlite3 calls, which form their own group.
"""

import collections
import pstats
import sys


def group_of(filename: str, name: str) -> str:
    if "sqlite3" in name:
        return "sqlite3 (C library calls)"
    if "sqlalchemy" in filename:
        return "SQLAlchemy"
    if "stellarisdashboard" in filename:
        return "stellarisdashboard (" + filename.rsplit("/", 1)[-1] + ")"
    if filename == "~":
        return None  # built-in: assigned by caller
    return "other Python (stdlib etc.)"


stats = pstats.Stats(sys.argv[1]).stats
totals = collections.Counter()
for (filename, _, name), (_, _, tottime, _, callers) in stats.items():
    group = group_of(filename, name)
    if group is not None:
        totals[group] += tottime
        continue
    caller_time = sum(c[2] for c in callers.values())
    for (caller_file, _, caller_name), c in callers.items():
        caller_group = group_of(caller_file, caller_name) or "other Python (stdlib etc.)"
        totals[caller_group] += tottime * c[2] / caller_time if caller_time else 0.0
total = sum(totals.values())
print(f"total self time {total:.1f} s (with profiler overhead)")
for group, seconds in totals.most_common():
    print(f"  {group:45s} {seconds:7.1f} s {100 * seconds / total:5.1f}%")
