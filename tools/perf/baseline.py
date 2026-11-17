"""Baseline: per-save parse time, per-processor time, flush/commit time. Serial, 1 game or all."""
import sys, time, json, pathlib, collections, os, shutil, logging, zipfile, pickle
out = pathlib.Path(sys.argv[1]); games = sys.argv[2:]
os.environ["STELLARISDASHBOARD_OUTPUT"] = str(out)
from stellarisdashboard import config
config.CONFIG.base_output_path = out
shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
from stellarisdashboard.parsing import timeline, save_parser
from stellarisdashboard import datamodel
import sqlalchemy
print("db path", config.CONFIG.db_path)
logging.disable(logging.CRITICAL)
root = pathlib.Path("corpus/4.5.2/saves")
proc_t = collections.defaultdict(float)
# instrument processors
orig = timeline.TimelineExtractor._data_processors
def wrapped(self):
    for p in orig(self):
        f = p.extract_data_from_gamestate
        def timed(deps, f=f, p=p):
            t = time.perf_counter()
            try: return f(deps)
            finally: proc_t[p.ID] += time.perf_counter() - t
        p.extract_data_from_gamestate = timed
        yield p
timeline.TimelineExtractor._data_processors = wrapped
from sqlalchemy.orm import Session
tot = collections.defaultdict(float)
of = Session.flush
def flush(self, *a, **k):
    t = time.perf_counter()
    try: return of(self, *a, **k)
    finally: tot["flush(explicit)"] += time.perf_counter() - t
Session.flush = flush
oc = Session.commit
def commit(self, *a, **k):
    t = time.perf_counter()
    try: return oc(self, *a, **k)
    finally: tot["commit"] += time.perf_counter() - t
Session.commit = commit
rows = []
for g in sorted(root.iterdir()):
    if games and not any(g.name.startswith(x) for x in games): continue
    for f in sorted(g.glob("*.sav")):
        t0 = time.perf_counter()
        gs = save_parser.parse_save(f)
        t1 = time.perf_counter()
        tle = timeline.TimelineExtractor()
        tle.process_gamestate(g.name, gs)
        t2 = time.perf_counter()
        assert not tle.failures, tle.failures
        with zipfile.ZipFile(f) as z: raw = z.getinfo("gamestate").file_size
        rows.append(dict(game=g.name, save=f.name, raw_mb=raw/1e6, parse=t1-t0, timeline=t2-t1))
        tot["parse"] += t1-t0; tot["timeline"] += t2-t1
        del gs
n = len(rows)
print(f"saves={n} parse={tot['parse']:.1f}s timeline={tot['timeline']:.1f}s total={tot['parse']+tot['timeline']:.1f}s -> {n/(tot['parse']+tot['timeline']):.2f} saves/s")
print(f"raw gamestate MB total={sum(r['raw_mb'] for r in rows):.0f}  explicit flush={tot['flush(explicit)']:.1f}s commit={tot['commit']:.1f}s")
for k, v in sorted(proc_t.items(), key=lambda kv: -kv[1])[:20]:
    print(f"  {k:35s} {v:7.2f}s {100*v/tot['timeline']:5.1f}%")
json.dump(dict(rows=rows, proc=proc_t, tot=tot), open(out/"bench.json","w"), indent=1)
print("db sizes", {p.name: round(p.stat().st_size/1e6,1) for p in config.CONFIG.db_path.glob("*.db")})
