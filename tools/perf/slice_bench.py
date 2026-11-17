"""
Evaluation prototype: write one slice of the timeline (CountryData economy totals + BudgetItem rows)
(a) in Python with raw sqlite3 and no ORM, (b) entirely in Rust (tools/perf/rust_slice, rusqlite),
and compare the rows and the time with the database built by the unmodified importer.

    uv run python tools/perf/slice_bench.py <corpus saves dir> <baseline db dir> <scratch dir> [game prefix] [every nth save]
"""
import collections, pathlib, sqlite3, sys, time

import rust_parser, rust_slice
from stellarisdashboard import datamodel

TOTALS = ["energy", "minerals", "alloys", "consumer_goods", "food", "trade", "unity", "influence",
          "physics_research", "society_research", "engineering_research", "biomass"]
ITEMS = ["energy", "minerals", "food", "alloys", "consumer_goods", "trade", "volatile_motes", "exotic_gases",
         "rare_crystals", "living_metal", "zro", "dark_matter", "nanites", "minor_artifacts", "astral_threads",
         "biomass", "physics_research", "society_research", "engineering_research", "unity", "influence"]
SR = {"living_metal", "zro", "dark_matter"}
REAL = {"default", "fallen_empire", "awakened_fallen_empire"}
SCHEMA = f"""
CREATE TABLE shared_description(description_id INTEGER PRIMARY KEY, text VARCHAR(500));
CREATE INDEX ix_sd ON shared_description(text);
CREATE TABLE countrydata(country_data_id INTEGER PRIMARY KEY, date INTEGER, country_id_in_game INTEGER,
  {", ".join(f"net_{r} FLOAT" for r in TOTALS)});
CREATE TABLE budget_item(budget_item_id INTEGER PRIMARY KEY, country_data_id INTEGER, budget_item_description_id INTEGER,
  {", ".join(f"net_{r} FLOAT" for r in ITEMS)});
CREATE INDEX ix_bi ON budget_item(country_data_id);
"""


def new_db(path):
    path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        pathlib.Path(str(path) + suffix).unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def num(v):
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, list) and v and isinstance(v[0], (int, float)):
        return v[0]
    return 0.0


def python_raw(con, gs, date, descriptions):
    """The logic of CountryDataProcessor._extract_country_economy, writing tuples with executemany."""
    cur = con.cursor()
    n = 0
    for country_id, country in sorted(gs["country"].items()):
        if not isinstance(country, dict):
            continue
        balance = country.get("budget", {}).get("current_month", {}).get("balance", {})
        totals = [0.0] * len(TOTALS)
        rows = []
        if isinstance(balance, dict):
            real = country.get("type") in REAL
            for name, values in balance.items():
                if name == "none" or not values:
                    continue
                res = [num(values[r]) if r in values else 0.0 for r in TOTALS]
                totals = [a + b for a, b in zip(totals, res)]
                if not real:
                    continue
                desc_id = descriptions.get(name)
                if desc_id is None:
                    cur.execute("INSERT INTO shared_description(text) VALUES (?)", (name,))
                    desc_id = descriptions[name] = cur.lastrowid
                rows.append((desc_id, *(
                    (values["sr_" + r] if "sr_" + r in values else values.get(r, 0.0)) if r in SR
                    else (num(values[r]) if r in values else 0.0) if r in TOTALS
                    else values.get(r, 0.0)
                    for r in ITEMS
                )))
        cur.execute(f"INSERT INTO countrydata VALUES (NULL, ?, ?, {','.join('?' * len(TOTALS))})", (date, country_id, *totals))
        cd_id = cur.lastrowid
        cur.executemany(f"INSERT INTO budget_item VALUES (NULL, {cd_id}, {','.join('?' * (1 + len(ITEMS)))})", rows)
        n += len(rows)
    con.commit()
    return n


def slice_rows(con, date, baseline):
    """Multiset of (country id in game, item name, values...) and of the per-country totals."""
    if baseline:
        join = ("JOIN country_data cd ON bi.country_data_id = cd.country_data_id JOIN country c ON cd.country_id = c.country_id "
                "JOIN shared_description sd ON sd.description_id = bi.budget_item_description_id")
        cid, tjoin = "c.country_id_in_game", "FROM country_data cd JOIN country c ON cd.country_id = c.country_id"
    else:
        join = ("JOIN countrydata cd ON bi.country_data_id = cd.country_data_id "
                "JOIN shared_description sd ON sd.description_id = bi.budget_item_description_id")
        cid, tjoin = "cd.country_id_in_game", "FROM countrydata cd"
    items = con.execute(f"SELECT {cid}, sd.text, {', '.join('bi.net_' + r for r in ITEMS)} FROM budget_item bi {join} WHERE cd.date = ?", (date,)).fetchall()
    totals = con.execute(f"SELECT {cid}, {', '.join('cd.net_' + r for r in TOTALS)} {tjoin} WHERE cd.date = ?", (date,)).fetchall()
    return collections.Counter(items), collections.Counter(totals)


def main():
    saves_dir, base_dir, scratch = map(pathlib.Path, sys.argv[1:4])
    prefix = sys.argv[4] if len(sys.argv) > 4 else ""
    nth = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    t = collections.defaultdict(float)
    n_saves = n_rows = mismatches = 0
    for game in sorted(saves_dir.iterdir()):
        if not game.name.startswith(prefix):
            continue
        base = sqlite3.connect(f"file:{base_dir / (game.name + '.db')}?mode=ro", uri=True)
        py_db, rs_db = scratch / "slice_py.db", scratch / "slice_rs.db"
        py_con = new_db(py_db); new_db(rs_db).close()
        descriptions = {}
        for save in sorted(game.glob("*.sav"))[::nth]:
            c0 = time.process_time()
            gs = rust_parser.parse_save_file(str(save))
            c1 = time.process_time()
            date = datamodel.date_to_days(gs["date"])
            rows = python_raw(py_con, gs, date, descriptions)
            c2 = time.process_time()
            del gs
            c3 = time.process_time()
            t_read, t_parse, t_extract, rs_rows = rust_slice.budget_slice(str(save), str(rs_db), date)
            c4 = time.process_time()
            t["py parse to dict"] += c1 - c0; t["py extract+write"] += c2 - c1; t["py free dict"] += c3 - c2
            t["rust total"] += c4 - c3; t["rust unzip"] += t_read; t["rust parse (no python objects)"] += t_parse
            t["rust extract+write"] += t_extract
            n_saves += 1; n_rows += rows
            rs_con = sqlite3.connect(rs_db)
            want = slice_rows(base, date, True)
            for label, con in (("python-raw", py_con), ("rust", rs_con)):
                got = slice_rows(con, date, False)
                if got != want:
                    mismatches += 1
                    print(f"MISMATCH {label} {game.name} {save.name}: items {sum((got[0]-want[0]).values())}/{sum((want[0]-got[0]).values())} "
                          f"totals {sum((got[1]-want[1]).values())}/{sum((want[1]-got[1]).values())}")
            rs_con.close()
    print(f"saves={n_saves} budget_item rows={n_rows} ({n_rows / n_saves:.0f}/save) mismatching save comparisons={mismatches}")
    for k, v in t.items():
        print(f"  {k:32s} {1000 * v / n_saves:8.1f} ms/save")


main()
