"""
Database connection, schema migration, and low-level query helpers.

This module is the single entry point to the SQLite database. It:
  - resolves the DB path from settings (default: data/career.db)
  - creates the schema on first run (idempotent)
  - provides a context-manager connection helper
  - exposes small typed helpers used across modules

No business logic lives here — it is pure data access.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #
def _project_root() -> Path:
    """Project root = two levels up from this file (src/db/repository.py)."""
    return Path(__file__).resolve().parents[2]


def load_settings() -> dict:
    """Load config/settings.yaml. Returns {} if missing (safe defaults)."""
    path = _project_root() / "config" / "settings.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def db_path(settings: Optional[dict] = None) -> Path:
    """Resolve the SQLite file path from settings."""
    settings = settings or load_settings()
    rel = settings.get("paths", {}).get("db", "data/career.db")
    return (_project_root() / rel).resolve()


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #
@contextmanager
def get_connection(settings: Optional[dict] = None) -> Iterator[sqlite3.Connection]:
    """
    Context manager returning a SQLite connection with sane defaults.
    Creates the schema on first use.
    """
    settings = settings or load_settings()
    path = db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        _migrate(conn)
        yield conn
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist (idempotent)."""
    schema = _project_root() / "src" / "db" / "schema.sql"
    with open(schema, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    """Current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _iso_days_ago(days: int) -> str:
    """UTC ISO-8601 timestamp *days* in the past (for windowed queries)."""
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def init_db(settings: Optional[dict] = None) -> None:
    """Explicitly create the schema (useful for tests / bootstrap)."""
    with get_connection(settings) as conn:
        conn.commit()


def execute_sql(sql: str, params: tuple = (), settings: Optional[dict] = None) -> sqlite3.Cursor:
    """Run a statement and return the cursor. Commit is caller's responsibility."""
    with get_connection(settings) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


def query_one(sql: str, params: tuple = (), settings: Optional[dict] = None) -> Optional[dict]:
    """Return a single row as a dict, or None."""
    with get_connection(settings) as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def query_all(sql: str, params: tuple = (), settings: Optional[dict] = None) -> list[dict]:
    """Return all rows as a list of dicts."""
    with get_connection(settings) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def log_audit(event: str, detail: str = "", settings: Optional[dict] = None) -> None:
    """Record an entry in the security_audit table (used by hardening layer)."""
    try:
        with get_connection(settings) as conn:
            conn.execute(
                "INSERT INTO security_audit (event, detail, timestamp) VALUES (?, ?, ?)",
                (event, detail, now_iso()),
            )
            conn.commit()
    except Exception:
        # Audit logging must never crash the pipeline.
        pass


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {db_path()}")
