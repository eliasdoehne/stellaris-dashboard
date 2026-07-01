# Codebase Review — Code Smells, Performance & Improvement Areas

*Scope: full sweep of `stellarisdashboard/` (~12.5k lines of Python) as of `master` @ `795afa3` (2026-07-01).*
*Method: manual read-through of every module. The test suite could not be executed in the review environment (project targets Python ≥ 3.14 + the `rust_parser` workspace member), so findings are from static inspection; each "confirmed bug" was traced through the surrounding code paths.*

---

## Executive summary

The codebase is generally in good shape: the processor pipeline in `parsing/timeline.py` is well-factored, the recent ledger/galaxy-map rework is clean, and hot paths already carry deliberate optimizations (WAL pragmas, `selectinload`, shared-description cache, `lru_cache`d name rendering). The sweep still found:

- **9 confirmed correctness bugs**, mostly small (typo'd attribute names, `pass` vs `continue`, self-assignment) but several silently corrupt or drop data during parsing.
- **A family of performance issues** that grow with campaign length or game count: N+1 query patterns in several processors, per-request scans of *all* game databases, and a duplicate-date check that loads every gamestate row on every save.
- **A recurring reliance on Python's salted `hash()` for values persisted to the database**, which invalidates the planet change-detection optimization on every restart.
- Assorted smells: import-time side effects in `config.py`, a parsing→dashboard import dependency, dead code, deprecated `logger.warn` calls, and thin test coverage of the timeline processors (where most of the bugs live).

Recommended follow-up actions are collected at the end, ordered by value/effort.

---

## 1. Confirmed bugs

### 1.1 Planet modifier expiry dates are never updated (self-assignment)
`stellarisdashboard/parsing/timeline.py:1587`

```python
if expiration != current_modifiers[modifier_text]:
    db_modifier.expiry_date = expiration   # assigns the OLD value back to itself
```

`expiration` was read from `db_modifier.expiry_date` a few lines above, so the update is a no-op; the branch should assign `current_modifiers[modifier_text]`. A modifier whose expiry date changes in-game keeps its stale date forever.

### 1.2 `is_known_to_player` typo — assignments silently discarded (2 sites)
`stellarisdashboard/parsing/timeline.py:2029` and `:2665`

```python
previous_ruler_event.is_known_to_player = country_model.has_met_player()  # RulerEventProcessor
matching_event.is_known_to_player = is_known                              # FactionProcessor
```

The column is `event_is_known_to_player`. SQLAlchemy models happily accept arbitrary attribute assignment, so these set a transient Python attribute that is never persisted. Ruler-change and faction-leader events never get their visibility upgraded after first contact. Every other call site spells it correctly.

### 1.3 `RulerEventProcessor`: `return` aborts the whole loop instead of skipping one country
`stellarisdashboard/parsing/timeline.py:1961`

```python
for country_id, country_model in countries_dict.items():
    country_dict = self._gamestate_dict["country"][country_id]
    if not isinstance(country_dict, dict):
        return None      # should be `continue`
```

One malformed country entry (which the format allows — other processors guard for it) silently skips ruler, capital, tradition, ascension-perk and edict processing for **all remaining countries** in that save.

### 1.4 Exception handler references a nonexistent attribute
`stellarisdashboard/dashboard_app/visualization_data.py:459`

```python
except Exception as e:
    logger.exception(player_cd.country.rendered_country_name)
```

`Country` has `rendered_name`, not `rendered_country_name`. When the handler fires it raises `AttributeError`, replacing the original exception and crashing the plot-data update instead of logging it.

### 1.5 `CountryColors._get_rgb`: value shift computed from saturation
`stellarisdashboard/dashboard_app/visualization_data.py:1848`

```python
for shift in itertools.chain([0.0], *((v, -v) for v in _V_SHIFTS)):
    v_shifted = s + shift        # `s` (saturation) — should be `v` (value)
```

The duplicate-color-avoidance logic shifts brightness starting from the color's *saturation* rather than its *value*, so the "avoid duplicate colors" feature picks wrong (or no) shifts. Note also that the generator expression reuses the name `v`, shadowing the outer `v` and making the bug easy to miss — worth renaming regardless of the fix.

### 1.6 Windows default path: OneDrive and non-OneDrive branches are identical
`stellarisdashboard/config.py:43`

```python
one_drive_path = home / "OneDrive/Documents/Paradox Interactive/Stellaris/"
non_one_drive_path = home / "OneDrive/Documents/Paradox Interactive/Stellaris/"  # same!
```

The fallback for non-OneDrive Windows installs (`~/Documents/...`) is unreachable; users without OneDrive get a nonexistent default and the dashboard finds no saves until they configure the path manually.

### 1.7 Tab-layout validation: `pass` where `continue` was intended (2 sites)
`stellarisdashboard/config.py:376` and `:380`

```python
if not isinstance(plot_list, list):
    logger.warning(f"Ignoring invalid graph list for tab {tab}")
    pass            # falls through to `for g in plot_list` → TypeError on int,
                    # or silently iterates the characters of a string
for g in plot_list:
    if not isinstance(g, str):
        logger.warning(f"Ignoring invalid graph ID {g}")
        pass        # falls through — the invalid ID is appended anyway
    processed[tab].append(g)
```

Both warnings claim to ignore invalid input but don't. A user with a malformed `config.yml` gets a crash at startup (int case) or garbage tabs instead of the advertised fallback.

### 1.8 `Planet.colonized_date` stored as a raw date string on completion
`stellarisdashboard/parsing/timeline.py:1790`

```python
planet_model.colonized_date = colonization_end_date   # e.g. "2215.03.14" (str) or "none"
```

`colonized_date` is an `Integer` (days) column and is written as `date_to_days(...)` in `_add_planet_model`. SQLite's loose typing masks the inconsistency, but the column now holds a mix of ints and strings (including the literal `"none"`), which breaks any future comparison/sorting on it. Should be `end_date_days` (already computed a few lines up).

### 1.9 `Leader.get_name_and_class` calls a method that doesn't exist
`stellarisdashboard/datamodel.py:1545`

```python
def get_name_and_class(self):
    return f"{self.leader_class.capitalize()} {self.get_name()}"   # no such method
```

`Leader` has no `get_name`; any caller would crash. Nothing in the repo calls it — it's dead *and* broken. Delete it, or reimplement using `rendered_name`.

### 1.10 (Likely bug) Policy events are permanently hidden from the ledger
`stellarisdashboard/parsing/timeline.py:2484`

`PolicyProcessor` creates `new_policy` / `changed_policy` `HistoricalEvent`s without passing `event_is_known_to_player`, which defaults to `False` — including for the **player's own country**. Since the ledger filters on that flag (unless `show_everything` is on), policy events effectively never appear. Every comparable processor sets the flag explicitly; this looks like an omission rather than a decision.

### 1.11 (Likely bug) Dash 404 path renders escaped HTML
`stellarisdashboard/dashboard_app/graph_ledger.py:239`

`update_content` returns `render_template("404_page.html", ...)` — a raw HTML string — as a Dash `children` output. Dash escapes string children, so the user sees the page's HTML source as text instead of a rendered 404 page. Return a Dash component (e.g. a `dcc.Location` redirect or `html.Div` message) instead.

### 1.12 `Game.player_country_id` assigned but not a column
`stellarisdashboard/parsing/timeline.py:167`

```python
game.player_country_id = player_country_id   # Game has no such column
```

Silently stored as a transient attribute and lost on session close. Either add the column (useful — the player country is currently only derivable via `Country.is_player`) or remove the assignment.

---

## 2. Performance

### 2.1 Duplicate-save check loads every gamestate, every save
`stellarisdashboard/parsing/timeline.py:98`

```python
def _check_if_gamestate_exists(self, db_game):
    existing_dates = {gs.date for gs in db_game.game_states}   # loads ALL rows
```

Accessing `db_game.game_states` materializes every `GameState` row for the campaign on every processed save — O(saves²) over a long campaign. Replace with a single filtered query:

```python
return self._session.query(datamodel.GameState).filter_by(
    game=db_game, date=self.basic_info.date_in_days
).first() is not None
```

(`GameState.date` is already indexed.)

### 2.2 N+1 query patterns in the processor pipeline

Several processors issue one query per entity per save instead of pre-loading a lookup dict (the pattern `SystemProcessor`/`PlanetProcessor` already use):

| Processor | Per-save queries | Suggested change |
|---|---|---|
| `CountryProcessor` (`timeline.py:458`) | one `Country` query per country | preload `{country_id_in_game: model}` in one query |
| `SpeciesProcessor` (`timeline.py:1082`) | one `Species` query per species | preload dict once |
| `SystemProcessor._add_system` (`timeline.py:364`) | one query per hyperlane neighbor | look up in the already-built `systems_by_ingame_id` dict |
| `PopStatsProcessor` (`timeline.py:3952`) | one `Planet` query per planet with pops | depend on `PlanetProcessor` data (already a dict) |
| `FleetInfoProcessor._check_ship_command` (`timeline.py:3296`) | one `Fleet` query per led ship | preload fleets once |
| `ScientistEventProcessor` (`timeline.py:3055`) | full `country_model.technologies` relationship + lazy `db_description` per tech, per country, per save | query `Technology` joined to `SharedDescription` once per country |

Individually small, but they run for every country/planet/ship on every autosave; together they dominate ingest time as the DB grows.

### 2.3 Every page/callback scans all game databases
`datamodel.get_available_games_dict()` opens a session on **every known game's DB file** and runs 3 queries per game. It is called per request in `history_page`, `galaxy_page`, and per Dash callback (`update_game_header`, `update_country_select_options`, `update_content`). With dozens of campaign DBs, each page interaction re-opens and queries all of them. Cache it with a short TTL or invalidate on DB-file mtime change.

### 2.4 History ledger loads the entire event table into Python
`stellarisdashboard/dashboard_app/history_ledger.py:236`

With an empty filter, `get_event_and_link_dicts` iterates *all* countries and fetches *all* their events, then filters visibility/scope in Python (`include_event`). Late-game DBs have tens of thousands of events; the page renders all of them with no pagination. Consider pushing `event_is_known_to_player`, scope, and date filters into the SQL query and paginating the page.

### 2.5 Persisted `hash()` values are invalidated every restart
`stellarisdashboard/parsing/timeline.py:1615` (`_check_and_update_hash`), `:411` (bypass `network_id`)

Python's `str` hashing is randomized per process (`PYTHONHASHSEED`). The planet districts/buildings/deposits/modifiers hashes stored in the DB therefore never match in a new process, so the first save of every session re-diffs and re-writes *every planet's* child rows — exactly the churn the hash columns were added to avoid. Use a stable digest (e.g. `zlib.crc32`/`hashlib` over the sorted items) instead. The same applies to `hash("lgate")`/`hash(frozenset(...))` for `Bypass.network_id` (less severe since bypasses are deleted and recreated each save — but that delete-all also wipes bypasses of *other* games sharing the session? No — DBs are per-game — still, `self._session.query(datamodel.Bypass).delete()` deletes bypasses of *all systems in the game* each save, which is more churn than needed).

### 2.6 `DiplomaticRelationsProcessor` is O(N²) per save
`timeline.py:742` loads all relations and loops over all real-country pairs on every save. Row creation is one-time, but the pair loop plus per-pair dict lookups run every save; with large galaxies (100+ countries) this is 10k+ iterations doing attribute access on ORM objects. Consider only iterating pairs present in the save's `relations_manager` plus pairs with existing rows.

### 2.7 Smaller items
- `BatchSavePathMonitor` creates a **new `ProcessPoolExecutor` per chunk** (`save_parser.py:213`); create one executor for the whole batch.
- `PlanetDeposit.is_resource_deposit` (`datamodel.py:1650`) does up to 31 `endswith` calls per deposit per page render; a compiled regex (`_\d+$` with a bound check) or a precomputed suffix set is cheaper.
- `utils.preformat_history_url` uses an **unbounded** `lru_cache`; bound it (`maxsize=...`) to cap memory over long sessions.
- `AbstractPerCountryDataContainer.extract_data_from_gamestate` (`visualization_data.py:302`) renders `cd.country.rendered_name` before the country-type filter; move it after the `continue` check.
- `Config.game_data_dirs` / `Config.localization_files` are properties that re-read `dlc_load.json` and re-glob on every access; fine today (few call sites) but easy to accidentally put in a loop — consider caching with explicit invalidation.
- `ContinuousSavePathMonitor` stores a `submit_time` per pending result that is never read (`save_parser.py:179`).

---

## 3. Code smells & maintainability

### 3.1 Parsing layer imports from the dashboard layer
`timeline.py:15` imports `clear_cached_country_colors` from `dashboard_app.visualization_data`, giving the save-parsing pipeline a dependency on the web/visualization stack (heavy imports: plotly/scipy/networkx get pulled into the CLI batch-parse path too). Move the country-color cache (and its clear function) into a small shared module, or emit an event/callback instead.

### 3.2 Import-time side effects in `config.py`
`config.py:568` runs `initialize()` at import: reads `./config.yml` (CWD-dependent), **creates directories**, and **writes `config.yml`** — on any import of the package (including tests and tooling). Prefer explicit initialization from the entry points (`__main__.py`, `cli.py`) and lazy `CONFIG` access.

### 3.3 Mutating global config from request handlers
- `graph_ledger.update_content` sets `config.CONFIG.normalize_stacked_plots`, a field that isn't declared on the `Config` dataclass (it only exists once that callback has run — anything reading it earlier gets `AttributeError`; `_get_raw_data_for_stacked_and_budget_plots` does exactly that if called first). Declare the field with a default, or better, thread it through as a parameter.
- `settings.apply_settings` accepts **GET** as well as POST (`settings.py:151`), so any website can change dashboard settings via a cross-site request to `localhost:28053` (CSRF; includes filesystem paths). Restrict to POST, and validate `int()`/`float()` conversions (currently a bad value returns an unhandled 500).
- `settings[key] = key in settings` (`settings.py:158`) is always `True` — write `settings[key] = True` and let the later "missing bool → False" loop do its job; the current form reads like a bug.

### 3.4 Duplicated / dead / vestigial code
- `RulerEventProcessor.DEPENDENCIES` lists `PlanetProcessor.ID` twice (`timeline.py:1936`).
- `TruceProcessor.__init__` doesn't call `super().__init__()` (`timeline.py:3656`) — works only because `initialize()` re-sets the base attributes; fragile.
- `save_parser._apply_filename_filter` checks `if filter_string:` twice back-to-back (`save_parser.py:80-86`).
- Commented-out debug `print` in `EnvoyEventProcessor` (`timeline.py:3206`).
- `Leader.get_name_and_class` (see §1.9), `Game.player_country_id` assignment (see §1.12).
- `BatchSavePathMonitor.split_into_chunks`: `while iterable:` on an iterator is always truthy — the inner `if not chunk: break` does all the work; use `iter(lambda: list(islice(...)), [])` or a plain loop.

### 3.5 Deprecated / inconsistent logging
`logger.warn(...)` (deprecated alias) at `config.py:510-511` and `timeline.py:196`; everywhere else uses `logger.warning`. Also `logger.exception(country_name)` (`visualization_data.py:316`) logs the country name as the message — include context text.

### 3.6 Schema typo baked into the DB: `communations`
`datamodel.py:1005` names the column (and the diplo-dict key used across `timeline.py`) `communations` instead of `communications`. Cosmetic, but it propagates through three modules and will confuse every future reader; renaming needs an alembic migration (the project auto-migrates, and `Config`-driven batch mode is already set up for it).

### 3.7 Misc
- `days_to_date` docstring parameters are copy-pasted from `date_to_days` (`datamodel.py:371`).
- `get_country_color(game_id: int, ...)` — wrong type hint, it's a `str` (`graph_ledger.py:512`).
- `if config.CONFIG.production == True:` → `if config.CONFIG.production:` (`graph_ledger.py:524`).
- `get_color_vals` calls `random.seed(key_str)` on the **global** RNG (`visualization_data.py:138`), perturbing any other consumer of `random`; use a local `random.Random(key_str)`.
- `MarketPriceDataContainer._iter_galactic_market_price` (`visualization_data.py:536`) aligns DB rows with `config.CONFIG.market_resources` by `zip` position; if the save's resource list and the configured list ever differ in length/order, prices silently misalign. Match on `res.resource_index` explicitly.
- `SectorColonyEventProcessor._history_add_or_update_governor_events` (`timeline.py:1877`) queries `HistoricalEvent` filtered only by `event_type` + `db_description` — not by country or leader — and then patches the newest match. For `governed_planet` events `db_description` is `None`, so the query can match a different planet's event across countries. The leader/date guard usually saves it, but the filter should include `country`/`leader` (or `planet`).
- `PopStatsProcessor` dereferences `self._gamestate_dict.get("pop_jobs").values()` and `self._gamestate_dict["pop_groups"]` without fallbacks (`timeline.py:3774,3797`). On an older-format save this raises, and because `process_gamestate` wraps everything in one try/except, the **entire save is rolled back** rather than just pop stats being skipped. Use `.get(..., {})` like the neighboring code.
- The broad `except Exception` swallow in `process_gamestate` (`timeline.py:87`) hides all processor errors unless `debug_mode` is set. Reasonable for robustness, but consider per-processor isolation (commit what succeeded, log what failed) so one bad processor doesn't discard the whole snapshot.

---

## 4. Architecture & design observations (no action required, worth discussing)

- **Per-plot re-iteration of gamestates:** every one of the ~90 `DataContainer`s iterates `gs.country_data` per gamestate. The containers are tiny, so this is fine today; if plot count keeps growing, a single pass that fans out to containers would cut ORM attribute overhead.
- **`get_gamestates_since` yields ORM objects across a session boundary** (`datamodel.py:429`): the generator holds its session open until fully consumed; a consumer that breaks early keeps the session (and SQLite read snapshot) alive. Currently all consumers drain it — just a sharp edge to be aware of.
- **Timelapse export blocks a Flask request** (`galaxy_map.py:216`) for potentially minutes and offers no progress/cancel; the docstring acknowledges this. A background thread + polling endpoint (htmx is already in use) would improve UX. Also `int(form.get("step"))` with a negative/zero value crashes `_day_list` (`range()` empty → `export_days[-1]` IndexError) — validate inputs.
- **`EventFilter` min_date** is parsed with `float(request.args.get("min_date", -inf))` — a non-numeric query param yields an unhandled 500 (`history_ledger.py:64,88`).

---

## 5. Testing gaps

The suite (`test/`) covers the rust parser bindings, name rendering, `_extract_id`, and version comparison — all pure functions. There is **no coverage of**:

- the timeline processors (where bugs §1.1–1.3, 1.8, 1.10 live) — a fixture gamestate dict + in-memory SQLite would catch the typo class of bug cheaply;
- the datamodel helpers (`date_to_days`/`days_to_date` round-trip, `get_owner_country_at`, `Government.get_reform_description_dict`);
- config parsing (`_preprocess_tab_layout` — §1.7 would have been caught by a 3-line test);
- visualization data containers.

A thin "process one synthetic gamestate twice, assert DB contents and event visibility" integration test would guard the entire ingest path against regressions.

---

## 6. Recommended follow-up actions

Ordered roughly by (impact ÷ effort). The **Status** column reflects the fix PR
accompanying this report (branch `claude/codebase-review-report-sk1nxo`).

| # | Action | Refs | Effort | Status |
|---|--------|------|--------|--------|
| 1 | Fix the five one-line parsing/config bugs: modifier expiry self-assignment, two `is_known_to_player` typos, `return`→`continue` in `RulerEventProcessor`, `pass`→`continue` (×2) in `_preprocess_tab_layout` | §1.1–1.3, §1.7 | XS | ✅ done |
| 2 | Fix `rendered_country_name`, `colonized_date` string write, OneDrive default path; delete `Leader.get_name_and_class`; set `event_is_known_to_player` on policy events; drop the dead `Game.player_country_id` write; fix the Dash 404 path | §1.4, 1.6, 1.8–1.12 | XS | ✅ done |
| 3 | Replace `_check_if_gamestate_exists` with an indexed date query | §2.1 | XS | ✅ done |
| 4 | Replace persisted `hash()` values with a stable digest (planets + bypass network ids); one-time re-diff on upgrade is acceptable | §2.5 | S | ⬜ open — semantic change, own PR |
| 5 | Pre-load lookup dicts to remove the N+1 patterns in `CountryProcessor`, `SpeciesProcessor`, `SystemProcessor._add_system`, `PopStatsProcessor`, `FleetInfoProcessor`, `ScientistEventProcessor` | §2.2 | M | ✅ done |
| 6 | Cache `get_available_games_dict()` (TTL or mtime-based) and stop calling it per Dash callback | §2.3 | S | ✅ done (5s TTL) |
| 7 | Restrict `/applysettings/` to POST, validate numeric form fields, clean up the `key in settings` idiom; validate timelapse form ints | §3.3, §4 | S | ✅ done (timelapse form still open) |
| 8 | Declare `normalize_stacked_plots` on `Config` (or pass it through the callback chain) | §3.3 | XS | ✅ done (declared) |
| 9 | Add a synthetic-gamestate integration test for the timeline pipeline + unit tests for `_preprocess_tab_layout` and date round-trips | §5 | M | ⬜ open |
| 10 | Move `clear_cached_country_colors` out of `dashboard_app` to break the parsing→dashboard dependency | §3.1 | S | ⬜ open |
| 11 | Make `config.initialize()` explicit at entry points instead of import time | §3.2 | M | ⬜ open — see proposal in §7 |
| 12 | Push ledger filtering into SQL and paginate the history page | §2.4 | M | ⬜ open |
| 13 | Fix `CountryColors._get_rgb` saturation/value mix-up (and rename the shadowed loop variable) | §1.5 | XS | ✅ done |
| 14 | Housekeeping batch: `logger.warn`→`warning`, duplicate dependency entry, `super().__init__()` in `TruceProcessor`, dead `submit_time`, commented-out print, docstring/type-hint fixes, `== True` | §3.4–3.7 | XS | ✅ done |
| 15 | Plan an alembic rename for `communations` → `communications` | §3.6 | S | ⬜ open |

Also included in the fix PR: `PopStatsProcessor` now tolerates missing
`pop_jobs`/`pop_groups` sections instead of rolling back the entire save (§3.7),
and `BatchSavePathMonitor` reuses a single process pool across chunks (§2.7).

---

## 7. Proposal: improved configuration handling

This expands on follow-up item 11. It is a design proposal, not part of the fix
PR — the migration is incremental and each step is independently shippable.

### 7.1 Pain points in the current design

1. **Import-time side effects.** `config.py` runs `initialize()` at module
   import (`config.py:568`): it reads `./config.yml` relative to the *current
   working directory*, creates output directories, and **rewrites the config
   file** — on any import of any `stellarisdashboard` module. Tests, tooling,
   and the CLI all touch the filesystem just by importing. The rewrite also
   normalizes the file, silently discarding user comments and formatting.
2. **Every setting is declared in up to four places** that must stay in sync:
   the `Config` dataclass field, `DEFAULT_SETTINGS`, one of the
   `BOOL_KEYS`/`INT_KEYS`/`FLOAT_KEYS`/… sets, and the UI metadata dict in
   `dashboard_app/settings.py` (label, description, min/max). Drift between
   these is exactly how bugs like the undeclared `normalize_stacked_plots`
   field happen.
3. **Mixed lifetimes on one mutable global.** Persisted user settings
   (`save_file_path`), runtime-only state (`debug_mode`,
   `normalize_stacked_plots`), and transient CLI overrides (`--threads`) all
   live on the same mutable `config.CONFIG` object, which web request handlers
   mutate while the parser thread reads it.
4. **Scattered validation.** `_preprocess_bool` raises, path keys silently
   reset to defaults, tab-layout problems log-and-fall-back, and
   `settings.py` does its own `int()`/`float()` casts. There is no single
   place where "what happens on invalid input" is decided, and no feedback to
   the user when a value is rejected.
5. **Untyped defaults.** Most dataclass fields default to `None` and only
   become usable after `apply_dict` runs, so type checkers can't verify
   anything and `Config()` alone is an unusable object.

### 7.2 Design goals

- **One declaration per setting** — type, default, validation, persistence,
  and UI metadata in a single record; everything else derived.
- **No import-time side effects** — explicit load at entry points, injectable
  paths for tests, writes only on explicit save.
- **Separate persisted settings from runtime state.**
- **Uniform validation** with user-visible feedback.
- **Backward compatible** — same `config.yml` keys, same `config.CONFIG`
  call sites during the transition.

### 7.3 Proposed structure

**(a) A declarative setting registry** as the single source of truth:

```python
@dataclasses.dataclass(frozen=True)
class SettingSpec:
    default: Any                        # or a default_factory for resolved paths
    parse: Callable[[Any], Any]         # YAML/form value -> typed value; raises ValueError
    category: str                       # settings-page section, e.g. "Performance"
    label: str                          # settings-page display name
    description: str = ""
    min_value: float | None = None      # numeric bounds for the UI + validation
    max_value: float | None = None
    persisted: bool = True              # False => runtime-only, never written to YAML
    requires_restart: bool = False      # drives the "*" marker in the UI

SETTINGS: dict[str, SettingSpec] = {
    "threads": SettingSpec(
        default=1, parse=parse_positive_int, category="Performance",
        label="Number of threads", min_value=1, requires_restart=True,
        description="Number of threads for reading save files.",
    ),
    ...
}
```

Derived from this one table:
- the typed `Config` dataclass (fields can be generated or checked against the
  registry in a unit test),
- `DEFAULT_SETTINGS` and the `BOOL_KEYS`/`INT_KEYS`/… behavior (the sets
  disappear; `parse` replaces them),
- `_build_settings_dict()` in `settings.py` becomes a ~10-line loop grouping
  specs by `category`,
- YAML serialization (only `persisted=True` keys),
- validation for **both** file loading and the settings form — one code path.

Adding a setting becomes a one-place change plus a UI string.

**(b) An explicit lifecycle** instead of import-time magic:

```python
# config.py
def load(config_file: pathlib.Path | None = None) -> Config:
    """Read + validate the config file. Pure: never writes, never mkdirs."""

def save(cfg: Config, config_file: pathlib.Path | None = None) -> None:
    """Write persisted settings. Called from apply_settings and first-run setup."""

def get() -> Config:
    """Return the active config; raises if load() hasn't run (or lazily
    loads read-only, during the transition)."""
```

- `__main__.py` and `cli.py` call `config.load()` once at startup.
- The startup rewrite of `config.yml` is dropped; the file is written on first
  run (if absent) and on explicit save from the settings page only.
- Directory creation moves to the code that needs the directory
  (`Config.db_path` already does this lazily — the same pattern covers the
  output dir).
- During migration, `config.CONFIG` remains as a module-level alias so the
  ~100 existing call sites don't need to change in the same PR.

**(c) Config file resolution order**, replacing the bare CWD dependency:

1. `STELLARIS_DASHBOARD_CONFIG` environment variable (tests, power users),
2. `./config.yml` if present (backward compatibility with every existing
   install),
3. a stable per-user location, e.g. `platformdirs.user_config_dir("stellaris-dashboard")`,
4. built-in defaults.

New installs land in (3), so the dashboard behaves the same regardless of the
directory it is launched from — a recurring source of "my settings are gone"
confusion when the app is started from a different shell/launcher.

**(d) Runtime state moves off the persisted object.** `debug_mode`, the CLI
`--threads` override, and UI toggles like `normalize_stacked_plots` go to a
small `RuntimeState` object (or, for pure UI state, stay inside the Dash
callback chain — `update_content` already receives the checklist value and
could pass it down instead of writing to global config).

**(e) Immutable snapshots + atomic swap.** `load()`/`apply_dict()` produce a
*new* frozen `Config`; applying settings swaps one module-level reference.
Readers (the parser thread, `PlotDataManager`) grab a snapshot at the start of
a unit of work, which removes the current class of mid-parse races and lets
`PlotDataManager` detect "settings changed" by comparing object identity or a
version counter instead of copying three individual fields.

**(f) Uniform validation with feedback.** `parse` raising `ValueError` funnels
every invalid value — from YAML or the web form — through one handler: log a
warning, fall back to the default (or previous value for form input), and
collect the rejects so the settings page can show *"2 settings were reset:
threads ('abc' is not a number), port (must be 1–65535)"* instead of today's
mix of crash / silent reset / log-only.

### 7.4 Incremental migration plan

| Step | Change | Risk |
|------|--------|------|
| 1 | Introduce `SettingSpec` registry; generate `DEFAULT_SETTINGS`, the `*_KEYS` sets, and `_build_settings_dict` from it. Pure refactor, no behavior change; add a test asserting registry ↔ dataclass consistency. | Low |
| 2 | Split `initialize()` into `load()`/`save()`; call `load()` from the entry points; keep `config.CONFIG` as an alias; stop rewriting `config.yml` at startup. | Low–Med (startup ordering) |
| 3 | Move runtime-only flags (`normalize_stacked_plots`, `debug_mode`, CLI overrides) off the persisted object. | Low |
| 4 | Add the config-file resolution order (env var → CWD → platformdirs). | Low |
| 5 | Freeze `Config`, swap atomically on apply; migrate consumers to snapshot semantics. | Med |

Steps 1–3 remove the concrete bugs and drift this review found; steps 4–5 are
quality-of-life and concurrency hardening that can wait.
