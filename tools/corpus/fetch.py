# /// script
# requires-python = ">=3.11"
# dependencies = ["google-cloud-storage"]
# ///
"""
Download the test corpus from its GCS bucket (see docs/test-corpus.md).

    uv run tools/corpus/fetch.py                     # all games into ./corpus/<version>/
    uv run tools/corpus/fetch.py --game gunder       # only games whose id starts with "gunder"
    uv run tools/corpus/fetch.py --build-db          # also import the saves into ./output/db/

Credentials: STELLARIS_CORPUS_KEY (the service account key as JSON text), otherwise the usual
Google lookup (GOOGLE_APPLICATION_CREDENTIALS or `gcloud auth application-default login`).
"""

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import tomllib

HERE = pathlib.Path(__file__).parent
REPO = HERE.parent.parent


def make_client():
    from google.cloud import storage

    if key := os.environ.get("STELLARIS_CORPUS_KEY"):
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(json.loads(key))
        return storage.Client(project=creds.project_id, credentials=creds)
    return storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT") or "unused")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--game", default="", help="only games whose id starts with this")
    parser.add_argument(
        "--dest", type=pathlib.Path,
        default=pathlib.Path(os.environ.get("STELLARIS_CORPUS_DIR") or REPO / "corpus"),
    )
    parser.add_argument("--build-db", action="store_true")
    parser.add_argument("--threads", type=int, default=os.cpu_count())
    args = parser.parse_args()

    cfg = tomllib.loads((HERE / "corpus.toml").read_text(encoding="utf-8"))
    bucket_name = os.environ.get("STELLARIS_CORPUS_BUCKET") or cfg["bucket"]
    if not bucket_name:
        parser.error("set `bucket` in corpus.toml or STELLARIS_CORPUS_BUCKET")
    bucket = make_client().bucket(bucket_name)

    version = bucket.blob("CURRENT").download_as_text().strip()
    manifest = json.loads(bucket.blob(f"{version}/manifest.json").download_as_text())
    root = args.dest / version
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    fetched = 0
    for gid, game in manifest["games"].items():
        if not gid.startswith(args.game):
            continue
        for entry in game["files"]:
            path = root / entry["name"]
            if path.exists() and sha256(path) == entry["sha256"]:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            bucket.blob(f"{version}/{entry['name']}").download_to_filename(path)
            if sha256(path) != entry["sha256"]:
                raise SystemExit(f"checksum mismatch: {path}")
            fetched += 1
    print(f"corpus {version} in {root} ({fetched} files downloaded)")

    if args.build_db:
        cmd = ["uv", "run", "stellarisdashboardcli", "parse-saves"]
        cmd += ["--save-path", str(root / "saves"), "--threads", str(args.threads)]
        if args.game:
            cmd += ["--game-name", args.game]
        subprocess.run(cmd, cwd=REPO, check=True)


if __name__ == "__main__":
    main()
