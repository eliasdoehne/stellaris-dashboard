"""cProfile the timeline stage and attribute self-time to categories."""
import sys, pathlib, shutil, logging, cProfile, pstats, collections, time
out = pathlib.Path(sys.argv[1]); game = sys.argv[2]
from stellarisdashboard import config
config.CONFIG.base_output_path = out
shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
from stellarisdashboard.parsing import timeline, save_parser
logging.disable(logging.CRITICAL)
pr = cProfile.Profile()
g = next(p for p in pathlib.Path("corpus/4.5.2/saves").iterdir() if p.name.startswith(game))
unprof = 0
for f in sorted(g.glob("*.sav")):
    gs = save_parser.parse_save(f)
    pr.enable(); timeline.TimelineExtractor().process_gamestate(g.name, gs); pr.disable()
st = pstats.Stats(pr)
cat = collections.defaultdict(float)
for (fn, line, name), (cc, nc, tt, ct, callers) in st.stats.items():
    if "sqlalchemy" in fn: c = "sqlalchemy"
    elif "sqlite3" in name or "sqlite3" in fn: c = "sqlite3 (C)"
    elif "stellarisdashboard" in fn: c = "app code (timeline/datamodel/...)"
    elif fn == "~": c = "builtins called from anywhere"
    else: c = "stdlib/other"
    cat[c] += tt
tot = sum(cat.values())
for k, v in sorted(cat.items(), key=lambda kv: -kv[1]): print(f"{k:40s} {v:8.1f}s {100*v/tot:5.1f}%")
# builtins attributed by caller
bc = collections.defaultdict(float)
for (fn, line, name), (cc, nc, tt, ct, callers) in st.stats.items():
    if fn != "~" or "sqlite3" in name: continue
    tc = sum(v[2] for v in callers.values()) or 1
    for (cfn, _, _), v in callers.items():
        c = "sqlalchemy" if "sqlalchemy" in cfn else "app" if "stellarisdashboard" in cfn else "other"
        bc[c] += tt * v[2] / tc
print("builtins by caller:", {k: round(v, 1) for k, v in bc.items()})
