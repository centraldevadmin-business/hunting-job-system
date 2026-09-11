"""
Local backup layer — timestamped, versioned copies of the DB.

Backups are plain SQLite files (NOT encrypted) because they already live on the
same host as the DB and are git-ignored. The point of this layer is *versioning*
and *retention*, not confidentiality:

  - ``make_backup(settings)`` copies ``career.db`` to ``data/backups/`` with a
    timestamped name (``career.db.YYYYMMDD-HHMMSS.sqlite``).
  - ``prune_backups(settings, keep=N)`` deletes old backups beyond the retention
    window (default 7).
  - ``list_backups(settings)`` returns newest-first.

The DB file is copied while the app is idle (call this from the scheduler, not
mid-write) to avoid a torn snapshot.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..db.repository import db_path, load_settings, log_audit


def _project_root(settings: Optional[dict]) -> Path:
    """Project root = two levels up from this file (src/security/backup.py)."""
    return Path(__file__).resolve().parents[2]


def _backups_dir(settings: Optional[dict]) -> Path:
    settings = settings or load_settings()
    rel = settings.get("paths", {}).get("backups", "data/backups")
    d = (_project_root(settings) / rel).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backup_prefix(settings: Optional[dict]) -> str:
    """Base name for backups, e.g. 'career.db'."""
    return db_path(settings).name


def make_backup(settings: Optional[dict] = None) -> Path:
    """
    Copy the live DB to a timestamped backup.

    Returns the path of the new backup. Logs an audit event.
    """
    settings = settings or load_settings()
    src = db_path(settings)
    if not src.exists():
        log_audit("backup_skip", f"no db at {src}")
        return Path("")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    prefix = _backup_prefix(settings)
    dst = _backups_dir(settings) / f"{prefix}.{ts}.sqlite"

    # Copy (not move) — the live DB stays in place for the running app.
    shutil.copy2(src, dst)
    log_audit("backup", f"created {dst.name} ({dst.stat().st_size} bytes)")
    return dst


def list_backups(settings: Optional[dict] = None) -> list[dict]:
    """Return backups newest-first, each as {path, bytes, mtime}."""
    settings = settings or load_settings()
    d = _backups_dir(settings)
    prefix = _backup_prefix(settings)
    out = []
    for p in sorted(d.glob(f"{prefix}.*.sqlite"), reverse=True):
        st = p.stat()
        out.append({"path": str(p), "bytes": st.st_size, "mtime": st.st_mtime})
    return out


def prune_backups(settings: Optional[dict] = None, keep: int = 7) -> int:
    """
    Delete old backups beyond ``keep``. Returns the number deleted.
    """
    settings = settings or load_settings()
    d = _backups_dir(settings)
    prefix = _backup_prefix(settings)
    backups = sorted(d.glob(f"{prefix}.*.sqlite"), reverse=True)
    deleted = 0
    for p in backups[keep:]:
        p.unlink()
        deleted += 1
    if deleted:
        log_audit("backup_prune", f"deleted {deleted} old backup(s)")
    return deleted


def restore_backup(settings: Optional[dict] = None, backup_path: Optional[str] = None) -> Path:
    """
    Restore the DB from a backup, overwriting the live DB.

    If ``backup_path`` is None, the most recent backup is used. Returns the
    restored DB path. Logs an audit event.
    """
    settings = settings or load_settings()
    dst = db_path(settings)
    if backup_path is None:
        backups = list_backups(settings)
        if not backups:
            raise FileNotFoundError("No backups available to restore.")
        backup_path = backups[0]["path"]

    shutil.copy2(backup_path, dst)
    log_audit("backup_restore", f"restored {dst.name} from {Path(backup_path).name}")
    return dst
