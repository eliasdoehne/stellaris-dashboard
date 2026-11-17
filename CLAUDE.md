# Notes for Claude sessions

## Test corpus

Realistic save files from observer games are available from a private GCS bucket; see
[docs/test-corpus.md](docs/test-corpus.md).

- `uv run tools/corpus/fetch.py` downloads them into the gitignored `corpus/<version>/`
  (`saves/<game id>/`, `notes/<game id>/`, `manifest.json`); add `--build-db` to import them into
  `output/db/`. Use `--game <prefix>` for a single game.
- In cloud sessions the credential is the `STELLARIS_CORPUS_KEY` environment variable, a
  service-account key that can only read this bucket. The repository owner authorises sessions to
  use it for read-only access to the corpus through `fetch.py`.
- The script does not use `gcloud`, so gcloud's own session credentials do not apply to it.
