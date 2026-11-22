"""Measure what the timeline (graph) page sends to the browser.

Usage (from the repository root; server pinned to one core, the client may use others):

    PYTHONPATH=<source tree> python tools/perf/graph_payload.py <game.db> <out.json>
        [--repeat N] [--server-cpus 0] [--compress {default,on,off}] [--label TEXT]

<game.db> is the database of one game (e.g. from tools/perf/bench.py); a copy is placed in a temporary
output directory. The version of the package to measure is the one on PYTHONPATH (master: a checkout
of origin/master). It is started as a real waitress server in a separate process (production mode,
settings through STELLARIS_DASHBOARD_CONFIG, no save monitor), as ``dashboard_app.start_server()``
does. For every graph tab of the default tab layout (the market tab included) the tool then sends the
request the browser's Dash renderer sends to render the tab (``POST /timeline/_dash-update-component``
for ``tab-content.children``), with browser-like ``Accept-Encoding: gzip, deflate, br``, and reports
per tab: response bytes on the wire and uncompressed, the time spent in the callback inside the
server (measured by wrapping the registered callback, so it works for old versions too; includes the
JSON serialisation) and the end-to-end time until the whole body has been received. The first request
per tab (cold: the very first one also loads the game's plot data) and the median of N further
requests (warm) are given. For versions with a server side "enlarge" (modal) callback, that is measured
for the first plot of the first tab (the browser uploads the figures store with it); newer versions
build the enlarged figure in the browser.

--compress on adds Flask-Compress to the server (for versions that don't enable it themselves), off
removes the compression of versions that do; default leaves the version as it is.
"""

import argparse
import gzip
import http.client
import json
import os
import pathlib
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import zlib

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

CALLBACK_HEADER = "X-Callback-Seconds"


def serve(compress: str) -> None:
    """Child process: run the dashboard server of the package on PYTHONPATH."""
    import flask

    from stellarisdashboard import config, dashboard_app

    config.initialize()
    flask_app = dashboard_app.flask_app
    timeline_app = dashboard_app.graph_ledger.timeline_app

    # Time the registered callbacks (Dash's wrapper includes the response serialisation).
    for key, entry in timeline_app.callback_map.items():
        # (callbacks that run in the browser have no server side function)
        if ("tab-content.children" in key or "modal-graph.figure" in key) and "callback" in entry:
            original = entry["callback"]

            def timed(*args, _original=original, **kwargs):
                start = time.perf_counter()
                try:
                    return _original(*args, **kwargs)
                finally:
                    flask.g.callback_seconds = time.perf_counter() - start

            entry["callback"] = timed

    @flask_app.after_request
    def add_timing(response):
        seconds = getattr(flask.g, "callback_seconds", None)
        if seconds is not None:
            response.headers[CALLBACK_HEADER] = f"{seconds:.6f}"
        return response

    if compress == "on":
        import flask_compress

        flask_compress.Compress(flask_app)
    elif compress == "off":
        flask_app.config["COMPRESS_MIMETYPES"] = []
    dashboard_app.start_server()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def decode_body(body: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        return zlib.decompress(body)
    if encoding == "br":
        import brotli

        return brotli.decompress(body)
    return body


class Client:
    def __init__(self, port: int):
        self.port = port

    def post(self, body: dict):
        data = json.dumps(body).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, br",
        }
        start = time.perf_counter()
        conn.request("POST", "/timeline/_dash-update-component", data, headers)
        response = conn.getresponse()
        raw = response.read()
        elapsed = time.perf_counter() - start
        status = response.status
        encoding = response.getheader("Content-Encoding") or ""
        callback = response.getheader(CALLBACK_HEADER)
        conn.close()
        if status != 200:
            raise RuntimeError(f"HTTP {status}: {raw[:300]!r}")
        decoded = decode_body(raw, encoding)
        return dict(
            wire=len(raw),
            plain=len(decoded),
            end_to_end=elapsed,
            callback=float(callback) if callback else None,
            encoding=encoding or "identity",
            json=decoded,
            request_bytes=len(data),
        )


def content_request(game: str, tab: str) -> dict:
    return {
        "output": "tab-content.children",
        "outputs": {"id": "tab-content", "property": "children"},
        "inputs": [
            {"id": "url", "property": "search", "value": f"?game_name={game}&tab={tab}"},
            {"id": "dash-plot-checklist", "property": "value", "value": []},
            {"id": "country-perspective-dropdown", "property": "value", "value": None},
        ],
        "changedPropIds": ["url.search"],
    }


def find_components(node, found):
    """All components (dicts with props.id) in a Dash response tree."""
    if isinstance(node, dict):
        props = node.get("props")
        if isinstance(props, dict) and "id" in props:
            found.append(node)
        for value in node.values():
            find_components(value, found)
    elif isinstance(node, list):
        for value in node:
            find_components(value, found)
    return found


def modal_request(content: dict):
    """The request the browser sends when the header of the first plot's card is clicked, or None
    if the version builds the enlarged figure in the browser."""
    components = find_components(content, [])
    plot_ids = [
        c["props"]["id"]["index"]
        for c in components
        if isinstance(c["props"]["id"], dict) and c["props"]["id"].get("type") == "expand-plot"
    ]
    store = next((c for c in components if c["props"]["id"] == "full-figures-store"), None)
    if store is None:
        return None
    first = plot_ids[0]
    outputs = [
        {"id": "plot-modal", "property": "className"},
        {"id": "modal-graph", "property": "figure"},
        {"id": "modal-title", "property": "children"},
    ]
    inputs = [
        [
            {"id": {"index": pid, "type": "expand-plot"}, "property": "n_clicks", "value": int(pid == first)}
            for pid in plot_ids
        ],
        {"id": "modal-close", "property": "n_clicks", "value": 0},
        {"id": "modal-backdrop", "property": "n_clicks", "value": 0},
    ]
    state = [{"id": "full-figures-store", "property": "data", "value": store["props"]["data"]}]
    return {
        "output": "..plot-modal.className...modal-graph.figure...modal-title.children..",
        "outputs": outputs,
        "inputs": inputs,
        "changedPropIds": [f'{{"index":"{first}","type":"expand-plot"}}.n_clicks'],
        "state": state,
    }


def measure(args) -> dict:
    game = args.database.stem
    work = pathlib.Path(tempfile.mkdtemp(prefix="graph_payload_"))
    (work / "db").mkdir()
    shutil.copy(args.database, work / "db" / args.database.name)
    settings = work / "config.yml"
    port = free_port()
    settings.write_text(
        f"base_output_path: '{work}'\nproduction: true\nhost: 127.0.0.1\nport: {port}\n"
        "log_level: WARNING\nlog_to_file: false\n"
    )
    env = dict(os.environ, STELLARIS_DASHBOARD_CONFIG=str(settings))
    env["PYTHONPATH"] = os.environ.get("PYTHONPATH", str(REPO))
    command = [sys.executable, str(pathlib.Path(__file__).resolve()), "--serve", "--compress", args.compress]
    if args.server_cpus:
        command = ["taskset", "-c", args.server_cpus] + command
    server = subprocess.Popen(command, env=env, cwd=work)
    try:
        for _ in range(300):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("server did not start")
        client = Client(port)
        code = "import json;from stellarisdashboard import config;config.initialize();print(json.dumps(list(config.CONFIG.tab_layout)))"
        tabs = json.loads(
            subprocess.check_output([sys.executable, "-c", code], env=env, cwd=work, stderr=subprocess.DEVNULL)
            .decode()
            .splitlines()[-1]
        )
        results = {}
        modal = None
        for tab in tabs:
            runs = []
            for _ in range(1 + args.repeat):
                r = client.post(content_request(game, tab))
                r["parsed"] = json.loads(r.pop("json"))
                runs.append(r)
            cold, warm = runs[0], runs[1:]

            def med(key):
                values = [w[key] for w in warm if w[key] is not None]
                return statistics.median(values) if values else None

            results[tab] = dict(
                plain_bytes=cold["plain"],
                wire_bytes=cold["wire"],
                encoding=cold["encoding"],
                cold_callback_s=cold["callback"],
                cold_end_to_end_s=cold["end_to_end"],
                warm_callback_s=med("callback"),
                warm_end_to_end_s=med("end_to_end"),
                warm_wire_bytes=int(med("wire")),
            )
            if modal is None:
                try:
                    request = modal_request(cold["parsed"]["response"]["tab-content"]["children"])
                    if request is None:
                        results[tab]["modal_note"] = "built in the browser, no request"
                        modal = tab
                        continue
                    m = client.post(request)
                    results[tab]["modal"] = dict(
                        request_bytes=m["request_bytes"],
                        plain_bytes=m["plain"],
                        wire_bytes=m["wire"],
                        callback_s=m["callback"],
                        end_to_end_s=m["end_to_end"],
                    )
                    modal = tab
                except Exception as error:  # report it, don't hide it
                    results[tab]["modal_error"] = repr(error)
        return dict(label=args.label, game=game, compress=args.compress, repeat=args.repeat, tabs=results)
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(work, ignore_errors=True)


def print_table(result: dict) -> None:
    print(f"\n{result['label']} ({result['game']}, compress={result['compress']}, warm = median of {result['repeat']})\n")
    print("| tab | plain bytes | wire bytes | enc | callback cold s | callback warm s | end-to-end cold s | end-to-end warm s |")
    print("|---|---:|---:|---|---:|---:|---:|---:|")
    totals = [0, 0]
    for tab, r in result["tabs"].items():
        totals[0] += r["plain_bytes"]
        totals[1] += r["wire_bytes"]
        print(
            f"| {tab} | {r['plain_bytes']:,} | {r['wire_bytes']:,} | {r['encoding']} | {r['cold_callback_s']:.3f} "
            f"| {r['warm_callback_s']:.3f} | {r['cold_end_to_end_s']:.3f} | {r['warm_end_to_end_s']:.3f} |"
        )
    print(f"| total | {totals[0]:,} | {totals[1]:,} | | | | | |")
    for tab, r in result["tabs"].items():
        if "modal" in r:
            m = r["modal"]
            print(
                f"\nModal callback (first plot of {tab}): request {m['request_bytes']:,} bytes, response "
                f"{m['plain_bytes']:,} bytes plain / {m['wire_bytes']:,} on the wire, callback {m['callback_s']:.3f} s, "
                f"end-to-end {m['end_to_end_s']:.3f} s"
            )
        if "modal_note" in r:
            print(f"\nModal (first plot of {tab}): {r['modal_note']}")
        if "modal_error" in r:
            print(f"\nModal callback failed: {r['modal_error']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("database", nargs="?", type=pathlib.Path)
    ap.add_argument("out", nargs="?", type=pathlib.Path)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--server-cpus", default="", help="taskset CPU list for the server process")
    ap.add_argument("--compress", choices=["default", "on", "off"], default="default")
    ap.add_argument("--label", default="")
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.serve:
        serve(args.compress)
        return
    if not args.database or not args.out:
        ap.error("database and out are required")
    result = measure(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print_table(result)


if __name__ == "__main__":
    main()
