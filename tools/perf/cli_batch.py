"""Time ``parse-saves`` of the CLI on saves of the test corpus, and compare the databases.

Usage (from the repository root; select the source tree with PYTHONPATH, as for ui_latency.py):

    PYTHONHASHSEED=0 PYTHONPATH=$PWD python tools/perf/cli_batch.py <workdir> --corpus DIR
        [--game PREFIX] [--threads N] [--cpus 1,2,3] [--parse-cache DIR] [--reference DIR]

Runs ``python -m stellarisdashboard.cli parse-saves --save-path DIR --threads N`` as a separate
process group, with a generated configuration in <workdir> (``config.yml``, ``output/``,
``cli.log``; an earlier workdir of this tool is deleted), and reports the wall time and the peak of
the summed resident memory of the process group. With ``--reference DIR`` each database in
<workdir>/output/db is compared with DIR/<game id>.db using compare_db.py, whose last line is
printed. With ``--parse-cache DIR`` the parser is replaced by the cache hook of parse_cache_hook.py
(database identity checks only, never timings). The process group is always terminated at the end.
"""

import argparse
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import threading
import time

import parse_cache_hook

TOOLS = pathlib.Path(__file__).resolve().parent


def group_rss_mb(group: int) -> float:
    """Summed resident memory of the processes of a process group in MB; Linux only."""
    total = 0.0
    page_mb = os.sysconf("SC_PAGE_SIZE") / 2**20
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                stat = f.read()
            fields = stat[stat.rindex(")") + 2 :].split()
            if int(fields[2]) == group:
                total += int(fields[21]) * page_mb
        except (OSError, ValueError, IndexError):
            continue
    return total


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("workdir", type=pathlib.Path)
    p.add_argument("--corpus", type=pathlib.Path, required=True, help="folder with <game id>/*.sav")
    p.add_argument("--game", default="", help="prefix of the game ids to import (default: all)")
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--cpus", default="", help="taskset cpu list ('' = unpinned)")
    p.add_argument("--parse-cache", type=pathlib.Path, default=None, metavar="DIR")
    p.add_argument("--reference", type=pathlib.Path, default=None, metavar="DIR", help="folder with <game id>.db")
    args = p.parse_args()
    workdir = args.workdir.absolute()
    if workdir.exists():
        if any(workdir.iterdir()) and not (workdir / "cli_batch.marker").exists():
            sys.exit(f"{workdir} exists and was not created by this tool; refusing to delete it")
        shutil.rmtree(workdir)
    (workdir / "output" / "db").mkdir(parents=True)
    (workdir / "cli_batch.marker").write_text("")
    (workdir / "config.yml").write_text(
        f'save_file_path: "{args.corpus.absolute()}"\nbase_output_path: "{workdir / "output"}"\n'
        f"threads: {args.threads}\ncheck_version: false\n"
    )
    env = dict(os.environ, STELLARIS_DASHBOARD_CONFIG=str(workdir / "config.yml"))
    if args.parse_cache is not None:
        parse_cache_hook.install(workdir / "hook", args.parse_cache, env)
    cmd = [sys.executable, "-m", "stellarisdashboard.cli", "parse-saves", "--save-path", str(args.corpus.absolute()),
           "--threads", str(args.threads), "--game-name", args.game]
    if args.cpus:
        cmd = ["taskset", "-c", args.cpus] + cmd
    peak = [0.0]
    stop = threading.Event()
    log = open(workdir / "cli.log", "wb")
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True)

    def sample():
        while not stop.wait(0.25):
            peak[0] = max(peak[0], group_rss_mb(proc.pid))

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        code = proc.wait()
        wall = time.monotonic() - t0
    finally:
        stop.set()
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # stragglers
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
    print(f"parse-saves --threads {args.threads}: exit code {code}, wall time {wall:.1f} s, "
          f"peak memory of the process group {peak[0]:.0f} MB")
    status = 0 if code == 0 else 1
    if args.reference is not None:
        for db in sorted((workdir / "output" / "db").glob("*.db")):
            done = subprocess.run(
                [sys.executable, str(TOOLS / "compare_db.py"), str(args.reference / db.name), str(db)],
                capture_output=True, text=True,
            )
            lines = done.stdout.strip().splitlines()
            print(f"{db.name}: {lines[-1] if lines else done.stderr[-300:]}")
            status |= done.returncode
    return status


if __name__ == "__main__":
    sys.exit(main())
