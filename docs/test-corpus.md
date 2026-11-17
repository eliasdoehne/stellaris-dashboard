# Test corpus

A sample of saves from AI-only observer games of the current game version, kept in a private GCS
bucket so that development sessions on any machine (including Claude Code cloud sessions) have
realistic data. It is not needed for `uv run pytest` and is not available to other contributors.

## Contents

`gs://<bucket>/<version>/`, with `gs://<bucket>/CURRENT` naming the version in use:

- `manifest.json`: every file with its size, sha256 and the reason it was picked.
- `saves/<game id>/*.sav`: per game, the first and last save, one save every 10 years, and for each
  key event the last save before and the first save after it. Importing a game's saves in order
  therefore crosses each of those transitions.
- `notes/<game id>/`: the run notes and game setup, which say what happened when in each game.

The selection is defined in `tools/corpus/corpus.toml` and is about 500 MB.

## Using it

```
uv run tools/corpus/fetch.py              # download into ./corpus/<version>/ (gitignored)
uv run tools/corpus/fetch.py --build-db   # also import the saves into ./output/db/
```

Only missing or changed files are downloaded. There are no stored databases: build one from the
saves with the code under test. Without a Stellaris installation the import still works, but names
that need the game's localisation files stay as raw keys.

Credentials are read from `STELLARIS_CORPUS_KEY` (the read-only service account key as JSON text),
or else through the usual Google lookup (`GOOGLE_APPLICATION_CREDENTIALS`, or
`gcloud auth application-default login` on the owner's machine).

## Updating it

On the machine that has the save backups:

```
uv run tools/corpus/build.py              # dry run: selection and size
uv run tools/corpus/build.py --upload
uv run tools/corpus/build.py --archive-to "G:/My Drive/StellarisCorpus"   # full save sets
```

`--upload` makes the version's prefix match the selection, including deleting objects that are no
longer selected. For a new game version, change `version` and the games in `corpus.toml`, upload,
then delete the old prefix and the old archive folder.
