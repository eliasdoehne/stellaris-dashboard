# Test plan: Stellaris 4.x save-format fixes

This plan checks, on real saves from your own game, the fixes from branch `fix-4x-save-format-regressions`, together with the changes from PR #224 that were merged before it. It is meant to be run locally after both are on `master`.

## 1. What is being tested

| # | Fix | Affected game versions | Before the fix |
|---|---|---|---|
| F1 | Planet districts are stored (type × level) | 4.0+ | No district rows |
| F2 | Planet buildings are stored (through zones / `buildings_cache`) | 4.0+ | No building rows |
| F3 | Fleet composition uses `ship_design_implementation` | 4.0+ | Fleet Composition graph always 0 |
| F4 | Living Metal, Zro, Dark Matter budgets read the `sr_` names | 3.12+ | Those three budget graphs always 0 |
| F5 | Faction support reads `support_percent` | 3.12+ | Faction Support graph always 0 |
| F6 | Capital and sector capitals resolve colony IDs to planets | 4.4+ | Capital pointed at the system's star |
| F7 | Planet stats (stability, housing, amenities) come from the colony | 4.4+ | All 0 |
| F8 | Governors come from the colony (governor ledger events) | 4.4+ | No governor events |
| F9 | Leader home planet uses `background_planet` | 4.4+ | Home planet empty |
| F10 | Pops are split across their ethics | 4.5+ | 100% "no ethos" |
| F11 | Pops are split across their factions | 4.5+ | 100% "No faction". **The 4.5 save layout for this is unconfirmed; see §5.** |

Already covered automatically, so not repeated here:
- The 4.0.1 save in `test/saves` runs through CI.
- `test_real_save` asserts that F1, F2, F3 and F5 produce data.
- `test/save_format_test.py` covers every lookup helper with small synthetic inputs.

Your saves add what CI cannot: current game versions (4.4/4.5), mid-game content, and consecutive saves in which things change.

## 2. Strategy

1. **Automated tests first.** These catch environment problems before you spend time playing.
2. **Batch-import a purpose-built set of saves** into a separate output folder, then run the read-only DB check script (§6) on each game DB. This gives objective pass/fail numbers per fix.
3. **Cross-check against the game.** For the save dates you imported, compare the dashboard's values with what the game shows. This catches "non-zero but wrong".
4. **Live run.** Keep the dashboard running while you play for a couple of in-game years. This exercises incremental processing, change detection and ledger events.
5. **Upgrade path.** Process new saves into a copy of a database created by an older dashboard version.

Focus on the **current game version (4.5.x)**. That version exercises the 4.4+ and 4.5+ code paths, which only had an early-game save to check against so far. Rolling back to older game versions is optional, because CI covers the 4.0 path.

## 3. Saves to generate

Set **autosave to monthly** in the game options. The game removes old autosaves, so copy each game's save folder (`save games/<empire>_<id>/`) somewhere safe after every session.

| Set | Version | What to play | Must contain | Exercises |
|---|---|---|---|---|
| **S1: early** | 4.5.x | A new game with a regular (non-gestalt) empire. Keep the first ~12 monthly autosaves. | Capital, starting districts and buildings, a few ships | F1–F4, F6, F7, F9, F10 (baseline) |
| **S2: mid-game** | 4.5.x | Continue S1, or use an existing campaign, to roughly 2240+. Keep 12+ consecutive monthly autosaves. | **Factions formed** (most important). At least 3 colonies, at least 1 sector with a governor, a mixed fleet (corvettes, destroyers, cruisers…), and any income or upkeep of living metal, zro or dark matter. | F3–F11, especially F11 |
| **S3: changes** | 4.5.x | From a mid-game save, do one thing per month and keep each monthly save: build a district, build a building, appoint or replace a sector governor, build ships of a new hull size, relocate the capital if possible. | Before/after pairs for each action | Change detection, ledger events (F1, F2, F6, F8) |
| S4 *(optional)* | 4.5.x | A Nomads empire (arkship) and/or BioGenesis bioships, if you own them | Arkship colony; bioships | Arkship colonies are skipped without errors; bioship growth-stage sizes (F3) |
| S5 *(optional)* | 4.4 / 4.0–4.3 via a Steam beta branch | Any short game | — | Older code paths on real data |
| S6 | Existing DB | Copy an existing `output/db/<game>.db` made with the old dashboard, plus that game's newest saves | — | Upgrade path (§4, Phase 4) |

Optional extras for F10: check one gestalt empire (hive mind or machine) to confirm its pops show as "no ethos". Either play one briefly, or turn on *Store data of all countries* and pick an AI gestalt in the country-perspective dropdown.

## 4. Procedure

### Phase 0: setup
1. `git pull` on `master`, then `uv sync`. This rebuilds the Rust parser.
2. `uv run pytest`. Everything should pass. The game-file name tests need your Stellaris install path configured; they are the only tests skipped in CI.
3. Use a separate output folder so test runs don't mix with your real DBs:
   - Set `base_output_path: <some empty folder>` in the `config.yml` of the directory you run from.
   - Also set `read_all_countries: true` (the *Store data of all countries* setting).
4. Copy the saves to test into a separate folder, laid out like the game's: `<test-saves>/<game folder>/*.sav`. The live game then can't add files mid-run.

### Phase 1: batch import and DB checks
1. `uv run stellarisdashboardcli parse-saves --save-path <test-saves> --threads 1`
2. Search the console output for `Traceback`, `Rolling back` and `Could not find`. Any rollback is a failure; note the save and the processor name.
3. For each game: `uv run python check_db.py <base_output_path>/db/<game folder>.db`. The script is in §6. Compare its output against the pass criteria in §5.

### Phase 2: cross-check against the game
Load the save matching the latest imported date (the script prints it). Compare with the game:
- **F1/F2:** pick two or three planets. The ledger planet page (Event Ledger → planet link) shows *Districts* and *Buildings*, which should match the planet view. For districts, the dashboard count equals the district's level shown in-game.
- **F3:** compare hull counts with the fleet manager.
  - The DB stores raw counts.
  - The *Fleet Composition* graph (Military tab) weights them: destroyer ×2, cruiser ×4, battleship ×8, titan ×16, colossus ×32.
  - Frigates are not counted yet.
- **F4:** compare the Budget tab's Living Metal, Zro and Dark Matter graphs with the in-game resource tooltip (income and upkeep by category).
- **F5:** *Faction Support* (Pops tab) against the support % on the in-game Factions screen. The dashboard stores a fraction: 0.25 means 25%.
- **F6:** the capital on the country's ledger page or the Codex matches the in-game capital.
- **F7:** *Stability / Free Housing / Free Amenities by Planet* (Planets tab) for two or three planets against the planet view.
- **F8:** the ledger shows "governed sector" entries for the current sector governors.
- **F9:** a leader's ledger page shows a *Home Planet*.
- **F10:** *Ethos Demographics* (Demographics tab) against the ethics distribution the game shows for your empire's pops.
- **F11:** *Faction Demographics* member counts against the in-game Factions screen.

### Phase 3: live run
1. `uv run stellarisdashboard`, with `save_file_path` pointing at the real save folder.
2. Play about two in-game years with monthly autosaves. Do some of the S3 actions along the way.
3. Watch the console for tracebacks or rollbacks. Reload the graph and ledger pages occasionally: new data should appear, and the ledger should show the actions you took.

### Phase 4: upgrade path
Point `base_output_path` at a copy of an existing DB folder (S6) and import that game's newer saves. Expect these **one-time effects** on 4.4+ games; they are not bugs:
- A "capital relocation" entry on the first new save. The stored capital moves from the star to the real planet.
- Districts and buildings appear on planet pages from the first new save on. Older history is not backfilled.
- Per-planet graphs start new, correctly named series next to the old wrongly named ones.

If you want clean history, re-import the game from scratch instead.

### Phase 5: smoke checks for the PR #224 changes (in the combined build)
- The **Codex** page (sidebar, or `/codex/<game folder>`) lists empires and wars and links to their ledgers.
- **Timelapse export** (galaxy map page) returns immediately with a "started/queued" message and finishes in the background. Two exports in a row are queued, not run in parallel.
- **Policy changes** and **ruler changes** appear in the ledger for your own empire.
- Settings: applying invalid numeric values ignores them and logs a warning to the console, instead of showing an error page.
- `/history/<game folder>?min_date=abc` does not error.
- Market price graphs show sensible prices per resource.

## 5. Pass criteria

| Fix | Check (§6 output) | Pass |
|---|---|---|
| F1 | 1. Districts | Non-empty. City, mining, generator and farming appear in plausible totals. |
| F2 | 2. Buildings | Non-empty. Capital buildings appear. |
| F3 | 3. Fleet composition | Non-zero once you have military ships. Matches the fleet manager (frigates excluded). |
| F4 | 4. Strategic budgets | Non-zero whenever the game shows living metal, zro or dark matter income or upkeep. |
| F5 | 8. Faction support | Support > 0 for your factions. Matches in-game % ÷ 100. |
| F6 | 5. Capital | The planet class is a real planet type, not a star (`pc_*_star`). |
| F7 | 6. Planet stats | No planets with stability, housing *and* amenities all 0. |
| F8 | 10. Event counts | `governed_sector` > 0 when sectors have governors. |
| F9 | 9. Leaders with home planet | The large majority of leaders have one. |
| F10 | 7. Pops by ethos | Several ethics for a regular empire. The total equals the empire's pop count. |
| F11 | 8. Pops by faction | With factions present in-game, pops are spread across them with plausible sizes. |

**If F11 fails** (everything is "No faction" although factions exist in-game), the 4.5 layout differs from what the code accepts. Run the snippet below on one affected save and send the output. It prints a few pop groups only, not the whole save:

```python
# faction_sample.py, run with: uv run python faction_sample.py <path to .sav>
import json, sys
import rust_parser

gs = rust_parser.parse_save_file(sys.argv[1])
print("version:", gs.get("version"))
groups = [g for g in gs.get("pop_groups", {}).values() if isinstance(g, dict) and g.get("factions")]
print(f"{len(groups)} pop groups with non-empty 'factions'")
for g in groups[:3]:
    print(json.dumps({k: g.get(k) for k in ("key", "size", "ethos", "factions", "fractions", "wanted_factions")}, default=str))
factions = gs.get("pop_factions", {})
sample = next((f for f in factions.values() if isinstance(f, dict)), None) if isinstance(factions, dict) else None
print("pop_factions sample keys:", sorted(sample) if sample else None)
```

## 6. DB check script

Save as `check_db.py` and run with `uv run python check_db.py <path to game .db>`. It opens the database read-only and reports on the latest player data.

```python
"""Read-only checks for a Stellaris Dashboard game database.

Usage: uv run python check_db.py output/db/<game_id>.db
"""
import sqlite3
import sys


def days_to_date(days):
    return f"{2200 + days // 360:04}.{days % 360 // 30 + 1:02}.{days % 30 + 1:02}"


con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
q = lambda sql, *args: con.execute(sql, args).fetchall()

latest = q(
    "SELECT cd.country_data_id, cd.date FROM country_data cd JOIN country c ON c.country_id = cd.country_id"
    " WHERE c.is_player = 1 ORDER BY cd.date DESC LIMIT 1"
)
if not latest:
    sys.exit("No player country data found (observer mode or empty DB?)")
cd_id, date = latest[0]
print(f"Latest player data: {days_to_date(date)}  ({q('SELECT COUNT(*) FROM gamestate')[0][0]} gamestates in DB)\n")

print("1. Districts (type: total count over all planets)")
for name, n in q(
    "SELECT d.text, SUM(x.count) FROM planet_district x JOIN shared_description d"
    " ON d.description_id = x.description_id GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
):
    print(f"     {name}: {n}")

print("2. Buildings (type: total count over all planets)")
for name, n in q(
    "SELECT d.text, SUM(x.count) FROM planet_building x JOIN shared_description d"
    " ON d.description_id = x.description_id GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
):
    print(f"     {name}: {n}")

print("3. Player fleet composition (corvette, destroyer, cruiser, battleship, titan, colossus):")
print("    ", q(
    "SELECT ship_count_corvette, ship_count_destroyer, ship_count_cruiser, ship_count_battleship,"
    " ship_count_titan, ship_count_colossus FROM country_data WHERE country_data_id = ?", cd_id)[0])

print("4. Player net living metal / zro / dark matter (sum of budget items):")
print("    ", [round(v or 0, 2) for v in q(
    "SELECT SUM(net_living_metal), SUM(net_zro), SUM(net_dark_matter) FROM budget_item"
    " WHERE country_data_id = ?", cd_id)[0]])

print("5. Player capital (name, planet class):")
print("    ", q("SELECT p.planet_name, p.planet_class FROM country c JOIN planet p"
              " ON p.planet_id = c.capital_planet_id WHERE c.is_player = 1"))

print("6. Player planet stats: planets, with stability 0, with free housing 0, with free amenities 0")
print("    ", q("SELECT COUNT(*), SUM(stability = 0), SUM(free_housing = 0), SUM(free_amenities = 0)"
              " FROM planetstats WHERE countrydata_id = ?", cd_id)[0])

print("7. Player pops by ethos")
for name, n in q(
    "SELECT d.text, p.pop_count FROM popstats_ethos p JOIN shared_description d"
    " ON d.description_id = p.ethos_description_id WHERE p.country_data_id = ? ORDER BY 2 DESC", cd_id
):
    print(f"     {name}: {n}")

print("8. Player pops by faction")
for name, n, support in q(
    "SELECT f.faction_name, p.pop_count, p.support FROM popstats_faction p JOIN political_faction f"
    " ON f.faction_id = p.faction_id WHERE p.country_data_id = ? ORDER BY 2 DESC", cd_id
):
    print(f"     {name[:70]}: {n} (support {support})")

print("9. Leaders with a home planet:", q("SELECT SUM(planet_id IS NOT NULL), COUNT(*) FROM leader")[0])
print("10. Event counts:", dict(q(
    "SELECT event_type, COUNT(*) FROM historical_event WHERE event_type IN"
    " ('governed_sector', 'governed_planet', 'capital_relocation', 'colonization') GROUP BY 1")))
```

Names in the output are raw localisation keys (for example `{"key": "SPEC_Alari_planet"}`); the dashboard UI renders them.

## 7. Known limitations (not failures)

- **Frigates** are not counted in fleet composition. This needs a new DB column and is a separate change.
- **Zones and district specializations** (4.0) are not stored. Only districts and buildings are.
- **History is not backfilled** in existing DBs. See Phase 4.
- **Nomad arkship colonies** have no planet, so they are left out of per-planet stats and capital tracking.

## 8. What to send back if something fails

- The console output around the failure: the traceback, or the `Rolling back` line with the save date.
- The `check_db.py` output for the affected game.
- The game version (shown in the save's `meta`, or the launcher) and which DLCs are active.
- For F11, the `faction_sample.py` output.
- The `.sav` file itself only if you're comfortable sharing it.
