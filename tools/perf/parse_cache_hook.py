"""A sitecustomize hook that makes every Python process of a run use parsed saves from a cache.

The Rust parser's dict order differs on every parse, so databases built from fresh parses cannot
be compared with keys. With the hook, ``rust_parser.parse_save_file`` returns the pickled
gamestate ``<cache>/<game id>/<save name>.pickle`` (as written by ``bench.py --cache``) if there is
one, in the dashboard and in the CLI and also in their worker processes (every fresh interpreter
imports ``sitecustomize``). For database identity checks only: unpickling costs ~1.5 s per save,
so never use it for timings.

Used by ui_latency.py and cli_batch.py: ``install(hook_dir, cache_dir, env)`` writes the hook and
adds it to the environment (a dict) of the process to start.
"""

import os
import pathlib

SITECUSTOMIZE = """\
import os, pathlib, pickle, sys

_cache = os.environ.get("UI_LATENCY_PARSE_CACHE")
if _cache:
    try:
        import rust_parser as _rp
        _original = _rp.parse_save_file

        def parse_save_file(path, _original=_original):
            p = pathlib.Path(path)
            cached = pathlib.Path(_cache) / p.parent.name / (p.name + ".pickle")
            if cached.exists():
                with open(cached, "rb") as f:
                    return pickle.load(f)
            return _original(path)

        _rp.parse_save_file = parse_save_file
        print(f"ui_latency: parse cache hook active in pid {os.getpid()}", file=sys.stderr, flush=True)
    except Exception as error:
        print(f"ui_latency: parse cache hook failed: {error!r}", file=sys.stderr, flush=True)
"""


def install(hook_dir: pathlib.Path, cache_dir: pathlib.Path, env: dict) -> None:
    """Write the hook to hook_dir and set up env (PYTHONPATH, the cache location) to use it."""
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "sitecustomize.py").write_text(SITECUSTOMIZE)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(hook_dir), env.get("PYTHONPATH")]))
    env["UI_LATENCY_PARSE_CACHE"] = str(cache_dir.absolute())
