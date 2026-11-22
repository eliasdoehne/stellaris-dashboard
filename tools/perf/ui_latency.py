"""Measure how much the dashboard's UI is blocked while saves are imported.

Usage (from the repository root; select the source tree to measure with PYTHONPATH, e.g. a
checkout of another version):

    PYTHONHASHSEED=0 PYTHONPATH=$PWD python tools/perf/ui_latency.py <workdir> --corpus DIR
        --game PREFIX --saves 37:40 [--seed-db FILE] [--one-at-a-time | --at-once K | --batches N,N,...]
        [--threads N] [--cpus 2,3] [--controller-cpu 2] [--probe light|heavy]
        [--start-method spawn|forkserver|fork] [--parse-cache DIR]
        [--import-page [--live-saves GAME:INDEX,... --live-after N]]

The script starts the dashboard (the same thing ``python -m stellarisdashboard`` does: web server
thread plus save monitor) as a separate process group from a generated configuration in
<workdir> (``config.yml``, ``saves/<game id>/``, ``output/``, ``dashboard.log``), with the web
server in production mode (waitress) on a free local port. It then copies saves of the test
corpus (see docs/test-corpus.md) into the empty save folder and measures, per import:

* the import latency: from copying the save to its gamestate being visible in the game's
  database (polled read-only every 100 ms), plus a settle time for what the monitor does after
  the import (refreshing plot data); the settle time is part of the measured window;
* a tick gap probe inside the dashboard process: a thread sleeps 10 ms in a loop and records
  every gap between ticks longer than 15 ms. That is time during which no Python thread of the
  process got to run, e.g. because another thread held the GIL;
* the latency of a cheap Dash callback (POST /timeline/_dash-update-component, the tab title
  callback), sent every 50 ms from this controller process, i.e. what a browser would see.
  Response status and content are checked; failures count as errors. With ``--probe heavy`` it is
  instead the callback that builds the figures of the graph tab (the first tab of the default
  layout, no country perspective), sent 1 s after the previous response: it needs real CPU time,
  so it also notices a process that is busy but hands over the GIL often. The tick gaps of a
  heavy run include the probe's own work (building figures in the dashboard process);
* the resident memory of the dashboard process and of its other processes (the import worker,
  the forkserver) at the end of each window.

Per window (and for an idle baseline before the first save) the report has: import latency, max
tick gap, time lost to gaps (sum of gap - 10 ms over gaps > 20 ms), number of gaps > 100 ms,
probe latency p50 / p95 / max and the number of probes > 100 ms. It is printed as a Markdown
table and all raw data is written to <workdir>/ui_latency.json.

Modes:

* ``--one-at-a-time`` (default): copy each selected save, wait until it is imported, settle, next.
* ``--at-once K``: copy the first K selected saves at once; wait until all are imported or for
  ``--timeout`` seconds without progress; saves that never show up are reported as dropped.
* ``--batches 4,4,1,2``: copy the selected saves in consecutive batches of these sizes (which must
  add up to the number of saves), each batch at once, waiting for it to be imported before the
  next. Useful to import a whole game in an empty output folder.

``--saves A:B`` is a Python slice of the game's saves sorted by name (e.g. 37:40 for saves 38 to
40); ``--seed-db FILE`` is copied to output/db/<game id>.db first, so that the selected saves are
imported on top of a realistic database (build it with tools/perf/bench.py --max-saves N).

* ``--import-page``: the saves selected by ``--saves`` are copied into the save folder BEFORE the
  dashboard starts (the monitor ignores them), then, after the idle baseline, the import of the game
  is started like the import page's button does (``POST /import/start``) and ``/import/status`` is
  polled until it says the job is finished. The windows are the idle baseline, each save of the job
  (from the previous progress change to the next one) and the whole job. ``--live-saves
  GAME:INDEX,...`` (a game prefix; the index into the game's saves sorted by name, which may also be
  a save of the imported game that is not selected) copies those saves into the save folder
  ``--live-after`` saves into the job, and the report says when each became visible in its
  database and when the job finished.

``--start-method`` forces the multiprocessing start method in the dashboard process (by default the
platform's, forkserver on Linux).

The dashboard process and its children run on --cpus (taskset) and this process on
--controller-cpu; pass an empty string to disable the pinning. The whole process group of the
dashboard is always terminated at the end, also on errors and Ctrl+C.
"""

import argparse
import http.client
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
import zipfile

import parse_cache_hook

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
TICK = 0.010  # sleep of the tick probe in the dashboard process
GAP_RECORD = 0.015  # record tick gaps longer than this
DASH_PATH = "/timeline/_dash-update-component"
DASH_BODY = json.dumps(
    {
        "output": "graph-tab-title.children",
        "outputs": {"id": "graph-tab-title", "property": "children"},
        "inputs": [{"id": "url", "property": "search", "value": ""}],
        "changedPropIds": ["url.search"],
    }
)
READY_MARKER = "Waiting for new saves"
FIRST_TAB = "Budget"  # the first tab of the default tab layout


# --------------------------------------------------------------------------- child process


def child_main(workdir: pathlib.Path, start_method: str = ""):
    """Run the dashboard like stellarisdashboard.__main__.main(), with a tick gap probe."""
    os.environ["STELLARIS_DASHBOARD_CONFIG"] = str(workdir / "config.yml")
    import multiprocessing as mp

    if start_method:
        mp.set_start_method(start_method)

    gap_file = open(workdir / "gaps.txt", "a", buffering=1)

    def probe():
        previous = time.monotonic()
        while True:
            time.sleep(TICK)
            now = time.monotonic()
            if now - previous > GAP_RECORD:
                gap_file.write(f"{previous:.6f} {now - previous:.6f}\n")
            previous = now

    threading.Thread(target=probe, name="tick-probe", daemon=True).start()
    from stellarisdashboard import __main__ as dashboard_main

    mp.freeze_support()
    dashboard_main.main()


# --------------------------------------------------------------------------- helpers


def default_corpus() -> pathlib.Path:
    version = tomllib.loads((REPO / "tools" / "corpus" / "corpus.toml").read_text())["version"]
    return REPO / "corpus" / version / "saves"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def save_date_days(save: pathlib.Path) -> int:
    """The in-game date of a save, as days since 2200.01.01 (as stored in the gamestate table)."""
    date = None
    try:
        with zipfile.ZipFile(save) as z:
            for line in z.read("meta").decode("utf-8", "replace").splitlines():
                if line.startswith("date="):
                    date = line.split("=", 1)[1].strip().strip('"')
                    break
    except (OSError, KeyError, zipfile.BadZipFile):
        pass
    if date is None:
        date = save.stem.split("_")[-1]
    y, m, d = map(int, date.split("."))
    return (y - 2200) * 360 + (m - 1) * 30 + d - 1


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


class DashProbe(threading.Thread):
    """
    Sends a Dash callback request and records (send time, latency, ok): the cheap tab title
    callback every 50 ms (``light``), or the callback that builds the graph tab every second,
    1 s after the previous response (``heavy``).
    """

    def __init__(self, port: int, kind: str = "light", game_id: str = ""):
        super().__init__(name="dash-probe", daemon=True)
        self.port = port
        self.kind = kind
        if kind == "heavy":
            self.body = heavy_body(game_id)
            self.output = "tab-content"
            self.interval = 1.0
        else:
            self.body = DASH_BODY
            self.output = "graph-tab-title"
            self.interval = 0.05
        self.samples = []  # (t_send, latency, ok)
        self.errors = []
        self.stop_event = threading.Event()
        self.first_ok = threading.Event()

    def request(self, conn):
        conn.request("POST", DASH_PATH, self.body, {"Content-Type": "application/json"})
        response = conn.getresponse()
        body = response.read()
        if response.status != 200:
            return f"status {response.status}"
        try:
            data = json.loads(body)
        except ValueError:
            return "response is not JSON"
        if self.output not in data.get("response", {}):
            return f"response lacks {self.output}"
        return None

    def run(self):
        conn = None
        next_send = time.monotonic()
        while not self.stop_event.is_set():
            delay = next_send - time.monotonic()
            if delay > 0:
                self.stop_event.wait(delay)
            t0 = time.monotonic()
            try:
                if conn is None:
                    conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
                problem = self.request(conn)
            except (OSError, http.client.HTTPException) as error:
                problem = f"{type(error).__name__}: {error}"
                if conn is not None:
                    conn.close()
                conn = None
                self.stop_event.wait(0.2)
            latency = time.monotonic() - t0
            if self.kind == "heavy":
                next_send = time.monotonic() + self.interval
            else:
                next_send = max(next_send + self.interval, t0)
            if problem is None:
                self.first_ok.set()
                self.samples.append((t0, latency, True))
            elif self.first_ok.is_set():
                self.samples.append((t0, latency, False))
                self.errors.append((t0, problem))
        if conn is not None:
            conn.close()


def heavy_body(game_id: str) -> str:
    """The request the browser sends for the first tab of the graph page of a game."""
    return json.dumps(
        {
            "output": "tab-content.children",
            "outputs": {"id": "tab-content", "property": "children"},
            "inputs": [
                {"id": "url", "property": "search", "value": f"?game_name={game_id}&tab={FIRST_TAB}"},
                {"id": "dash-plot-checklist", "property": "value", "value": []},
                {"id": "country-perspective-dropdown", "property": "value", "value": None},
            ],
            "changedPropIds": ["url.search"],
        }
    )


def process_memory(group_leader: int):
    """(rss of the leader, {pid: (command, rss)} of the other processes of its group) in MB; Linux only."""
    leader, others = None, {}
    try:
        page_mb = os.sysconf("SC_PAGE_SIZE") / 2**20
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/stat") as f:
                    stat = f.read()
                fields = stat[stat.rindex(")") + 2 :].split()
                if int(fields[2]) != group_leader:  # process group
                    continue
                rss = int(fields[21]) * page_mb
                if int(entry) == group_leader:
                    leader = rss
                else:
                    with open(f"/proc/{entry}/cmdline", "rb") as f:
                        command = f.read().replace(b"\0", b" ").decode(errors="replace")
                    others[int(entry)] = (command[-60:], rss)
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        pass
    return leader, others


def read_gaps(workdir: pathlib.Path):
    gaps = []
    try:
        for line in (workdir / "gaps.txt").read_text().splitlines():
            parts = line.split()
            if len(parts) == 2:
                gaps.append((float(parts[0]), float(parts[1])))
    except OSError:
        pass
    return gaps


def window_stats(t0, t1, gaps, samples):
    """Statistics for the interval [t0, t1] (time.monotonic values)."""
    g = [gap for start, gap in gaps if t0 <= start <= t1]
    s = [x for x in samples if t0 <= x[0] <= t1]
    lat = [x[1] for x in s if x[2]]
    return {
        "duration": t1 - t0,
        "max_gap": max(g, default=0.0),
        "gap_time_lost": sum(x - TICK for x in g if x > 0.020),
        "gaps_over_100ms": sum(1 for x in g if x > 0.100),
        "probes": len(s),
        "probe_errors": sum(1 for x in s if not x[2]),
        "probe_p50": percentile(lat, 0.5),
        "probe_p95": percentile(lat, 0.95),
        "probe_max": max(lat, default=None),
        "probes_over_100ms": sum(1 for x in lat if x > 0.100),
    }


def db_has_date(db_file: pathlib.Path, days: int) -> bool:
    if not db_file.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=1)
        try:
            return conn.execute("SELECT 1 FROM gamestate WHERE date = ? LIMIT 1", (days,)).fetchone() is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def http_request(port: int, method: str, path: str, body: str = "", timeout: float = 60):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if body else {}
        conn.request(method, path, body or None, headers)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        conn.close()


PROGRESS_RE = re.compile(r'<progress max="(\d+)" value="(\d+)"')
ERROR_RE = re.compile(r'<p class="import-error"[^>]*>(.*?)</p>', re.S)
JOB_ERRORS_RE = re.compile(r"<li><code>(.*?)</code></li>", re.S)


def import_status(port: int):
    """(finished, total, done, errors) of the import job shown by /import/status."""
    status, html = http_request(port, "GET", "/import/status", timeout=30)
    if status != 200:
        raise RuntimeError(f"/import/status answered {status}")
    progress = PROGRESS_RE.search(html)
    total, done = (int(progress.group(1)), int(progress.group(2))) if progress else (0, 0)
    finished = 'hx-get="/import/status"' not in html
    return finished, total, done, JOB_ERRORS_RE.findall(html), html


def resolve_live_saves(args, corpus: pathlib.Path):
    """[(game id, save path)] for --live-saves."""
    live = []
    for item in filter(None, args.live_saves.split(",")):
        prefix, _, index = item.rpartition(":")
        games = sorted(d for d in corpus.iterdir() if d.is_dir() and d.name.startswith(prefix))
        if len(games) != 1:
            sys.exit(f"--live-saves {prefix!r} matches {[g.name for g in games]}; need exactly one")
        live.append((games[0].name, sorted(games[0].glob("*.sav"))[int(index)]))
    return live


# --------------------------------------------------------------------------- controller


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("workdir", type=pathlib.Path, help="created (an earlier workdir of this tool is deleted)")
    p.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--corpus", type=pathlib.Path, default=None, help="folder with <game id>/*.sav")
    p.add_argument("--game", default="", help="prefix of the game id (must match one game)")
    p.add_argument("--saves", default=":", help="slice of the game's sorted saves, e.g. 37:40")
    p.add_argument("--seed-db", type=pathlib.Path, default=None)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--one-at-a-time", action="store_true", help="the default")
    mode.add_argument("--at-once", type=int, metavar="K", help="copy K saves at once")
    mode.add_argument("--batches", metavar="N,N,...", help="consecutive batch sizes, each copied at once")
    p.add_argument(
        "--parse-cache", type=pathlib.Path, default=None, metavar="DIR",
        help="use parsed saves from DIR/<game id>/<save name>.pickle (as written by bench.py --cache) "
        "instead of parsing, in the dashboard and its workers; for database identity checks only, "
        "as unpickling costs time of its own",
    )
    p.add_argument("--import-page", action="store_true", help="import the selected saves through the import page")
    p.add_argument("--live-saves", default="", metavar="GAME:INDEX,...", help="with --import-page, see above")
    p.add_argument("--live-after", type=int, default=2, help="with --live-saves: copy them after this many saves")
    p.add_argument("--probe", choices=["light", "heavy"], default="light", help="see the module docstring")
    p.add_argument(
        "--start-method", default="", choices=["", "spawn", "forkserver", "fork"],
        help="multiprocessing start method of the dashboard process (default: the platform's)",
    )
    p.add_argument("--threads", type=int, default=1, help="`threads` setting of the dashboard")
    p.add_argument("--cpus", default="2,3", help="taskset cpu list for the dashboard ('' = unpinned)")
    p.add_argument("--controller-cpu", default="2", help="cpu of this process ('' = unpinned)")
    p.add_argument("--idle", type=float, default=10.0, help="seconds of idle baseline")
    p.add_argument("--warmup", type=float, default=5.0, help="seconds between readiness and baseline")
    p.add_argument("--settle", type=float, default=3.0, help="seconds after an import is visible")
    p.add_argument("--timeout", type=float, default=120.0, help="seconds without progress before giving up")
    return p.parse_args()


def prepare_workdir(args, game_id: str):
    workdir = args.workdir
    if workdir.exists():
        if any(workdir.iterdir()) and not (workdir / "ui_latency.marker").exists():
            sys.exit(f"{workdir} exists and was not created by this tool; refusing to delete it")
        shutil.rmtree(workdir)
    (workdir / "saves" / game_id).mkdir(parents=True)
    (workdir / "output" / "db").mkdir(parents=True)
    (workdir / "ui_latency.marker").write_text("")
    port = free_port()
    (workdir / "config.yml").write_text(
        "\n".join(
            [
                f"save_file_path: {json.dumps(str(workdir / 'saves'))}",
                f"base_output_path: {json.dumps(str(workdir / 'output'))}",
                "production: true",
                "host: 127.0.0.1",
                f"port: {port}",
                f"threads: {args.threads}",
                "open_browser: false",
                "check_version: false",
                "",
            ]
        )
    )
    db_file = workdir / "output" / "db" / f"{game_id}.db"
    if args.seed_db is not None:
        # The backup API gives a consolidated copy even if the seed has a write-ahead log.
        src = sqlite3.connect(f"file:{args.seed_db}?mode=ro", uri=True)
        dst = sqlite3.connect(db_file)
        src.backup(dst)
        dst.close()
        src.close()
    return port, db_file


def start_dashboard(args, port):
    cmd = [sys.executable, str(pathlib.Path(__file__).resolve()), str(args.workdir), "--child"]
    if args.start_method:
        cmd += ["--start-method", args.start_method]
    if args.cpus:
        cmd = ["taskset", "-c", args.cpus] + cmd
    log = open(args.workdir / "dashboard.log", "wb")
    env = dict(os.environ)
    if args.parse_cache is not None:
        parse_cache_hook.install(args.workdir / "hook", args.parse_cache, env)
    return subprocess.Popen(
        cmd, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True
    )


def kill_group(proc):
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # stragglers (pool workers, forkserver)
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def copy_in(save: pathlib.Path, folder: pathlib.Path):
    """Copy as a new file; renamed into place so that the monitor never sees a partial save."""
    tmp = folder / (save.name + ".part")
    shutil.copyfile(save, tmp)
    os.rename(tmp, folder / save.name)


def wait_for_dates(pending, db_file, probe, proc, timeout):
    """Wait until every date in `pending` (dict days -> save name) is in the DB. Returns
    {days: time visible}, missing dates."""
    seen = {}
    last_progress = time.monotonic()
    pending = dict(pending)
    while pending:
        if proc.poll() is not None:
            raise RuntimeError("the dashboard process exited; see dashboard.log")
        for days in list(pending):
            if db_has_date(db_file, days):
                seen[days] = time.monotonic()
                del pending[days]
                last_progress = seen[days]
        if time.monotonic() - last_progress > timeout:
            break
        time.sleep(0.1)
    return seen, pending


def main_controller(args):
    corpus = args.corpus or default_corpus()
    games = sorted(d for d in corpus.iterdir() if d.is_dir() and d.name.startswith(args.game))
    if len(games) != 1:
        sys.exit(f"--game {args.game!r} matches {[g.name for g in games]} in {corpus}; need exactly one")
    game_dir = games[0]
    game_id = game_dir.name
    lo, _, hi = args.saves.partition(":")
    saves = sorted(game_dir.glob("*.sav"))[slice(int(lo) if lo else None, int(hi) if hi else None)]
    if not saves:
        sys.exit("no saves selected")
    if args.batches:
        sizes = [int(n) for n in args.batches.split(",")]
        if sum(sizes) != len(saves):
            sys.exit(f"--batches adds up to {sum(sizes)}, but {len(saves)} saves are selected")
        starts = [sum(sizes[:i]) for i in range(len(sizes))]
        batches = [saves[a : a + n] for a, n in zip(starts, sizes)]
    elif args.at_once:
        batches = [saves[: args.at_once]]
    else:
        batches = [[s] for s in saves]

    if args.controller_cpu:
        os.sched_setaffinity(0, {int(c) for c in args.controller_cpu.split(",")})
    port, db_file = prepare_workdir(args, game_id)
    folder = args.workdir / "saves" / game_id
    proc = probe = None
    result = {"game": game_id, "args": {k: str(v) for k, v in vars(args).items()}, "windows": []}
    try:
        if args.import_page:
            # the monitor ignores the saves that exist when it starts
            for save in saves:
                copy_in(save, folder)
        proc = start_dashboard(args, port)
        probe = DashProbe(port, args.probe, game_id)
        probe.start()
        deadline = time.monotonic() + 180
        log_file = args.workdir / "dashboard.log"
        while True:
            if proc.poll() is not None:
                raise RuntimeError("the dashboard process exited at startup; see dashboard.log")
            ready = probe.first_ok.is_set() and READY_MARKER in log_file.read_text(errors="replace")
            if ready:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("the dashboard did not become ready within 180 s; see dashboard.log")
            time.sleep(0.2)
        # The monitor ignores saves that exist when it starts; READY_MARKER says it has started.
        print(f"dashboard ready on port {port}; warm-up {args.warmup:.0f} s, idle baseline {args.idle:.0f} s", flush=True)
        time.sleep(args.warmup)
        t0 = time.monotonic()
        time.sleep(args.idle)
        windows = [("idle", t0, time.monotonic(), None, [])]
        memory = [process_memory(proc.pid)]

        if args.import_page:
            run_import_page(args, port, proc, probe, game_id, saves, db_file, windows, memory, result, corpus)
            batches = []
        for batch in batches:
            expected = {save_date_days(s): s.name for s in batch}
            t_copy = time.monotonic()
            for save in batch:
                copy_in(save, folder)
            print(f"copied {', '.join(s.name for s in batch)}", flush=True)
            seen, missing = wait_for_dates(expected, db_file, probe, proc, args.timeout)
            t_visible = max(seen.values(), default=time.monotonic())
            time.sleep(args.settle)
            label = batch[0].stem if len(batch) == 1 else f"{len(batch)} saves at once"
            windows.append((label, t_copy, time.monotonic(), (t_visible - t_copy) if seen else None, sorted(missing.values())))
            memory.append(process_memory(proc.pid))
            if missing:
                print(f"NOT IMPORTED: {sorted(missing.values())}", flush=True)
            result.setdefault("imported", {}).update({expected[d]: t - t_copy for d, t in seen.items()})

        probe.stop_event.set()
        probe.join()
        time.sleep(0.3)
        gaps = read_gaps(args.workdir)
        for (label, a, b, latency, missing), (rss, others) in zip(windows, memory):
            row = window_stats(a, b, gaps, probe.samples)
            row.update({"label": label, "import_latency": latency, "dropped": missing})
            row["rss_mb"] = rss
            row["other_processes_rss_mb"] = {f"{pid} {cmd}": mb for pid, (cmd, mb) in others.items()}
            result["windows"].append(row)
        result["gaps"] = gaps
        result["probe_samples"] = probe.samples
        result["probe_errors"] = probe.errors
        result["dropped"] = [name for w in result["windows"] for name in w["dropped"]]
    finally:
        if probe is not None:
            probe.stop_event.set()
        kill_group(proc)
    (args.workdir / "ui_latency.json").write_text(json.dumps(result))
    print(markdown_table(result))
    return 1 if result["dropped"] else 0


def run_import_page(args, port, proc, probe, game_id, saves, db_file, windows, memory, result, corpus):
    """Start the import of the game like the import page does and follow it; appends to windows/memory."""
    live = resolve_live_saves(args, corpus) if args.live_saves else []
    expected_live = {}  # (db file, days) -> (game id, save name)
    visible = {}  # the same keys -> time the gamestate was visible in the database
    t_start = time.monotonic()
    status, html = http_request(port, "POST", "/import/start", f"game_id={game_id}")
    error = ERROR_RE.search(html)
    if status >= 400 or error:
        raise RuntimeError(f"/import/start refused: {status} {error.group(1) if error else html[:200]}")
    print(f"import of {game_id} started ({len(saves)} saves)", flush=True)
    changes = [(t_start, 0)]  # (time, saves done)
    live_copied = None
    last_progress = time.monotonic()
    while True:
        if proc.poll() is not None:
            raise RuntimeError("the dashboard process exited; see dashboard.log")
        finished, total, done, errors, _ = import_status(port)
        now = time.monotonic()
        if done != changes[-1][1]:
            changes.append((now, done))
            last_progress = now
            print(f"  {done}/{total} saves", flush=True)
        if live and live_copied is None and done >= args.live_after:
            live_copied = now
            for other_game, save in live:
                folder = args.workdir / "saves" / other_game
                folder.mkdir(parents=True, exist_ok=True)
                copy_in(save, folder)
                expected_live[(args.workdir / "output" / "db" / f"{other_game}.db", save_date_days(save))] = (other_game, save.name)
                print(f"  copied live save {other_game}/{save.name}", flush=True)
        for key in expected_live:
            if key not in visible and db_has_date(*key):
                visible[key] = time.monotonic()
        if finished:
            break
        if now - last_progress > args.timeout:
            raise RuntimeError(f"the import made no progress for {args.timeout:.0f} s")
        time.sleep(0.25)
    t_finished = time.monotonic()
    result["import_page"] = dict(total=total, done=done, errors=errors, seconds=t_finished - t_start)
    # one window per save: from the previous change of the progress to this one
    for (t_a, _), (t_b, n) in zip(changes, changes[1:]):
        windows.append((f"save {n}", t_a, t_b, t_b - t_a, []))
        memory.append((None, {}))
    windows.append(("whole import", t_start, t_finished, t_finished - t_start, []))
    memory.append(process_memory(proc.pid))
    if live:
        pending = {k: v for k, v in expected_live.items() if k not in visible}
        deadline = time.monotonic() + args.timeout
        while pending and time.monotonic() < deadline:
            for key in list(pending):
                if db_has_date(*key):
                    visible[key] = time.monotonic()
                    del pending[key]
            time.sleep(0.1)
        result["live_saves"] = [
            dict(game=g, save=n, visible_after_copy=(visible[k] - live_copied) if k in visible else None,
                 visible_after_job_finished=(visible[k] - t_finished) if k in visible else None)
            for k, (g, n) in expected_live.items()
        ]
        for entry in result["live_saves"]:
            print("live save:", entry, flush=True)
    time.sleep(args.settle)


def ms_or_dash(megabytes) -> str:
    return "-" if megabytes is None else f"{megabytes:.0f}"


def markdown_table(result) -> str:
    def ms(x):
        return "-" if x is None else f"{x * 1000:.0f}"

    head = (
        "| window | import s | max gap ms | lost s | gaps >100ms | probe p50 ms | p95 ms | max ms "
        "| probes >100ms | probe errors | UI process MB | other processes MB |\n|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [head]
    for w in result["windows"]:
        latency = "-" if w["import_latency"] is None else f"{w['import_latency']:.1f}"
        if w["dropped"]:
            latency += " (DROPPED: " + ", ".join(w["dropped"]) + ")"
        rows.append(
            f"| {w['label']} | {latency} | {ms(w['max_gap'])} | {w['gap_time_lost']:.2f} | {w['gaps_over_100ms']} "
            f"| {ms(w['probe_p50'])} | {ms(w['probe_p95'])} | {ms(w['probe_max'])} | {w['probes_over_100ms']} "
            f"| {w['probe_errors']} | {ms_or_dash(w['rss_mb'])} "
            f"| {sum(w['other_processes_rss_mb'].values()):.0f} |"
        )
    return "\n".join(rows)


if __name__ == "__main__":
    arguments = parse_args()
    if arguments.child:
        child_main(arguments.workdir, arguments.start_method)
    else:
        arguments.workdir = arguments.workdir.absolute()
        if arguments.seed_db is not None:
            arguments.seed_db = arguments.seed_db.absolute()
        sys.exit(main_controller(arguments))
