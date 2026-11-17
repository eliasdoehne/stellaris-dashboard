# /// script
# requires-python = ">=3.11"
# dependencies = ["google-cloud-storage"]
# ///
"""
Select the corpus sample from the local save backups and upload it (see docs/test-corpus.md).

    uv run tools/corpus/build.py                 # dry run: print the selection and its size
    uv run tools/corpus/build.py --upload        # upload to gs://<bucket>/<version>/ and set CURRENT
    uv run tools/corpus/build.py --archive-to "G:/My Drive/StellarisCorpus"   # copy the full save sets
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tomllib

HERE = pathlib.Path(__file__).parent
DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")


def save_date(path: pathlib.Path) -> tuple[int, int, int] | None:
    m = DATE_RE.search(path.name)
    return tuple(map(int, m.groups())) if m else None


def select_saves(game: dict, saves_dir: pathlib.Path, interval: int) -> dict[pathlib.Path, str]:
    """Map each selected save to the reason it was picked."""
    saves = sorted(
        (p for p in (saves_dir / game["id"]).glob("*.sav") if save_date(p)), key=save_date
    )
    if until := game.get("until"):
        y, m = map(int, until.split("."))
        saves = [p for p in saves if save_date(p) <= (y, m, 1)]
    picked = {saves[0]: "first", saves[-1]: "last"}
    seen_years = set()
    for p in saves:
        year = save_date(p)[0]
        if year % interval == 0 and year not in seen_years:
            seen_years.add(year)
            picked.setdefault(p, "base")
    for event in game.get("events", []):
        y, m = map(int, event.split("."))
        before = [p for p in saves if save_date(p) <= (y, m, 1)]
        after = [p for p in saves if save_date(p) > (y, m, 1)]
        for p in before[-1:] + after[:1]:
            picked.setdefault(p, f"event {event}")
    return dict(sorted(picked.items(), key=lambda kv: save_date(kv[0])))


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def default_scratch() -> pathlib.Path:
    # scratch/ is gitignored and lives in the main checkout, not in worktrees
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, cwd=HERE, check=True,
    ).stdout.strip()
    return pathlib.Path(common).parent / "scratch"


def build_manifest(cfg: dict, saves_dir: pathlib.Path, scratch: pathlib.Path, hash_files: bool):
    """Return (manifest, {object name: local path})."""
    manifest = {"version": cfg["version"], "games": {}}
    sources = {}
    for game in cfg["game"]:
        gid = game["id"]
        files = []
        for path, why in select_saves(game, saves_dir, cfg["interval_years"]).items():
            name = f"saves/{gid}/{path.name}"
            sources[name] = path
            entry = {"name": name, "size": path.stat().st_size, "why": why}
            if hash_files:
                entry["sha256"] = sha256(path)
            files.append(entry)
        for note in game.get("notes", []):
            path = scratch / note
            name = f"notes/{gid}/{path.name}"
            sources[name] = path
            entry = {"name": name, "size": path.stat().st_size, "why": "notes"}
            if hash_files:
                entry["sha256"] = sha256(path)
            files.append(entry)
        manifest["games"][gid] = {"events": game.get("events", []), "files": files}
    return manifest, sources


def print_summary(manifest: dict) -> None:
    total = 0
    for gid, game in manifest["games"].items():
        saves = [f for f in game["files"] if f["why"] != "notes"]
        size = sum(f["size"] for f in game["files"])
        total += size
        n_event = sum(f["why"].startswith("event") for f in saves)
        print(f"{gid}: {len(saves)} saves ({n_event} for events), {size / 1e6:.0f} MB")
    print(f"total: {total / 1e6:.0f} MB")


def upload(manifest: dict, sources: dict, bucket_name: str) -> None:
    from google.cloud import storage

    client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or "unused")
    bucket = client.bucket(bucket_name)
    prefix = manifest["version"] + "/"
    existing = {b.name: b for b in client.list_blobs(bucket, prefix=prefix)}
    wanted = {prefix + f["name"]: f for g in manifest["games"].values() for f in g["files"]}
    for name, entry in wanted.items():
        old = existing.get(name)
        if old is not None and (old.metadata or {}).get("sha256") == entry["sha256"]:
            continue
        print("upload", name)
        blob = bucket.blob(name)
        blob.metadata = {"sha256": entry["sha256"]}
        blob.upload_from_filename(sources[entry["name"]])
    for name in existing.keys() - wanted.keys() - {prefix + "manifest.json"}:
        print("delete", name)
        bucket.blob(name).delete()
    bucket.blob(prefix + "manifest.json").upload_from_string(
        json.dumps(manifest, indent=1), content_type="application/json"
    )
    bucket.blob("CURRENT").upload_from_string(manifest["version"], content_type="text/plain")
    print(f"gs://{bucket_name}/{prefix} is up to date; CURRENT = {manifest['version']}")


def archive(cfg: dict, saves_dir: pathlib.Path, dest: pathlib.Path) -> None:
    """Copy the full save set of every game, skipping files already there with the same size."""
    for game in cfg["game"]:
        target = dest / cfg["version"] / game["id"]
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
        for src in sorted((saves_dir / game["id"]).glob("*.sav")):
            dst = target / src.name
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                shutil.copy2(src, dst)
                copied += 1
        print(f"{game['id']}: copied {copied} saves to {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--saves-dir", type=pathlib.Path,
        default=pathlib.Path.home() / "Documents/Paradox Interactive/Stellaris/save-backup",
    )
    parser.add_argument("--scratch", type=pathlib.Path, default=None)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--archive-to", type=pathlib.Path)
    args = parser.parse_args()

    cfg = tomllib.loads((HERE / "corpus.toml").read_text(encoding="utf-8"))
    scratch = args.scratch or default_scratch()
    manifest, sources = build_manifest(cfg, args.saves_dir, scratch, hash_files=args.upload)
    print_summary(manifest)
    if args.upload:
        bucket = os.environ.get("STELLARIS_CORPUS_BUCKET") or cfg["bucket"]
        if not bucket:
            parser.error("set `bucket` in corpus.toml or STELLARIS_CORPUS_BUCKET")
        upload(manifest, sources, bucket)
    if args.archive_to:
        archive(cfg, args.saves_dir, args.archive_to)


if __name__ == "__main__":
    main()
