"""
Copies of save files kept in the output folder, so they can be imported again later.

- Failed saves: saves that failed to parse or import, or where a single data processor
  failed, are copied to <base_output_path>/failed_saves/<game_id>/ together with a JSON
  record of the errors. Oldest copies are deleted once the folder exceeds
  failed_saves_max_size_mb. The import page (or `stellarisdashboardcli
  reprocess-failed-saves`) imports them again and deletes the copies that now import cleanly.
- Backups: with backup_saves enabled, every save read by the dashboard is copied to
  <base_output_path>/save_backups/<game_id>/ (no size limit).

Both folders use the same layout as the Stellaris save folder, so they can be passed to
`stellarisdashboardcli parse-saves --save-path`.
"""

import dataclasses
import datetime
import json
import logging
import pathlib
import re
import shutil
import time
import traceback
import zipfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

from stellarisdashboard import config

logger = logging.getLogger(__name__)

FAILED_SAVES_DIR = "failed_saves"
BACKUP_SAVES_DIR = "save_backups"

# failure stages, in the order in which they can happen
STAGE_PARSE = "parse"
STAGE_IMPORT = "import"
STAGE_PROCESSOR = "processor"

# keeps records readable when an error repeats for many objects
MAX_ERROR_CHARS = 20_000

_DATE_RE = re.compile(rb'^date="(\d+\.\d+\.\d+)"', re.MULTILINE)


def failed_saves_path() -> pathlib.Path:
    return config.CONFIG.base_output_path / FAILED_SAVES_DIR


def backup_saves_path() -> pathlib.Path:
    return config.CONFIG.base_output_path / BACKUP_SAVES_DIR


def failure(stage: str, error: str, processor: Optional[str] = None) -> Dict[str, Any]:
    result = dict(stage=stage)
    if processor is not None:
        result["processor"] = processor
    result["error"] = error[-MAX_ERROR_CHARS:]
    return result


def format_exception(e: BaseException) -> str:
    return "".join(traceback.format_exception(type(e), e, e.__traceback__))


def read_save_date(save_file: pathlib.Path) -> Optional[str]:
    """Read the in-game date from the save's meta file, None if it cannot be read."""
    try:
        with zipfile.ZipFile(save_file) as z:
            match = _DATE_RE.search(z.read("meta"))
        return match.group(1).decode() if match else None
    except Exception:
        return None


def copy_name(save_file: pathlib.PurePath, game_date: Optional[str], mtime: float) -> str:
    """
    File name of the copy of a save. Ironman games overwrite ironman.sav,
    so their copies get the in-game date (or the modification time) appended.
    """
    if save_file.stem != "ironman":
        return save_file.name
    if game_date:
        suffix = game_date
    else:
        suffix = time.strftime("%Y%m%d-%H%M%S", time.localtime(mtime))
    return f"ironman_{suffix}{save_file.suffix}"


def record_path(copy_path: pathlib.Path) -> pathlib.Path:
    return copy_path.with_suffix(".json")


def is_kept_copy(save_file: pathlib.Path) -> bool:
    """True if this file is one of the kept copies of failed saves."""
    try:
        return save_file.resolve().is_relative_to(failed_saves_path().resolve())
    except OSError:
        return False


def _copy_destination(
    save_file: pathlib.Path, game_id: str, base_dir: pathlib.Path, game_date: Optional[str]
) -> pathlib.Path:
    if save_file.stem == "ironman" and game_date is None:
        game_date = read_save_date(save_file)
    name = copy_name(save_file, game_date, save_file.stat().st_mtime)
    return base_dir / game_id / name


def keep_failed_save(
    save_file: pathlib.Path,
    game_id: str,
    failures: List[Dict[str, Any]],
    game_date: Optional[str] = None,
) -> Optional[pathlib.Path]:
    """
    Copy a save that failed to parse or import to the failed saves folder and write
    a record of the errors next to it. Never raises, returns the path of the copy.
    """
    if not config.CONFIG.keep_failed_saves or not failures:
        return None
    try:
        if is_kept_copy(save_file):
            # a kept copy failed again: only refresh its record
            dest = save_file
            previous = _read_record(record_path(dest))
            source = previous.get("save_file", str(save_file))
        else:
            dest = _copy_destination(save_file, game_id, failed_saves_path(), game_date)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(save_file, dest)
            source = str(save_file)
        record = dict(
            save_file=source,
            game_id=game_id,
            game_date=game_date or read_save_date(dest),
            failed_at=datetime.datetime.now().isoformat(timespec="seconds"),
            dashboard_version=_dashboard_version(),
            failures=failures,
        )
        with open(record_path(dest), "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        stages = ", ".join(sorted({f["stage"] for f in failures}))
        logger.warning(f"Kept a copy of {save_file.name} ({stages} failed) at {dest}")
        enforce_size_limit(
            failed_saves_path(), config.CONFIG.failed_saves_max_size_mb * 2**20, keep=dest
        )
        return dest
    except Exception:
        logger.exception(f"Could not keep a copy of failed save {save_file}")
        return None


def remove_kept_copy(copy_path: pathlib.Path) -> None:
    """Delete a kept copy that was imported successfully."""
    try:
        record_path(copy_path).unlink(missing_ok=True)
        copy_path.unlink(missing_ok=True)
        _remove_if_empty(copy_path.parent)
        logger.info(f"Reprocessed {copy_path.name}, removed the kept copy")
    except Exception:
        logger.exception(f"Could not remove kept copy {copy_path}")


@dataclasses.dataclass(frozen=True)
class KeptCopy:
    path: pathlib.Path
    game_id: str
    game_date: Optional[str]
    failed_at: str
    reasons: List[str]  # one line per failure, e.g. "import: ValueError: ..."


def _summarize_failure(f: Dict[str, Any]) -> str:
    lines = [line.strip() for line in str(f.get("error", "")).splitlines() if line.strip()]
    prefix = f["stage"] + (f" ({f['processor']})" if f.get("processor") else "")
    return f"{prefix}: {lines[-1][:300] if lines else 'unknown error'}"


def list_kept_copies(game_id: Optional[str] = None) -> List[KeptCopy]:
    """The kept copies of failed saves (of one game, by default all), oldest in-game date first per game."""
    base_dir = failed_saves_path()
    copies = []
    # game_id is only compared, never used in a glob pattern
    for path in base_dir.glob("*/*.sav") if base_dir.is_dir() else []:
        if game_id is not None and path.parent.name != game_id:
            continue
        record = _read_record(record_path(path))
        copies.append(
            KeptCopy(
                path=path,
                game_id=path.parent.name,
                game_date=record.get("game_date") or read_save_date(path),
                failed_at=str(record.get("failed_at", "")),
                reasons=[_summarize_failure(f) for f in record.get("failures", [])],
            )
        )
    return sorted(copies, key=lambda c: (c.game_id, _date_key(c.game_date), c.path.name))


def _date_key(game_date: Optional[str]) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in game_date.split("."))
    except (AttributeError, ValueError):
        return (10**9,)


def back_up_save(
    save_file: pathlib.Path, game_id: str, game_date: Optional[str] = None
) -> Optional[pathlib.Path]:
    """Copy a save to the backup folder if backup_saves is enabled. Never raises."""
    if not config.CONFIG.backup_saves:
        return None
    try:
        if is_kept_copy(save_file) or save_file.resolve().is_relative_to(
            backup_saves_path().resolve()
        ):
            return None
        dest = _copy_destination(save_file, game_id, backup_saves_path(), game_date)
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(save_file, dest)
        logger.info(f"Backed up {save_file.name} to {dest}")
        return dest
    except Exception:
        logger.exception(f"Could not back up save {save_file}")
        return None


def select_copies_to_delete(
    copies: Iterable[pathlib.Path],
    sizes: Dict[pathlib.Path, int],
    mtimes: Dict[pathlib.Path, float],
    max_bytes: int,
    keep: Optional[pathlib.Path] = None,
) -> List[pathlib.Path]:
    """Oldest copies whose deletion brings the total size to at most max_bytes, never `keep`."""
    total = sum(sizes[c] for c in copies)
    to_delete = []
    for c in sorted(copies, key=lambda c: (mtimes[c], str(c))):
        if total <= max_bytes:
            break
        if c == keep:
            continue
        to_delete.append(c)
        total -= sizes[c]
    return to_delete


def enforce_size_limit(
    base_dir: pathlib.Path, max_bytes: int, keep: Optional[pathlib.Path] = None
) -> List[pathlib.Path]:
    """Delete the oldest kept copies (and their records) until the folder fits max_bytes."""
    copies = list(base_dir.glob("*/*.sav"))
    sizes = {c: c.stat().st_size + _size_or_zero(record_path(c)) for c in copies}
    mtimes = {c: c.stat().st_mtime for c in copies}
    to_delete = select_copies_to_delete(copies, sizes, mtimes, max_bytes, keep)
    for c in to_delete:
        logger.warning(
            f"Deleting kept copy {c} to stay below failed_saves_max_size_mb={config.CONFIG.failed_saves_max_size_mb}"
        )
        record_path(c).unlink(missing_ok=True)
        c.unlink(missing_ok=True)
        _remove_if_empty(c.parent)
    return to_delete


def _size_or_zero(path: pathlib.Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _remove_if_empty(directory: pathlib.Path) -> None:
    try:
        directory.rmdir()
    except OSError:
        pass


def _read_record(path: pathlib.Path) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _dashboard_version() -> str:
    # imported lazily: save parser worker processes should not load the dashboard app
    from stellarisdashboard.dashboard_app.utils import VERSION

    return VERSION
