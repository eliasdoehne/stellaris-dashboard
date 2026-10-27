# Proposal: testing against real saves, built for AI-first development

**Goal.** Any Claude Code cloud session, and CI, can run a code change against real saves from the current game version and get a clear result: *does it still import, did anything break, and what changed in the data?* Fixes can then be merged on evidence, without a local playtest each time.

## 1. TL;DR

1. **A versioned save corpus** in a separate repository you own. There is **one branch per game version**, and only the current version is used (no backwards compatibility). It has two tiers:
   - **smoke**: about 15 saves, at most about 100 MB. These are short runs of *consecutive* saves at key game phases. CI and cloud sessions fetch this tier with a sparse, shallow clone in seconds.
   - **playthrough**: yearly saves of a whole game, about 0.5–1 GB. They are stored as release assets and run only in GitHub Actions: on demand, nightly, or before a release.
2. **You collect it once per game version** by running an observer game with the dashboard's existing `backup_saves` option switched on. A `corpus` script then selects and publishes the sample. Your time is roughly one overnight run plus about 15 minutes.
3. **Tests that judge real data without hand-written expected values**:
   - (a) No parse or processor failures.
   - (b) Declarative invariants, such as "pops by ethos sum to the pop total" and "the capital is not a star".
   - (c) A **golden snapshot** of a normalized summary of the database, so every PR shows its effect on the data as a reviewable diff.
   - (d) A render smoke test of every page and graph.
4. **Save-format drift becomes visible without downloading anything.** A *shape fingerprint* of each version's saves (all key paths and value types, about 0.5 MB of text) is committed to the main repo. When a new version comes out, a diff of the fingerprints, cross-referenced with the paths the code actually reads, becomes the to-do list.
5. **Cloud sessions need no setup.** A SessionStart hook runs `uv sync` and fetches the smoke tier. `CLAUDE.md` tells Claude how to run the corpus tests, how to update snapshots, and how to put the data diff into the PR.

## 2. Where things stand today

What I found in the repo and measured in this cloud session:

| Aspect | Today |
|---|---|
| Real-save coverage | One save: `test/saves` (submodule `chennin/stellaris-dashboard-test-saves`). It is version **4.0.1**, dated 2312.01.01, with 602 systems and 48 countries. `test_real_save` imports it and checks that 4 tables are non-empty. |
| Save size | The `.sav` file is 3.6 MB. It unpacks to a **52.6 MB** `gamestate`, which gzip compresses to 3.1 MB and xz to 1.7 MB. |
| Cost per save | Rust parse: **5.3 s**. Full import of this one late-game save: **about 20 s**. The whole suite (355 tests) takes 26 s, of which the real save is about 80%. |
| Largest gamestate sections | `pop_jobs` (82k entries), `ships`, `planets`, `fleet`, `country`, `ship_design` |
| Format shape | **8,529** distinct key paths, with ID-keyed dicts collapsed. That is about 550 KB of text. |
| Everything else | Synthetic gamestate dicts per processor (`arkship_test.py`, `federation_test.py`, …). They encode what we *believe* the format looks like, so they keep passing after the game changes it. |
| Manual validation | `docs/test-plan-4x-save-format-fixes.md`: a local playtest checklist with an SQL check script. This is the process that should be automated. |
| Cloud sessions | The `test/saves` submodule is not checked out by default (I ran `git submodule update --init` by hand). There is no SessionStart hook and no `CLAUDE.md`. `uv sync` needs about 55 s cold. |
| Useful existing machinery | `backup_saves` (copies every save the dashboard reads), `failed_saves/` plus JSON failure records, `debug_mode` (re-raises processor errors), `stellarisdashboardcli parse-saves`, and per-processor savepoints and timings. |

Two observations shape the design:

- **Ledger events come from diffs between consecutive saves.** Wars, governors, factions, crises and so on are detected by comparing one save with the one before it. A sample of isolated saves therefore misses most event code. The sample needs *runs* of consecutive saves.
- **Observer-mode saves have no player** (the `player` list is empty, so `_identify_player_country()` returns `None`). All player-perspective code (visibility, `is_known_to_player`, player graphs) would go untested. §4.3 deals with this.

## 3. Design principles (from your constraints)

| Constraint | Consequence |
|---|---|
| A playthrough is several GB | Never put a whole playthrough in the inner loop. Use **tiers**: a small tier for every change and a large tier only in CI. Sample deliberately: consecutive runs at key phases, plus yearly saves. |
| The format changes without notice | Pin the corpus to a **game version**. Make drift *visible* (shape fingerprints, drift report) instead of trying to predict it. Tests assert **invariants and snapshots**, not hard-coded format details. |
| One-off observer games are fine | Collection must be one command plus waiting. Build on `backup_saves`, which already exists, instead of asking you to copy autosaves before the game rotates them away. |
| No backwards compatibility | **One active corpus.** When a new version is published it replaces the old one: snapshots are re-baselined and old-format code paths in `save_format.py` can be deleted. The current 4.0.1 save goes away. |

## 4. Design

### 4.1 Corpus contents and sampling

Per game version there are **two games**:
- **Game A: observer.** About 250 in-game years (2200 to roughly 2450). Medium galaxy, all DLC enabled, crisis on, mid-game around 2300 and end-game around 2400. This covers wars, federations, the Galactic Community, awakenings and crises.
- **Game B: played.** A short session that you play yourself, 2–3 in-game years. This gives genuine player-perspective data. It is optional if perspective injection (§4.3) turns out to be enough.

Set **monthly autosaves** and run the dashboard with `backup_saves: true`. Every save then lands in `output/save_backups/<game>/` and the game's autosave rotation stops mattering.

The `corpus curate` script then selects:

| Tier | Selection | Approx. size* | Where it runs |
|---|---|---|---|
| **smoke** | Game A: four windows of 3 consecutive monthly saves (≈2205, ≈2300, ≈2400 or crisis active, last save). Game B: 3 consecutive saves. | 15 saves, ≈40–100 MB | Every PR in CI; every cloud session |
| **playthrough** | Game A: every January 1st save (≈250 saves), plus all of game B | ≈0.6–1 GB | GitHub Actions: on demand, nightly, before a release |
| *(not stored)* | All other monthly saves | ≈7–10 GB | Discarded after curation |

\* Extrapolated from the one 3.6 MB late-game save. Early saves are much smaller, but large galaxies are bigger. The curate script prints the real numbers.

**Later improvement:** pick the smoke windows where something happens (a war starts, a crisis spawns, an empire awakens) instead of at fixed dates. Parsing every save once (about 5 s each, in parallel) to find those months is affordable during curation, which only happens once.

### 4.2 Storage and distribution

**Recommendation:** a dedicated repo, for example `eliasdoehne/stellaris-dashboard-corpus`. Moving the corpus away from the third-party `chennin/…` submodule means you control it. The layout:

```
branch v4.x.y            (one orphan branch per game version; the default branch = current version)
  smoke/<game>/<save>.sav
  shapes.txt             (shape fingerprint, also copied into the main repo)
  CORPUS.md              (game version, DLCs, galaxy settings, how it was generated)
release corpus-4.x.y     (release assets: playthrough-gameA.zip, playthrough-gameB.zip)
```

In the main repo, `test/corpus/manifest.toml` pins exactly what the tests run against:

```toml
game_version = "4.x.y"
repo = "https://github.com/eliasdoehne/stellaris-dashboard-corpus"
commit = "<sha on branch v4.x.y>"      # smoke tier
release = "corpus-4.x.y"               # playthrough tier
```

`scripts/corpus.py fetch --tier smoke` does a shallow, partial, sparse clone of `commit`, downloading only the `smoke/` blobs, into `~/.cache/stellaris-dashboard-corpus/<sha>/`. It is idempotent and quick on a second run.

Why this layout:
- **Plain git over github.com is known to work from cloud sessions.** Cloning the existing test-save submodule through the session proxy just worked. Release-asset downloads use a different host that may not be on the session allowlist, and Git LFS bandwidth quotas would be used up by every CI run and session. Keeping the playthrough tier in releases and running it only in Actions, which has unrestricted network, avoids both problems.
- **An orphan branch per version keeps clones small.** A clone only ever fetches the current version's smoke files, however many old versions the repo has seen. GitHub's 100 MB per-file limit is far above any single save.
- **A pinned commit makes tests reproducible.** Changing the corpus is an explicit, reviewable PR to `manifest.toml`.

### 4.3 Test harness and tiers

| Tier | Data | Runs | Time budget |
|---|---|---|---|
| **0: unit** | Synthetic dicts (as today), optionally plus *real-data slices* (§4.6) | Always (`pytest`) | under 30 s |
| **1: smoke corpus** | smoke tier | `pytest -m corpus`; CI on every PR; cloud sessions after any parsing or processing change | ≈3–5 min |
| **2: playthrough** | playthrough tier | `corpus-full` workflow: `workflow_dispatch`, a `full-corpus` PR label, nightly, before release tags | ≈1 h, games run in parallel |

The harness, in `test/corpus/`:
- Imports each corpus game into a temporary output folder through the real pipeline (`cli.f_parse_saves`) with `debug_mode=True`. Any processor exception fails the test and names the processor and the save.
- **Perspective injection:** for observer games it rewrites `gamestate["player"]` to the largest regular empire before processing. This exercises player-perspective code without a played game. The hook belongs in the test harness, not in production code. It has to be verified on the first real corpus; if the player-perspective data turns out incomplete, game B provides real player data.
- Builds each game's DB once per session (a pytest session fixture) and runs all checks against those DBs.
- Keeps the old `test_real_save` assertions as part of the invariants, then deletes the 4.0.1 submodule.

### 4.4 Test oracles: judging real data without expected values

No one has hand-written expected values for real saves, so the tests combine four kinds of check:

1. **Hard failures.** Parse errors, processor exceptions, and failure records in `save_copies`.
2. **Declarative invariants.** This is a table in code, each row a SQL query, a predicate and the game phase it applies to. Most rows come straight from `check_db.py` in the 4.x test plan.
   - Presence: districts, buildings, fleet composition, strategic resource budgets, faction support, governed-sector events, leaders with home planets, and so on are non-empty once the phase reached makes them expected.
   - Consistency: pops by ethos and by faction sum to the pop total; the capital's planet class is not `pc_*_star`; every event's country, leader or planet exists; event dates fall within the gamestate range; there are no duplicate events per (type, entity, start).
   - Names: when localisation is available, rendered names contain no raw keys (`EMPIRE_DESIGN_…`) or unresolved `$…$` or `%…%` placeholders.
3. **Golden snapshot (approval test).** `corpus report <db>` writes a deterministic JSON summary per game with:
   - row counts per table;
   - event counts per event type;
   - for the player or injected country at selected dates: key graph values, rounded;
   - the planet classes seen, government and civic counts, and the number of names that fell back to prettified IDs.

   Snapshots are committed as `test/corpus/snapshots/<game>.json`. A test diffs against them, and `pytest -m corpus --update-corpus-snapshots` re-baselines.

   **This is the piece that lets you merge with confidence.** A fix PR shows exactly which numbers moved, for example "governed_sector events 0 → 41; nothing else changed". Unintended side effects show up as unexpected diff lines.
4. **Render smoke.** Starts the Flask/Dash app against the corpus DBs, requests every page (index, history, codex, galaxy map, settings) and every graph callback, and asserts no 5xx responses and no logged exceptions. Playwright and Chromium are already installed in cloud sessions, so screenshots of key pages can be added as CI artifacts or attached to PRs.

Processor timings are recorded in the report but not snapshotted. A processor that gets more than 2× slower is flagged in the job summary.

### 4.5 Making format drift visible

- **Shape fingerprint.** `scripts/save_shape.py` walks every corpus gamestate and records each key path with the value types seen, collapsing ID-keyed dicts to `*`. For example, `$.country.*.budget.current_month.balance.armies.energy  float`. I prototyped it on the 4.0.1 save: 8,529 paths, about 550 KB of sorted text. It is committed as `test/corpus/shapes.txt`, so it is diffable and searchable without the corpus.
- **Drift report.** `scripts/save_shape.py diff old new` writes markdown listing removed paths, added paths and type changes, grouped by top-level section. It runs automatically on a corpus bump PR and goes into the PR body.
- **Accessed-path tracing** (Phase 4). Runs the smoke tier once with the gamestate wrapped in a recording `dict` subclass, so that `isinstance(..., dict)` checks still pass. The result is `test/corpus/accessed_paths.txt`: every path the code reads. The drift report then puts first **"paths the dashboard reads that vanished or changed type"**: these are the likely breakages, the first to-do list for a new game version. It also lists **"new sections the dashboard ignores"**: new DLC mechanics that are candidate features.

### 4.6 Real-data slices for unit tests (optional, Phase 4)

`scripts/corpus.py slice --path 'colony.*' --limit 3` extracts small real excerpts into `test/fixtures/real/<version>/*.json`. Format-helper tests (`save_format_test.py`) and processor tests then run on both synthetic and real shapes. When the corpus is bumped the slices are regenerated, so the unit tests stop silently testing an obsolete format.

## 5. Workflows

### 5.1 A new game version is released (you, once per version)

1. Update the game.
2. Start game A in observer mode with the settings from `CORPUS.md`.
3. Run `uv run stellarisdashboardcli monitor-saves` with `backup_saves: true`, monthly autosaves, and the fastest game speed. Let it run (typically overnight). Optionally play game B for 2–3 years.
4. `uv run python scripts/corpus.py curate <output>/save_backups --version 4.x.y` selects the tiers, writes `CORPUS.md` and `shapes.txt`, and prints the sizes.
5. `uv run python scripts/corpus.py publish` pushes branch `v4.x.y` and uploads the release assets. It uses your own git and `gh` credentials.
6. Hand off to Claude, for example in a cloud session: *"Corpus 4.x.y is published, bump to it."*

Claude then:
- bumps `manifest.toml` and `shapes.txt`;
- runs the drift report;
- runs tier 1, fixes what breaks, and deletes format code that only served the previous version;
- re-baselines the snapshots;
- opens a PR whose body holds the drift report and the snapshot diff, and triggers tier 2 on it.

### 5.2 A normal fix or feature (Claude, in any cloud session)

1. The SessionStart hook has already run `uv sync` and fetched the smoke tier.
2. Claude reproduces the bug or confirms it is missing on real data with `pytest -m corpus -k <game>` or by querying the generated DB.
3. Claude implements the change and runs tier 0 plus tier 1, then updates the snapshots on purpose and checks that every changed number is explained by the change.
4. The PR body includes the snapshot diff summary, which CI also posts to the job summary. For changes with broad effects, Claude adds the `full-corpus` label or triggers `corpus-full` and reads the result through the GitHub tools.
5. You review the code **and** the data diff, then merge.

### 5.3 User-reported failures (optional)

`failed_saves/` already keeps the failing save together with a JSON error record. Users can attach these to issues, and the save can be added under `regressions/` in the corpus. Those saves then run in tier 1 for the rest of that game version.

## 6. Cloud session and CI integration

- **`.claude/settings.json` → SessionStart hook** `scripts/session_setup.sh`. It runs only in remote sessions (`$CLAUDE_CODE_REMOTE`) and does two things: `uv sync`, then `corpus.py fetch --tier smoke`. It is idempotent and logs a clear message if the fetch fails, so tier-0 work is never blocked.
- **`CLAUDE.md`** (new). Covers:
  - how to run the tiers and how to update snapshots;
  - the rule that PRs touching `parsing/`, `datamodel.py` or `dashboard_app/` must pass tier 1 and explain every snapshot change;
  - where the drift report and the shapes file live;
  - that only the current game version matters.
- **`.github/workflows/build.yml`**: the existing `pytest` step adds `-m corpus` after fetching the smoke tier, cached by commit SHA with `actions/cache`. A failing invariant or snapshot mismatch writes a readable markdown table to `$GITHUB_STEP_SUMMARY`, which Claude can read with the GitHub tools.
- **`.github/workflows/corpus-full.yml`** (new): tier 2. It runs on `workflow_dispatch`, the `full-corpus` label, nightly and `v*` tags. It runs the games in parallel jobs and uploads the DBs and reports as artifacts. Making it a prerequisite of the release job means no release ships without a clean full playthrough on the current game version.

## 7. Rollout plan

| Phase | Content | Who | Size |
|---|---|---|---|
| 1 | Corpus repo and branch layout, `manifest.toml`, `corpus.py fetch`, harness with `debug_mode`, perspective injection and invariants (port `test_real_save` and `check_db.py`), SessionStart hook, `CLAUDE.md`. Bootstrapped with the existing 4.0.1 save so it can be built and reviewed before new saves exist. | Claude | 1–2 sessions |
| 2 | Collect, curate and publish the corpus for the current live version, then bump to it, re-baseline and fix fallout. Remove the 4.0.1 submodule. | You (one-off run) + Claude | Overnight run + 1 session |
| 3 | Golden snapshots, CI step summary, `corpus-full` workflow, render smoke | Claude | 1–2 sessions |
| 4 | Shape fingerprint, drift report, accessed-path tracing, real-data slices | Claude | 1–2 sessions |
| 5 | Event-driven window selection, user-submitted regression saves, Playwright screenshots on PRs | Claude | As needed |

Phases 1–3 deliver most of the value. Phase 4 pays off at the next game update.

## 8. Decisions needed from you

1. **Corpus repo:** a new repo you own (recommended), or keep using `chennin/stellaris-dashboard-test-saves`?
2. **Public or private:**
   - Observer saves contain no personal data apart from player names in multiplayer saves, so **public** is simplest: no extra access setup for sessions or Actions.
   - Game **localisation files** are Paradox's copyrighted content. Storing them, for the name-rendering tests that are now `skip_github_actions`, would need a private repo, or those tests stay local-only. Your call.
3. **Required check:** should tier 1 be a required PR check, adding about 3–5 min per PR run?
4. **Dependencies:** the project avoids new libraries without approval. `pytest-xdist` (running games in parallel) is the only one this plan would want. Without it, games are run in parallel by a small `concurrent.futures` helper in the harness.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Observer saves miss player-only paths | Perspective injection, plus optional played game B |
| One observer game doesn't trigger every mechanic (a crisis type, DLC events) | The fingerprint shows which sections are empty or missing. Add a second observer game with different settings when a feature needs it. |
| Snapshot churn makes reviews noisy | Snapshot summary-level aggregates, not raw rows. CI prints a compact diff. A change that touches many numbers is a signal worth seeing. |
| The corpus becomes stale mid-version (hotfix patches) | The manifest records the exact game version. A drift report on a new patch's save is cheap, and a re-collect is needed only when the drift is relevant. |
| Tier 1 slows the inner loop | Running one game with `-k <game>` takes under a minute. Games run in parallel. Tier 0 stays the default quick check. |
| Session disk allowance | The smoke tier plus generated DBs stay well under a few hundred MB. The playthrough tier never runs in sessions. |
