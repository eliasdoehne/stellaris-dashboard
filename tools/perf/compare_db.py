"""Compare two game databases written by the timeline stage.

Usage:  python tools/perf/compare_db.py <reference.db> <candidate.db> [--show N]

Two comparisons per table:

* exact: the rows are identical including primary and foreign key values;
* resolved: the multisets of rows are identical after dropping the surrogate primary key and
  replacing every foreign key by the content of the row it references (recursively). Rows of
  the entity tables listed in NATURAL_KEYS are referenced by their in-game identity instead,
  which also breaks the reference cycles of the schema (country -> planet -> system -> country).

The databases are copied to a temporary directory first (with their WAL files), so the
originals are never written to. ``game.db_last_updated`` is ignored (wall-clock timestamp).
Exit status 1 if any table differs in the resolved comparison.
"""

import argparse
import collections
import pathlib
import shutil
import sqlite3
import sys
import tempfile

NATURAL_KEYS = {
    "game": ["game_name"],
    "shared_description": ["text"],
    "system": ["system_id_in_game"],
    "country": ["country_id_in_game"],
    "gamestate": ["date"],
    "species": ["species_id_in_game"],
    "leader": ["leader_id_in_game", "date_hired", "date_born"],
    "planet": ["planet_id_in_game"],
    "fleet": ["fleet_id_in_game"],
    "war": ["war_id_in_game", "start_date_days"],
    "federation": ["federation_id_in_game", "start_date_days"],
    "political_faction": ["country_id", "faction_id_in_game"],
}
IGNORED_COLUMNS = {("game", "db_last_updated")}
# 0 compares floats exactly; N rounds them to N significant digits (the parser's dict order, and
# with it the order of float sums, differs between two parses of the same save)
FLOAT_DIGITS = 0


class Db:
    def __init__(self, path: pathlib.Path, workdir: pathlib.Path):
        workdir.mkdir()
        for suffix in ("", "-wal", "-shm"):
            src = pathlib.Path(str(path) + suffix)
            if src.exists():
                shutil.copy(src, workdir / (path.name + suffix))
        self.con = sqlite3.connect(workdir / path.name)
        self.tables = sorted(
            r[0]
            for r in self.con.execute(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
            )
        )
        self.columns = {}
        self.pk = {}
        self.fks = {}
        self.rows = {}
        self.by_pk = {}
        for t in self.tables:
            info = self.con.execute(f'pragma table_info("{t}")').fetchall()
            self.columns[t] = [r[1] for r in info]
            pks = [r[1] for r in info if r[5]]
            assert len(pks) == 1, (t, pks)
            self.pk[t] = pks[0]
            self.fks[t] = {
                r[3]: (r[2], r[4]) for r in self.con.execute(f'pragma foreign_key_list("{t}")')
            }
            self.rows[t] = self.con.execute(f'select * from "{t}"').fetchall()
            i = self.columns[t].index(self.pk[t])
            self.by_pk[t] = {row[i]: row for row in self.rows[t]}
        for t, fks in self.fks.items():
            for col, (ref_table, ref_col) in fks.items():
                assert ref_col in (None, self.pk[ref_table]), (t, col, ref_table, ref_col)
        self._ref_memo = {}
        self._in_progress = set()

    def indexes(self):
        return sorted(
            (r[0], r[1])
            for r in self.con.execute(
                "select tbl_name, sql from sqlite_master where type='index' and sql is not null"
            )
        )

    def _value(self, table, col, value, natural_only):
        if (table, col) in IGNORED_COLUMNS:
            return None
        if col in self.fks[table] and value is not None:
            return self.reference(self.fks[table][col][0], value)
        if FLOAT_DIGITS and isinstance(value, float):
            return float(f"{value:.{FLOAT_DIGITS}g}")
        return value

    def reference(self, table, pk):
        """What a foreign key pointing at (table, pk) stands for."""
        key = (table, pk)
        if key in self._ref_memo:
            return self._ref_memo[key]
        row = self.by_pk[table].get(pk)
        if row is None:
            result = ("<dangling>", table)
        else:
            if key in self._in_progress:
                raise RuntimeError(f"reference cycle through {table}; add it to NATURAL_KEYS")
            self._in_progress.add(key)
            cols = self.columns[table]
            wanted = NATURAL_KEYS.get(table) or [c for c in cols if c != self.pk[table]]
            result = (table,) + tuple(
                self._value(table, c, row[cols.index(c)], True) for c in wanted
            )
            self._in_progress.discard(key)
        self._ref_memo[key] = result
        return result

    def resolved_rows(self, table):
        cols = self.columns[table]
        keep = [(i, c) for i, c in enumerate(cols) if c != self.pk[table]]
        return [tuple(self._value(table, c, row[i], False) for i, c in keep) for row in self.rows[table]]

    def natural_key_duplicates(self):
        dups = {}
        for t in NATURAL_KEYS:
            if t in self.by_pk:
                counts = collections.Counter(self.reference(t, pk) for pk in self.by_pk[t])
                n = sum(c - 1 for c in counts.values() if c > 1)
                if n:
                    dups[t] = n
        return dups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference", type=pathlib.Path)
    ap.add_argument("candidate", type=pathlib.Path)
    ap.add_argument("--show", type=int, default=5, help="differing rows to print per table")
    ap.add_argument("--float-digits", type=int, default=0, help="round floats to N significant digits")
    args = ap.parse_args()
    global FLOAT_DIGITS
    FLOAT_DIGITS = args.float_digits

    with tempfile.TemporaryDirectory() as tmp:
        a = Db(args.reference, pathlib.Path(tmp) / "a")
        b = Db(args.candidate, pathlib.Path(tmp) / "b")
        failed = False
        if a.tables != b.tables:
            print("TABLES DIFFER:", sorted(set(a.tables) ^ set(b.tables)))
            failed = True
        if a.indexes() != b.indexes():
            print("note: index definitions differ:")
            for x in sorted(set(a.indexes()) ^ set(b.indexes())):
                print("   ", x)
        for name, db in (("reference", a), ("candidate", b)):
            dups = db.natural_key_duplicates()
            if dups:
                print(f"note: {name} has rows sharing a natural key (weaker comparison): {dups}")
        total_rows = exact_tables = 0
        print(f"{'table':28s} {'ref rows':>9s} {'new rows':>9s}  exact  resolved")
        for t in a.tables:
            if t not in b.columns:
                continue
            if a.columns[t] != b.columns[t]:
                print(f"{t}: COLUMNS DIFFER")
                failed = True
                continue
            ignored = [i for i, c in enumerate(a.columns[t]) if (t, c) in IGNORED_COLUMNS]

            def strip(rows):
                if not ignored:
                    return rows
                return [tuple(None if i in ignored else v for i, v in enumerate(r)) for r in rows]

            exact = sorted(strip(a.rows[t]), key=repr) == sorted(strip(b.rows[t]), key=repr)
            ca = collections.Counter(a.resolved_rows(t))
            cb = collections.Counter(b.resolved_rows(t))
            resolved = ca == cb
            total_rows += len(a.rows[t])
            exact_tables += exact
            print(
                f"{t:28s} {len(a.rows[t]):9d} {len(b.rows[t]):9d}  "
                f"{'yes' if exact else 'NO ':5s}  {'yes' if resolved else 'NO'}"
            )
            if not resolved:
                failed = True
                only_a, only_b = ca - cb, cb - ca
                print(f"    only in reference: {sum(only_a.values())}, only in candidate: {sum(only_b.values())}")
                cols = [c for c in a.columns[t] if c != a.pk[t]]
                for label, only in (("ref", only_a), ("new", only_b)):
                    for row in list(only)[: args.show]:
                        print(f"    {label}: " + ", ".join(f"{c}={v!r}"[:90] for c, v in zip(cols, row))[:900])
        print(
            f"{len(a.tables)} tables, {total_rows} reference rows; "
            f"{exact_tables} tables identical including keys; "
            + ("RESOLVED COMPARISON FAILED" if failed else "all tables equal after resolving keys")
        )
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
