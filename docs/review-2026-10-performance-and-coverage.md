# Stellaris Dashboard: performance review and game-coverage gaps

*Review date: 2026-10-04. Code reviewed at commit `795afa3` (v8.0.0).*

*Measurements use the 4.0.1 save in `test/saves`: a 52 MB gamestate in the year 2312, with 44 countries, 6,353 planets, 1,185 pop groups and 25 DLCs enabled. They were taken in a 4-vCPU cloud container with Python 3.14, SQLAlchemy 2.0.50 and Dash 4.2. Treat the absolute numbers as indicative and compare the ratios. That save was made with console commands (`cheated_on_save=yes`), which inflates stockpiles but does not affect performance. The format checks in §3 also used real **4.4.6** and **4.5.0** saves.*

---

## Summary

Three findings drive the recommendations.

1. **Most of the per-save cost is spent moving data, not using it.** The Rust parser turns the whole gamestate (2.7 M nodes) into Python objects in a worker process, then pickles it and sends it to the main process. That costs about 5.5–6.5 s per late-game save. The DB ingestion that follows takes about 1.5 s. Parsing only the parts the processors read, in-process, should cut the pre-ingestion cost to around 1 s and roughly halve peak memory.
2. **Several graphs and ledger sections are silently wrong on current game versions.** Every save below ingests without an error, so nothing alerts the user. All of it was verified by running the ingestion on real 4.0.1, 4.4.6 and 4.5.0 saves.
   - **4.0+:** 0 district and 0 building rows. Districts and buildings moved into new `districts` and `zones` tables.
   - **4.0+:** the **Fleet Composition graph is all zeros**, because ships now reference designs through `ship_design_implementation`.
   - **Since at least 3.12:** the **Living Metal, Zro and Dark Matter budget graphs are always zero**. The save uses `sr_`-prefixed names.
   - **4.4+:** a new `colony` layer means "planet" IDs in pops, capitals and sectors are now colony IDs. The player's capital resolves to the *star*, and planet stability, housing and amenities graphs are flat zero.
   - **4.5 (released 2026-09-22):** pop groups no longer carry a single ethic or faction. **All pops land in "no ethos" / "No faction"**, so the ethos and faction graphs collapse to one bucket.
3. **Most 2022–2026 DLC systems are in the save but absent from the dashboard.** The test save contains resource stockpiles, the game's own **Empire Timeline** (51 distinct milestone types), situations, subject agreements, astral rifts, cosmic storms, Grand Archive exhibits, first-contact progress, espionage operations, GC resolutions and megastructures. None of these is ingested. Eight ledger event types exist in the schema but are never emitted.

---

## Shortlist

Effort key: **S** is about a day or less, **M** is 2–5 days, **L** is 1–3 weeks. "Measured" means observed on the test save. "Est." means an engineering estimate.

The numbers are for reference, not strict priority. If only two things get done, do **#3**, because current game versions get wrong data. Then do **#1**, the largest speed and memory win. §2.9 gives a suggested sequence.

| # | Change | Problem it fixes | Expected effect | Effort |
|---|---|---|---|---|
| 1 | **Filtered, in-process parsing.** The Rust side builds Python objects only for the paths the processors read. Parse in a thread with the GIL released, and drop the pickle hop. | The full tree is converted and pickled across processes on every save | Est. 5.5–6.5 s → ~1 s per save. Peak memory roughly halved. | M |
| 2 | **Never drop saves silently.** Queue new saves instead of marking the overflow as processed. | Saves arriving while a parse is in flight are skipped for good | No data gaps | S |
| 3 | **Fix the 4.x data-model regressions**, and add the 4.4/4.5 saves to `test/saves` with assertions that these outputs are non-empty. Read districts, zones and buildings from the new tables. Resolve ship sizes through `ship_design_implementation`. Map colony IDs to planets (4.4+). Read the 4.5 ethic and faction shares. Fix the `sr_` resource names. | Several graphs and ledger sections are empty or wrong on current saves. Measured on 4.0.1, 4.4.6 and 4.5.0; see §3.1. | Correct data on current game versions | M |
| 4 | **Ingestion hygiene.** Stable hashes, `no_autoflush`, lookup maps loaded once per save, a few indexes, and a fix for the per-planet and per-country query loops. | ~2,600–3,900 SQL statements per save. 4,481 autoflushes on first import. The first save after a restart is 2.4× slower. | Measured: the restart penalty drops from 4.0 s to 1.9 s with a fixed hash seed. Est.: steady state 1.5 s → ≤0.5 s, first import 9 s → 2–3 s. | M |
| 5 | **Smaller graph payloads.** Send each figure once, replace per-point hover strings with `hovertemplate`, turn on compression, and cache figure JSON per save. | Each figure is serialized twice and about 42% of the bytes are preformatted hover text. Compression is off. | Est. 5–10× fewer bytes per tab switch. The Economy tab alone is about 0.75 MB at 40 saves and grows linearly up to the 500-point cap. | S |
| 6 | **Thread-safe in-memory caches keyed by country perspective.** | Shared plot cache is mutated by the monitor thread and request threads without a lock. Every save resets a user-chosen perspective, which triggers two full reloads. | Removes full reloads after each save and removes data races | S |
| 7 | **Ledger: filter in SQL, eager-load, lazy-load sections with htmx.** | The main page loads every event for every country and filters in Python. Up to 8 lazy loads per event. | Page cost tracks what is shown, not the game's length | M |
| 8 | **Generic metric storage**, as long-format rows or a JSON blob per `CountryData`. | One column per resource. Each new resource (Menace, Logic, Valor…) needs a schema change. | Prerequisite for most coverage additions | M |
| 9 | **Event-detection bug fixes**: governor duplicates, missed level-ups, noisy war warnings. | Spurious and missing ledger rows (measured: governor events grow with save count on identical state) | Correct ledger | S |
| 10 | **Save-folder watching** through OS events, with a "file is stable" check. | Recursive glob plus a `stat` of every save file, every 0.5 s | Lower idle disk and CPU use, no partial-file reads | S |
| 11 | **Galaxy map: precompute ownership intervals and recolor client-side.** | The shared graph is mutated per request (race). A timelapse re-queries the DB for every frame. | Instant slider scrubbing and faster timelapse export | M |
| 12 | **Strategic: retire Dash for the Flask + JSON + client-side charts pattern** the galaxy map already uses. | Each tab switch rebuilds every figure server-side. Global state is shared across requests. | Cacheable, compressible and simpler to make concurrent | L |
| 13 | **Ingest the Empire Timeline, stockpiles, total pops and situations first** (see the coverage section). | Large coverage gaps | Biggest coverage gain for the effort | S–M |

---

## 1. Where the time goes today (measured)

Steady-state path for one save:

```
.sav ─unzip─▶ nom parser (Rust) ─▶ full Python dict ─pickle─▶ main process ─▶ 29 processors ─ORM─▶ SQLite
                  1.2 s              +1.3–3.0 s           +2–3 s                    ~1.5 s
                                                                                     │
                     browser ◀─Plotly JSON (×2 per figure)─ Dash callback ◀─ PlotDataManager (in-memory)
```

| Stage | Measured |
|---|---|
| Pure Rust `nom` parse into the `Value` tree | **1.2 s** (2,715,825 nodes) |
| `jomini::TextTape` tokenizing the same gamestate (for comparison) | **0.12 s** |
| Parse plus Python conversion, in-process | 2.5–4.3 s. Peak RSS 665 MB. |
| Pool round trip as the app runs it (worker parse, pickle, unpickle) | **5.5–6.5 s**. The pickle is 41 MB (2.0 s to dump, 1.5 s to load). |
| Timeline ingestion, first save into an empty DB | ~9 s, with 4,452 statements and 4,481 autoflushes |
| Timeline ingestion, steady state, player only | **1.4–1.6 s**, 2,577 SQL statements per save |
| Timeline ingestion, steady state, "store data of all countries" on | 1.8 s, 3,905 statements per save |
| First save after a dashboard restart | **3.8–4.0 s**. With `PYTHONHASHSEED=0` it is 1.9 s; see §2.3. |
| Graph tabs, 40 saves, all countries | 3.5 MB of figure JSON across all tabs. Economy tab is ~750 KB. 1.5 MB of the total is hover-text arrays. |
| Ledger main page, 2,934 events | 300–600 ms, 414 KB of HTML |
| Galaxy map per-date endpoint, 602 systems | 20–30 ms. Not a bottleneck at this size. |

Largest gamestate sections by node count: `pop_jobs` 662k, `planets` 611k, `ships` 396k, `country` 356k, `fleet` 222k, `construction` 157k, `ship_design` 115k. The dashboard does not read `construction` at all. From `ships`, `fleet`, `ship_design` and `pop_jobs` it reads only 2–3 fields per entry.

**How much of the save is actually used.** Two ingests were run with the gamestate wrapped in an access-recording dict. Across them, the processors touched **2,091 distinct paths covering 16.2% of the nodes** (440k of 2.7M). This is an upper bound, because lists of dicts were counted in full. The other ~84% is converted to Python objects, pickled, unpickled and thrown away on every save. Filtering only at the top level would save about 10%; the gain comes from filtering nested paths inside `pop_jobs`, `planets`, `ships`, `country` and `fleet`.

In steady state, each late-game save costs roughly **7–8 s of wall time and two full copies of the gamestate in memory**: the worker's copy and the unpickled one in the main process. Only about 1.5 s of that is the ingestion that produces stored data.

---

## 2. Recommendations in detail

### 2.1 Parsing pipeline (`parsing/rust_parser`, `parsing/save_parser.py`)

**Problem.** `parse_save_file` converts every node into Python objects (`parser.rs: value_to_pyobject`). `ContinuousSavePathMonitor` and `BatchSavePathMonitor` run this inside `multiprocessing` workers, so the whole tree is pickled back to the main process. The recursion-limit bump in `save_parser.py` exists only because of that pickling.

**Proposal, in increasing order of effort.**

1. **Path filter (the biggest win, low risk).**
   - Pass a filter spec to `parse_save_file`. It is a nested dict of the keys that are read, with `*` meaning "any child id":
     ```json
     {"ships": {"*": {"fleet": true, "ship_design_implementation": true, "leader": true}},
      "ship_design": {"*": {"growth_stages": true}},
      "pop_jobs": {"*": {"type": true, "pop_groups": true}}}
     ```
   - Skipped subtrees are only scanned for matching braces, never materialized.
   - Two independent indications of the gain:
     - Issue #134 (StellarMaps' parser) reported **~6× faster parsing (3 s → 0.5 s)** with this approach.
     - The access recording in §1 shows only 16% of nodes are needed.
   - Because the processors still receive the same dict shape, no Python processor needs to change.
   - Generate the first spec by recording key accesses while running the test save through `TimelineExtractor`.
   - Keep it honest with a test that wraps the gamestate in an access-recording dict and fails when a processor reads a path the spec does not cover.
2. **Parse in-process and drop the pickle hop.**
   - Run the Rust tokenize-and-filter step under `py.allow_threads(...)` from a worker thread in the main process.
   - Only the (now small) Python object construction needs the GIL.
   - This removes 2–3 s per save, the second in-memory copy, and the `multiprocessing` / `freeze_support` complexity in the PyInstaller build.
   - Batch import (`parse-saves`) still parallelizes, because tokenization releases the GIL.
3. **Replace `nom` with `jomini`.**
   - `jomini` is MIT-licensed and battle-tested by pdx-tools on Paradox formats.
   - Measured on the same file: 0.12 s to tokenize versus 1.2 s for the current parser.
   - It is built for this format's quirks (colors, escaped strings, duplicate keys, unusual operators), which the current grammar handles case by case. Confirm the edge cases the current parser fixes, such as valueless keys, using the existing test suite.
   - Two current behaviors must be reproduced in the conversion layer:
     - repeated keys become lists, with the nested-list special case in `parse_map_inner`;
     - integer-looking keys become `int`.
   - The existing `parser_test_cases.py` suite is the regression net.
4. **Optional later step: aggregate hot loops in Rust.** For example, collapse `pop_jobs` into a `{pop_group_id: [(job, amount)]}` map. This is only worthwhile after steps 1–3, if profiling still shows these loops.

Smaller items:
- Intern repeated dict keys (`PyString::intern`) if full conversion stays.
- `read_to_string` on a 50 MB+ entry is fine. `jomini` also works directly on `&[u8]`.

### 2.2 Save monitoring (`save_parser.py`, `cli.py`)

- **Data loss.**
  - `ContinuousSavePathMonitor.get_gamestates_and_check_for_new_files` stops submitting once `threads` parses are pending (`save_parser.py:176`).
  - It then marks *all* new files as processed (`:182`), so the overflow is never parsed.
  - With the default `threads=1`, any two saves arriving during one 7–8 s processing cycle lose one of them.
  - Fix: keep a FIFO backlog. If the backlog is deliberately thinned, log which saves were skipped.
- **Polling cost.** Every 0.5 s (`config.py:201`) the monitor globs `**/*.sav` across *all* games and `stat`s each file (`save_parser.py:119`). Use `watchdog` (inotify, FSEvents or ReadDirectoryChangesW), or at least scan only the directories with a recent mtime.
- **Partial writes.** Before parsing, wait until a file's size and mtime have been stable for about 1 s. This avoids reading a half-written zip.

### 2.3 Ingestion (`parsing/timeline.py`, `datamodel.py`)

The processor design (dependency-ordered processors that diff the save against the DB) is sound. The cost is in how each processor talks to the ORM.

1. **Stable change-detection hashes.**
   - `_check_and_update_hash` stores `hash(frozenset(...))` in the DB (`timeline.py:1615`).
   - Python's `str` hash is randomized per process, so after every restart each planet's district, building, deposit and modifier hashes mismatch. The first save then re-diffs all 6,353 planets.
   - Measured: 4.0 s for that first save, versus 1.9 s with a fixed seed.
   - Fix: use `zlib.crc32` or `hashlib.blake2b` over a sorted, serialized form.
   - `Bypass.network_id = hash("lgate")` (`:411`) has the same fragility. It is harmless today only because bypasses are rebuilt every save.
2. **Turn off autoflush during processing.** Wrap `_process_gamestate` in `with session.no_autoflush:` and flush once per processor, which the code already does. This removes the flush-before-every-query pattern: 4,481 flushes on the first import. It also silences the `SAWarning` about `CountryData` being added during autoflush, a latent correctness risk.
3. **Load lookups once per save instead of querying per row.**
   - `CountryProcessor` runs one query per country (`:460`), and `SpeciesProcessor` one per species (`:1084`). Load each into a dict up front.
   - `PopStatsProcessor` queries `Planet` once per planet per country (`:3954`). `PlanetProcessor` already provides that dict.
   - `PopStatsProcessor` also loops over all pop groups *once per country* (`:3797`), which is O(countries × pop_groups). Group by owner in a single pass.
   - Event "find the open event" queries (envoys, councilors, governors, wars, fleet commands, GC membership) should come from one query per event type per save, indexed in a dict by `(country_id, leader_id, …)`.
4. **`Country.get_most_recent_data()`** (`datamodel.py:773`) loads a country's entire `CountryData` history to read the last row. It is called on many event paths. Pass in the `CountryData` created this save, which `CountryDataProcessor` already holds, or use `ORDER BY date DESC LIMIT 1`.
5. **Write amplification.**
   - `GovernmentProcessor` rewrites `end_date_days` for every country on every save (`:2374`). Store open-ended governments with `NULL` and close them only when they change.
   - `_update_planet_modifiers` calls `session.refresh()` after each deleted modifier (`:1585`). Delete in bulk, then refresh once.
6. **Indexes.** `historical_event` has indexes only on `event_type`, `start_date_days`, `country_id` and `leader_id`. Hot lookups also filter by `planet_id`, `system_id`, `war_id`, `description_id` and `end_date_days IS NULL`. Add `(event_type, country_id, end_date_days)` and single-column indexes on `planet_id` and `war_id`. Add `(war_id, country_id)` on `war_participant`.
7. **Cheap preflight.** `_check_if_gamestate_exists` loads every `GameState` row (`:98`). Use `SELECT 1 … WHERE date = ?`.

A longer-term option: keep an in-memory snapshot of the previous save's relevant state between saves (the monitor runs for hours), and detect events by diffing dict to dict. The DB then becomes an append-mostly log. That removes most per-save reads entirely. After a restart, rebuild the snapshot from the DB once.

### 2.4 Storage and schema

- **Generic metrics.**
  - `CountryData.net_*` and the wide `BudgetItem` table have one column per resource. Newer resources (Menace, Logic, Valor, and whatever Willpower adds) and new counters (stockpiles, pop totals, naval capacity) each need a schema migration plus a new data container class.
  - Proposal: a `metric(country_data_id, metric_key_id, value)` table, or a compressed JSON blob per `CountryData`, plus one generic data container parameterized by metric key.
  - This is the enabler for most of the coverage section.
- **Record the game version per `GameState`**, from `version` in the gamestate or meta. Graphs can then mark version changes, because 4.0 (pops ×100) and 4.3† (tech costs halved, naval capacity ×5, empire size changes) make series jump. († = per patch notes, not verified against a save.) Processors can also branch on format.
- Alembic autogenerate runs on every engine setup (`datamodel._setup_engine`). This is acceptable, but a version stamp would skip it when nothing changed, and would allow real data migrations, such as backfilling districts after fix #3.

### 2.5 Graph dashboard (`dashboard_app/graph_ledger.py`, `visualization_data.py`)

1. **Payload.**
   - `update_content` sends a compact figure in the grid **and** a full figure in `dcc.Store` for every plot (`graph_ledger.py:292`), so every byte goes out twice.
   - Every trace also carries a per-point preformatted `text` array (`:417`, `:476`, `:489`): 1.5 MB of 3.5 MB at only 40 saves.
   - Fixes:
     - Use `hovertemplate` (for example `"%{y:.2f} · %{fullData.name}<extra>%{x:.2f}</extra>"`).
     - Build the modal figure from the grid figure's data, either with a clientside callback that swaps the layout or by fetching on demand.
     - Turn compression on (`compress=False` at `:65`; Flask-Compress is already a dependency and unused). Plotly JSON compresses very well.
2. **Downsampling.** "Every n-th gamestate" thinning drops peaks (wars, deficits). Use LTTB per trace at the same point budget.
3. **Cache figure JSON** keyed by `(game, tab, perspective, last_gamestate_date, settings)`. Today every tab switch rebuilds every figure from scratch.
4. **Correctness and concurrency.**
   - `PlotDataManager` is a single global per game, mutated by the monitor thread (`cli.py:88`) and by request threads (waitress serves concurrently) with no lock.
   - The monitor calls `get_current_execution_plot_data(game_name)` with `country_perspective=None`, which resets any perspective a user selected (`visualization_data.py:95`). Every new save then causes **two full reloads**: one to `None`, one back.
   - Fix: key managers by `(game, perspective)` in a small LRU, guard each with a lock, and have the monitor just mark them dirty.
   - `config.CONFIG.normalize_stacked_plots` is set per request on the global config object (`graph_ledger.py:233`). Pass it as a parameter instead.
5. **`get_available_games_dict()`** opens *every* game DB and runs three queries per game. Three callbacks call it on each page load (`graph_ledger.py:178`, `:204`, `:241`), as do the ledger and galaxy pages. Cache it with mtime-based invalidation, or query only the current game.
6. **Initial load** walks containers × countries × gamestates with lazy relationship loads (`budget`, `pop_stats_*`). It took 0.6 s at 40 saves. Eager-load these with `selectinload` in `get_gamestates_since`, or read them with one query per table.
7. **Live updates.** The page only refreshes on reload. A lightweight "last gamestate date" poll or SSE would let the open tab refresh after each save. That becomes cheap once item 3 is in place.

### 2.6 Event ledger (`dashboard_app/history_ledger.py`)

- `get_event_and_link_dicts` runs one query per key object (every country on the main page). It filters by scope, visibility and country type in Python (`EventFilter.include_event`) and lazily loads `country`, `target_country`, `leader`, `system`, `planet`, `faction`, `war`, `fleet` and `combat` for each event.
- Move the filters into the SQL `WHERE` clause and eager-load relationships with `selectinload`.
- Render each country's section lazily with htmx, which is already vendored: show headers first and load events on expand or scroll. Cache rendered fragments keyed by the last gamestate date.
- `leader_details` calls `utils.get_most_recent_date` once per leader. Compute it once per request.

### 2.7 Galaxy map and timelapse

- Speed is fine at 602 systems: 20–30 ms per date.
- **Correctness:** `GalaxyMapData` is shared per game and `update_graph_for_date` mutates node attributes per request. Two concurrent slider requests can interleave.
- **Proposal:**
  - Build per-system ownership interval arrays once per game, and refresh them after each save.
  - Ship them with the geometry, together with a Voronoi adjacency list (ridge, system A, system B).
  - Recolor and recompute borders in JS: a border exists where `owner(A) != owner(B)`. Scrubbing then needs no server round trips.
  - The timelapse exporter can reuse the same arrays instead of querying the DB for every frame.
  - Storm positions (see §3) could become an extra layer.

### 2.8 Correctness bugs found during the review

| Bug | Where | Evidence |
|---|---|---|
| Governor events are duplicated over time. The "previous event" query filters only by type and sector description, not by planet, country or leader. | `timeline.py:1876–1884` | Feeding the *identical* state repeatedly grows `governed_sector` events: 29 → 117 → 204 at 3, 46 and 120 saves. |
| Leader level-ups are only recorded when another attribute (traits, name, class…) changes in the same save. `level` is missing from the change condition. | `timeline.py:1276–1292` | Code inspection |
| Districts and buildings are never stored on 4.x saves. | `timeline.py:1456`, `:1481` | 0 rows on the 4.0.1 save |
| Fleet composition is always zero on 4.x. Ship size is looked up through `ship["ship_design"]`, which no longer exists. | `FleetInfoProcessor._get_ship_class` | The sum of all `ship_count_*` is 0 |
| Living Metal, Zro and Dark Matter budgets are always zero. The save keys are `sr_living_metal`, `sr_zro` and `sr_dark_matter`. | `timeline.py:1039–1041` | DB sum 0.0. These keys appear in 23 budget categories in the 4.0.1 save. |
| On 4.4+, capital, planet stats and sector capitals use colony IDs as planet IDs. | `RulerEventProcessor._history_add_or_update_capital`, `PopStatsProcessor`, `SectorColonyEventProcessor` | The 4.4.6 and 4.5.0 player capitals resolve to the star. All `planetstats` stability and housing values are 0. |
| On 4.5, ethic and faction are read from `pop_group.key`, which no longer carries them. | `PopStatsProcessor` | 4.5.0 save: 100% of pops are "no ethos" / "No faction". |
| Wars that include dead countries log three warnings on every save. | `WarProcessor.update_war_participants` | Log output |
| Eight `HistoricalEventType`s are never emitted: `megastructure_construction`, `habitat_ringworld_construction`, `voted_for/against_resolution`, `sector_creation`, `planetary_unrest`, `species_rights_reform`, `discovered_new_system`. | `datamodel.py` | grep |
| `Config.normalize_stacked_plots` only exists after the first Dash callback has run. | `graph_ledger.py:233` | Raises `AttributeError` if figures are built before that callback |

### 2.9 Suggested order of work

1. **Correctness first (≈1 week).** Current game versions produce wrong graphs silently, so start here:
   - #3, the 4.x data-model fixes.
   - The bug fixes in §2.8.
   - Add the 4.4.6 and 4.5.0 saves to `test/saves` (check the source repository's license first). Assert that districts, fleet composition, ethos buckets and planet stats are non-empty.
2. **Quick performance wins (≈1 week):**
   - #2 (save dropping).
   - The hash, autoflush and preloading parts of #4.
   - #5 (payload) and #6 (cache safety).
   - Stockpiles, total pops and the Empire Timeline from §3.
3. **Pipeline (1–2 weeks):** #1 steps 1–2 (filtered, in-process parse) with the access-recording test.
4. **Coverage foundation (1 week):** #8 (generic metrics) and game version per gamestate, then the §3 additions in priority order.
5. **Structural (as appetite allows):** #1 step 3 (jomini), #7 (ledger), #11 (galaxy map), #12 (move off Dash).

Add a benchmark harness (parse, ingest ×N, render) on the test save to CI, so each step is measured rather than assumed. The scripts behind §1 can be turned into one.

---

## 3. Underserved game features

### 3.0 How this was assessed

- **Release catalog.** A research subagent compiled a catalog of releases from 2022 to October 2026 using web search (the Stellaris wiki, Paradox dev diaries, Steam and Paradox news, press). Direct page fetches were blocked in this environment, so details come from search-result excerpts. Items marked **†** rest on a single source, or on inference.
- **What the dashboard reads.** Each feature was checked against the keys that `timeline.py` actually reads.
- **What the save contains.** Each feature was also checked against real saves:
  - the **4.0.1 test save** (late game). Its `required_dlcs` include Overlord, First Contact, Galactic Paragons, Astral Planes, The Machine Age, Cosmic Storms and Grand Archive.
  - early-game **4.4.6** and **4.5.0** saves from the public test data of [stellaris-galaxy-forge](https://github.com/IanHeinrich/stellaris-galaxy-forge). The 4.5.0 one has BioGenesis, Nomads, Shadows of the Shroud and Infernals enabled.
  - 3.10.4 and 3.12.1 saves from the history of the test-save repository.
  - "✔ in save" below means the data was located in at least one of these. Because the 4.4 and 4.5 saves are early game, wars, federations and crisis content are empty in them.
- **Ranking.** Items are ranked by value to someone reading their game's history, times confidence that the data is in the save, divided by effort.

**Covered today, for reference:**
- *Graphs:* net income per resource; the player's budget breakdown; demographics and pop happiness, crime and power by species, job, stratum, ethos, faction and planet; tech count and survey progress; military power, fleet size and composition; victory score.
- *Ledger:* wars, battles and peace; leaders (recruited, died, traits, level, ruler, councilor, governor, envoy, fleet command); colonization, terraforming and destroyed planets; system expansion and conquest; diplomatic pacts and rivalries; Galactic Community and council membership; traditions, ascension perks, edicts, policies, government reforms, factions, agendas; research.
- *Map:* system ownership over time.

### 3.1 Fix first: 4.x data the dashboard still reads the 3.x way

Rows marked ✔ were checked against the real saves. For districts, fleet composition, the colony layer, pop groups and strategic resources, the "effect today" was reproduced by running `TimelineExtractor` on those saves and inspecting the resulting DB.

| Area | What changed | Effect today | Evidence |
|---|---|---|---|
| **Districts, zones, buildings** (4.0) | `planet.districts` is now a list of ids into the top-level `districts` table (`{type, level, zones}`). Buildings hang off `zones` (`{type, buildings}`). | `planet_district` and `planet_building` stay empty. Planet ledger pages show no districts or buildings. Zones, district development levels and specializations are invisible. | ✔ 0 rows after ingesting the 4.0.1 save. Code reads `planet["district"]` / `planet["buildings"]` (`timeline.py:1456`, `:1481`). |
| **Fleet composition** (4.0) | Ships reference `ship_design_implementation: {design, growth_stage}`. Designs carry `growth_stages[].ship_size` instead of `ship_size`. | Every ship resolves to size `None`. **The Fleet Composition graph is all zeros** and `Fleet.is_civilian_fleet` is wrong. Frigates (187 ships in the save) have no category even after the fix. | ✔ The sum of all `ship_count_*` columns is 0. Resolving through the new path yields corvette 400, frigate 187, destroyer 105, and so on. |
| **Colony layer** (4.4) | A new top-level `colony.<id>` holds pops, jobs, districts, governor, stability, amenities, housing and designation, plus `carrier = {type: planet\|ship, reference}`. `planets.planet.<id>` keeps only physical data and `colony=<id>`. `country.owned_planets`, `controlled_colonies`, `capital`, `sectors.*.local_capital`, `pop_groups.*.planet` and `pop_jobs.*.planet` hold **colony IDs**. | The capital resolves to the wrong object (the star, in both test saves). Planet stats read stability and housing from the wrong place (all 0). Planet names in the per-planet graphs and the capital-relocation events are wrong. Arkship colonies (Nomads) have a *ship* as their carrier. | ✔ 4.4.6, 4.5.0. Also documented by [stellaris-companion `colony_resolver.py`](https://github.com/gitmaan/stellaris-companion). |
| **Pop groups no longer split by ethic or faction** (4.5, released 2026-09-22) | `key` holds only `{species, category}`. New fields: `ethos = {ethics: [...], pops: [...]}`, `factions`, `fractions`, `ethics_attraction`. Countries gain 17-element per-ethic arrays (`ethics_distribution`, `pops_with_ethic`, …) whose index order is not stored in the save. | **All pops land in "no ethos" and "No faction".** The ethos and faction graphs collapse to one bucket. | ✔ 4.5.0: 133,554 of 133,554 pops |
| **Strategic resource names** | Budgets use `sr_living_metal`, `sr_zro`, `sr_dark_matter`. | Those three budget graphs are always 0. | ✔ 3.12.1, 4.0.1, 4.5.0 |
| **Number scale jumps** (4.0 pops ×100; 4.3 tech costs halved, naval capacity per ship ×5, empire size reworked†) | Series are not comparable across versions. | Graphs show unexplained cliffs. | Record `version` per gamestate and draw markers (§2.4). |
| **Unemployment strata removed** (4.4), **Civilians stratum added** (4.0) | Job and stratum categories changed. From 4.4, `pop_jobs` lists only real job slots (1,082 entries in the early 4.4.6 save). In 4.0.1 it held about 57 placeholder entries per planet (81,924 in the test save), which is why it is the largest section there. | A harmless leftover "unemployed" bucket. Civilians are already handled through the job and stratum data. | ✔ 4.0.1, 4.4.6 |
| **Leader home planet** (4.4) | `planet=` replaced by `background_planet = {type, reference}` | The "Home Planet" field on leader pages is empty | ✔ 4.4.6 |
| **Wars can be joined and left mid-war** (4.4†) | Participants change during a war. | `WarProcessor` only ever *adds* participants, so leaving a war is never recorded. | Code inspection |
| **Nomadic empires with no systems** (Nomads, 4.4†) | Ownership is not starbase-based. | The galaxy map and "controlled systems" show nomads as owning nothing. | Inference from the release notes† |

### 3.2 Additions available in today's saves (ranked; all ✔ in real saves)

| # | Feature (release) | Where it lives in the save | What to add | Effort |
|---|---|---|---|---|
| 1 | **Empire Timeline** (4.0 "Phoenix"). The game's own milestone log. | `country.<id>.timeline_events: [{date, definition, data: [ids…]}]`. The player has 154 entries. There are 51 distinct definitions across empires, for example `timeline_first_colony`, `timeline_megastructure`, `timeline_galactic_community_resolution`, `timeline_war_declared_attacker/defender`, `timeline_vassalized`, `timeline_joined_federation`, `timeline_first_astral_rift`, `timeline_first_storm_within_borders`, `timeline_first_arc_site`, `timeline_encountered_leviathan`, `timeline_deficit`, `timeline_elections`. | Import the entries as ledger events with their **exact dates**, deduplicated on `(country, date, definition)`. Skip the `timeline_event_year` markers (1,326 of them). Localize via the game's localisation keys and resolve `data` ids to links. This covers many DLC milestones in one go, backfills history when a game is imported mid-way, and is not limited to save granularity. | S–M |
| 2 | **Resource stockpiles** (all versions) | `country.modules.standard_economy_module.resources`, e.g. `{energy, minerals, alloys, trade, astral_threads, sr_dark_matter, …}` | Stockpile graphs per resource, across empires and for the player. This makes deficits and hoarding visible; today only net income is shown. | S |
| 3 | **Population and capacity totals** | `country.num_sapient_pops`, `employable_pops`, `used_naval_capacity`, `starbase_capacity` | A "Total pops" comparison graph (issue #192), naval and starbase capacity usage. | S |
| 4 | **Situations**: deficits, revolts, Fallen Empire awakening, Voidworm plague (Grand Archive), pre-FTL tech progress (First Contact), and ascension situations† (Machine Age; Shroud in 4.1) | `situations.situations.<id> = {type, country, progress, last_month_progress, approach, stage_durations}`, plus `dead_situation` and `country.events.situations` | Ledger: started, approach changed, ended. Graph: progress of the player's active situations. | M |
| 5 | **Subjects and specialist vassals** (Overlord) | `agreements.agreements.<id> = {owner, target, date_added, date_changed, active_status, subject_specialization{specialist_type, level, experience}, term_data{agreement_preset, …}}`; `country.subjects`, `country.holding_planets` | Ledger: became subject or overlord (with preset), terms renegotiated, specialization levels, release or integration. Graph: number of subjects and holdings. Map: shade subjects. | M |
| 6 | **Megastructures**: Arc Furnace, Grand Archive, hyper relays, habitats, ring worlds… | `megastructures.<id> = {type, owner, planet, coordinate}`. The stage is encoded in the type suffix, e.g. `orbital_arc_furnace_2`. Also `dead_mega_structure`. | Ledger: construction, stage upgrades, completion, destruction. This fills the never-emitted `megastructure_construction` and `habitat_ringworld_construction` types. Map markers. | S–M |
| 7 | **Galactic Community resolutions and elections** | `galactic_community = {proposed, passed, voting, last, council, emissaries, election, community_formed, emergency_fund}`; `resolution.<id> = {type, country, supporters}` | Ledger: resolution proposed, voted and passed with supporters (fills `voted_for/against_resolution`), council elections. A "laws in force over time" view. | M |
| 8 | **Astral Rifts** (Astral Planes) | `astral_rifts.rifts.<id> = {status, explorer_fleet, leader, explorable_by, days_left, difficulty, clues, completed, log, coordinate}`, `astral_rifts.astral_rifts_explored`, `dead_astral_rift`; per-country `astral_actions_usage_states_array` | Ledger: rift explored (leader, fleet) and its outcome event; astral actions used. Map markers. The threads stockpile comes from #2. | M |
| 9 | **Cosmic Storms** (Cosmic Storms) | `storms.storms.<id>`. In 4.0.1: `{name, cluster{position, radius}, affected_country_ids, …}`. Later saves add `type`, `storm_current_pos`, `path` and the min/max radius. Also `storm_influence_fields`, `dead_cosmic_storm`, `galactic_object.storm` (4.4+) and `country.num_cosmic_storms_encountered`. | A storm layer on the galaxy map per date (position and radius per save are tiny). Ledger: storm entered or left our borders, dissipated. | M |
| 10 | **Grand Archive specimens** | `exhibits.<id> = {owner, exhibit_state, activated, specimen{specimen, origin, date_added, exhibition_date}}`, `vivarium_critters`, `country.modules.standard_grand_archive_module` | Ledger: specimen acquired (with origin), exhibited. Graph: specimens per empire. | S |
| 11 | **First contact and pre-FTL observation** (First Contact) | `first_contacts.contacts.<id> = {country, leader, location, date, clues, difficulty, days_left, completed, events}`; `country.first_contact`; `country.awareness` | Ledger: contact protocol started and finished, with the scientist and location. Today only "communications established" appears. Pre-FTL observation milestones. | M |
| 12 | **Espionage** (Nemesis; rework previewed for 4.5†) | `espionage_operations.operations.<id> = {type, target, spy_network, difficulty, days_left, log, assigned_assets}`, `spy_networks.<id> = {owner, target, leader, power}`, `espionage_assets`; `country.intel_level` | Ledger: operation started and finished with its outcome. Graph: infiltration per target, intel levels. | M |
| 13 | **Archaeology and relics** (Ancient Relics; arc sites) | `archaeological_sites.sites.<id> = {type, location, completed[{country, date}], events[{event_id, …}], clues, difficulty, days_left, excavator_fleet}`, `dead_archaeological_site`; `country.relics`, `last_activated_relic`, `last_received_relic` (seen in 3.10.4) | Ledger: dig chapters and site completion, with location and dates. Relics gained and activated. | S–M |
| 14 | **Federation progression** (Federations) | `federation.<id> = {members, associates, leader, federation_progression{federation_type, experience, levels, cohesion, laws, perks, succession_type, last_succession_date}}` | Graphs: federation level, XP and cohesion. Ledger: law changes, presidency changes, members joining and leaving. Today only "formed federation" is derived, from pairwise flags. | M |
| 15 | **Empire Focus** (4.0) | `country.focus = {priority.current, active cards, reward.rewards[{category, current, progress}]}`, top-level `focus_cards`, plus `focus_*` counters in `country.variables` | Graph: focus progress per category. Ledger: focus changed, reward tier reached. | S |
| 16 | **Leaders, Paragons-era fields** (partly covered) | `leaders.<id>.experience`, `available_trait`, `council_location`, `leader_terms`, `recruitment_date`; `dead_leader` | XP progression, pending trait picks, veteran classes and destiny traits on leader pages. Also fix the level-up bug (§2.8). | S |
| 17 | **Per-colony economy** (4.4+) | `colony.<id>.produces`, `upkeep`, `profits`, e.g. `produces={energy=116.7 food=175.1 …}` | Per-planet income graphs and "most productive worlds". This is cheaper and more accurate than deriving them from jobs. | S |
| 18 | **Game setup and crises** | `galaxy = {crises, victory_year, scaling, num_empires, technology_difficulty_scale, …}`, `additional_crisis_strength`. Crisis progress has no dedicated section: it shows up in global `flags` (e.g. `war_in_heaven_started`, `great_khan_dead`), country types and the `menace` resource. | Show the game settings on the index and ledger pages. Crisis arrival comes from #1. | S |

### 3.3 Newer content: present in 4.4+ saves, needs a mid- or late-game save to design against

The early 4.4.6 and 4.5.0 saves confirm *where* these systems live, but they are mostly empty that early in a game.

| Release | Trackable systems | Suggested coverage |
|---|---|---|
| **BioGenesis** + 4.0 (May 2025) | Biomorphosis paths; advanced authorities; **bioships** with growth stages and food upkeep; Behemoth Fury crisis path; Deep Space Citadel | Authority changes already produce government-reform events. Bioship growth stages come out of the 4.x fleet fix in §3.1. Crisis-path level and resource come from the generic metrics of §2.4. Citadels are covered by megastructures (#6). |
| **Shadows of the Shroud** + 4.1 "Lyra" (Sep 2025) | Per-empire **attunement** with each Shroud patron (−1000…+1000)†; deeds and callings; accords; covenants on cooldowns; psionic auras; psionic ascension as a situation; **proxy wars** | ✔ Keys: `country.modules.standard_shroud_module = {attunement, patron_relations, last_delve_date}`, top-level `patron_relations.<id> = {owner, patron, contact, special_project, leaders}`, `psionic_auras`. Add an attunement time series per patron (high value), ledger entries for covenants, accords and callings, and tag proxy wars in the war ledger. |
| **Infernals** + 4.2 "Corvus" (Nov 2025) | Volcanic worlds; **Galactic Hyperthermia** crisis path (Galactic Crucible); Red Giant origin | Crisis-path level series. The origin probably runs as a situation (→ #4). The new planet class is picked up automatically. |
| 4.3 "Cetus" (Mar 2026) | Balance rescale; Utopia, Synthetic Dawn and Humanoids folded into the base game (4.3.6) | Version markers on graphs (§2.4). |
| **Nomads** + 4.4 "Pegasus" (Jun 2026) | **Arkship** mobile capital; **waystations** with their own stockpiles; waylines; **contracts**; **Ambitions** (five levels; Menace, Valor); joining or leaving wars mid-war; Stellar Cannon | ✔ Keys: the arkship is a `colony` whose `carrier` is a ship. Also top-level `waystation_networks`, `contracts`, `missions`; country `is_nomadic` and `waystation_networks_manager` (4.5); `galactic_object.ship_colonies`. Add the arkship position on the map, waystation stockpiles (#2-style graphs), a contract ledger (issued, completed, failed), ambition level and resource series, war join and leave events, and nomad-aware ownership. |
| 4.5 "Cygnus" (Sep 2026) | Pop groups hold ethic and faction shares (✔); federation laws grant automatic pacts; espionage rework†; situations gain `stage`, with new types such as `situation_nomad_economy` | Update pop stats (§3.1). Federation law ledger (#14). Situation stages (#4). |
| *Announced:* **Willpower** + 4.6 (Q4 2026) | Ethics → **Ideologies**, radicalization and insurrection; the "Force of Will" ambition | Plan these on top of generic metrics so they need no schema changes. |

### 3.4 Lower value or poorly suited to save parsing

- UI-only features: map modes, the planet UI overhaul, situation-log layout, cosmetic packs.
- Per-tick combat detail. Saves only capture snapshots; the existing battle records are the right granularity.
- Scenario packs (late 2026). Non-standard starts may break assumptions such as the 2200.01.01 epoch, but there is little history to chart.
- Vivarium breeding details and the full astral-action cooldown state. Both are present in the save, but there is little story in them beyond the counts in #8 and #10.

### 3.5 About the in-game Empire Timeline

4.0 gave the game its own milestone log, which overlaps with the event ledger's purpose. The recommendation is to **use it as a data source rather than compete with it**:
- The game records exact dates and DLC-specific milestones the dashboard would otherwise need dozens of processors to detect.
- The dashboard still offers what the game does not: a cross-empire view, filtering, links between leaders, planets and wars, and graphs alongside the events.
- Importing it is also the cheapest way to give a mid-game import some history.

---

## Appendix: how the numbers were produced

- Parse timings come from `rust_parser.parse_save_file` on the test save. The pure-Rust comparison is a standalone release build of the current `parser.rs` with the PyO3 layer stripped, run against `jomini 0.27` `TextTape::from_slice` on the same uncompressed `gamestate`.
- The pool round trip uses `multiprocessing.Pool(1).apply_async(save_parser.parse_save, …)`, the same call path as `ContinuousSavePathMonitor`.
- The 16% figure comes from wrapping the gamestate in a `dict` subclass that records every `get`, `[]`, `in` and iteration as a normalized path (integer ids become `*`), running two ingests, then counting the nodes under recorded paths.
- Ingestion timings run `TimelineExtractor.process_gamestate` repeatedly on the same parsed gamestate with the date advanced by one month each time: 120 iterations player-only, 40 with "store data of all countries". SQL statements were counted with a `before_cursor_execute` listener.
- The restart test re-ran the same harness in a fresh process against the existing DB, with and without `PYTHONHASHSEED=0`.
- Payload sizes come from building every tab's figures through `graph_ledger.get_raw_plot_data_dicts` and the two layouts used in `update_content`, then JSON-encoding them.
- Ledger and galaxy endpoints were timed with Flask's test client against the 46-save DB.
- Caveat: repeating one save makes the event tables grow more slowly than a real campaign does, so ledger and query costs in long real games will be higher than shown here.
- The 4.4.6 and 4.5.0 format checks ran `TimelineExtractor` (with "store data of all countries" on) on early-game saves from the `testdata/` folder of [stellaris-galaxy-forge](https://github.com/IanHeinrich/stellaris-galaxy-forge), then inspected the resulting DB and the parsed gamestate. That repository's `docs/format-notes.md` and [stellaris-companion](https://github.com/gitmaan/stellaris-companion) (`colony_resolver.py`) document the 4.4 colony/carrier split independently.

**Sources for the release catalog (§3).** Direct fetches were blocked here, so these were read through search excerpts:
- Patch pages on the [Stellaris wiki](https://stellaris.paradoxwikis.com/): Patch 4.0, Patch 4.4, Patch 4.5, Nomads, Arkship, Ambition, The Shroud, Astral rift, Cosmic Storms (DLC), BioGenesis.
- Dev diaries on the [Paradox forum](https://forum.paradoxplaza.com/forum/developer-diary/): #368–#371 (4.0 changes), #391 and #396 (Shadows of the Shroud and 4.1), #404 (Infernals), #412 (4.3), #424 (Nomads and 4.4), #433 (4.5 preliminary release notes).
- [Paradox press release for Nomads and 4.4](https://www.paradoxinteractive.com/media/press-releases/press-release/paradox-interactive-launches-major-expansion-and-free-update-for-stellaris), and the Steam store pages for Season 10 and Willpower.
