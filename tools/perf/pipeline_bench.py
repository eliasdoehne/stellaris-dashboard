"""
Evaluation: wall time of the existing bulk import (cli.f_parse_saves) for one game with N parser
processes, and the cost of shipping a parsed gamestate between processes.

    uv run python tools/perf/pipeline_bench.py <corpus saves dir> <output dir> <game prefix> <threads>
    uv run python tools/perf/pipeline_bench.py --pickle <corpus saves dir> <game prefix>
"""
import logging, pathlib, pickle, shutil, sys, time


def main():
    if sys.argv[1] == "--pickle":
        import rust_parser
        saves = sorted(next(p for p in pathlib.Path(sys.argv[2]).iterdir() if p.name.startswith(sys.argv[3])).glob("*.sav"))[::3]
        tp = td = tl = size = 0
        for f in saves:
            c0 = time.process_time(); gs = rust_parser.parse_save_file(str(f))
            c1 = time.process_time(); blob = pickle.dumps(gs, protocol=5)
            c2 = time.process_time(); gs2 = pickle.loads(blob)
            c3 = time.process_time()
            tp += c1 - c0; td += c2 - c1; tl += c3 - c2; size += len(blob)
            del gs, gs2, blob
        n = len(saves)
        print(f"saves={n} parse={tp/n:.2f}s pickle.dumps={td/n:.2f}s pickle.loads={tl/n:.2f}s blob={size/n/1e6:.0f}MB per save")
        return
    saves_dir, out, game, threads = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
    from stellarisdashboard import config
    config.CONFIG.base_output_path = out
    shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    from stellarisdashboard import cli
    logging.disable(logging.CRITICAL)
    n = 0
    def count(*_):
        nonlocal n; n += 1
    t = time.perf_counter()
    cli.f_parse_saves(threads=threads, save_path=saves_dir, game_name_prefix=game, on_save_processed=count)
    el = time.perf_counter() - t
    print(f"game={game} threads={threads} saves={n} wall={el:.1f}s -> {el/n:.2f} s/save")


if __name__ == "__main__":
    main()
