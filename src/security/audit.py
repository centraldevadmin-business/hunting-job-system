"""
Audit trail helpers — read and write the security_audit table.

Every security-relevant decision (backup, decrypt attempt, weight change,
prompt change) is recorded here so you can trace *why* something happened.
This is the audit trail the self-improvement loop (Module 14) also writes to.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..db.repository import load_settings, log_audit, query_all


def record(event: str, detail: str = "", settings: Optional[dict] = None) -> None:
    """Record an audit event (thin wrapper around repository.log_audit)."""
    log_audit(event, detail, settings)


def recent(settings: Optional[dict] = None, limit: int = 50) -> list[dict]:
    """Return the most recent audit events, newest first."""
    settings = settings or load_settings()
    rows = query_all(
        "SELECT event, detail, timestamp FROM security_audit "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
        settings=settings,
    )
    return rows


def since(settings: Optional[dict] = None, hours: int = 24) -> list[dict]:
    """Return audit events from the last ``hours`` hours.

    Audit timestamps are stored in ISO-8601 UTC (see repository.now_iso), so
    the cutoff is computed the same way to keep string comparison valid.
    """
    settings = settings or load_settings()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = query_all(
        "SELECT event, detail, timestamp FROM security_audit "
        "WHERE timestamp >= ? ORDER BY id DESC",
        (cutoff,),
        settings=settings,
    )
    return rows


def count(settings: Optional[dict] = None) -> int:
    """Total number of audit events."""
    settings = settings or load_settings()
    rows = query_all("SELECT COUNT(*) AS n FROM security_audit", settings=settings)
    return rows[0]["n"] if rows else 0


def summarize(settings: Optional[dict] = None) -> dict[str, int]:
    """Count events by type."""
    settings = settings or load_settings()
    rows = query_all(
        "SELECT event, COUNT(*) AS n FROM security_audit GROUP BY event",
        settings=settings,
    )
    return {r["event"]: r["n"] for r in rows}
