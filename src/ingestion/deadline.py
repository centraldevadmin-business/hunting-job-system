"""
Deadline + freshness extraction — Module 2b.

Deterministic parsing of job descriptions for application deadlines and
posting dates. This is honest: it only reports what the JD actually says.
If no deadline is named, it returns None (never fabricates one).

Patterns handled:
  - "Apply by <date>" / "Application deadline: <date>"
  - "Closing on <date>" / "Closes <date>"
  - "Deadline: <date>" / "Until <date>"
  - "Posted <date>" / "Posted on <date>" / "Published <date>"
  - Relative: "Posted 2 days ago", "3 weeks ago", "1 month ago"
  - Rolling / open / no deadline → None (truly open)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


# Month name → number, both full and abbreviated.
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


@dataclass
class DeadlineInfo:
    deadline: object = None          # ISO date string or None
    posted_at: object = None         # ISO date string or None
    days_remaining: object = None    # int or None
    is_urgent: bool = False
    freshness: str = "fresh"         # fresh | aging | stale
    source: str = "none"             # none | deadline | posted | relative


def _parse_month_token(tok: str) -> int:
    t = tok.lower().rstrip(".")
    return _MONTHS.get(t, 0)


def _to_iso(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def _parse_deadline_text(jd: str) -> object:
    """Return an ISO date string for the application deadline, or None."""
    if not jd:
        return None
    text = jd

    # "Apply by / Closes / Deadline / Closing on / Until <date>"
    patterns = [
        r"(?:apply\s*by|application\s*deadline|closing\s*(?:on|date)?|"
        r"deadline(?:\s*for\s*application)?|closes?|until|submit\s*by)\s*"
        r"[:\-]?\s*"
        r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?",
        r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+(?:of)\s+"
        r"(?:the\s+)?([A-Za-z]{3,9})\.?\s*(\d{4})?",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            # Try month-name first group.
            m1, m2, m3 = m.group(1), m.group(2), m.group(3)
            mon = _parse_month_token(m1)
            day = int(m2)
            year = int(m3) if m3 else datetime.now(timezone.utc).year
            if 1 <= mon <= 12 and 1 <= day <= 31:
                return _to_iso(year, mon, day)

    # Numeric ISO-ish: "2026-09-30" or "30/09/2026" or "09/30/2026"
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso:
        return _to_iso(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    mdy = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if mdy:
        return _to_iso(int(mdy.group(3)), int(mdy.group(1)), int(mdy.group(2)))
    dmy = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if dmy:
        return _to_iso(int(dmy.group(3)), int(dmy.group(2)), int(dmy.group(1)))

    return None


def _parse_posted_text(jd: str) -> object:
    """Return an ISO date string for the posting date, or None."""
    if not jd:
        return None
    text = jd

    # Relative: "Posted 2 days ago", "3 weeks ago", "1 month ago"
    rel = re.search(
        r"(?:posted|published|listed|updated)\s+"
        r"(\d{1,2})\s+(second|minute|hour|day|week|month|year)s?\s+ago",
        text, re.IGNORECASE,
    )
    if rel:
        n = int(rel.group(1))
        unit = rel.group(2)
        now = datetime.now(timezone.utc)
        if unit == "day":
            d = now - timedelta(days=n)
        elif unit == "week":
            d = now - timedelta(weeks=n)
        elif unit == "month":
            d = now - timedelta(days=30 * n)
        elif unit == "year":
            d = now - timedelta(days=365 * n)
        else:
            d = now  # minutes/hours/seconds → treat as today
        return d.strftime("%Y-%m-%d")

    # "Posted <date>"
    m = re.search(
        r"(?:posted|published|listed)\s*[:\-]?\s*"
        r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?",
        text, re.IGNORECASE,
    )
    if m:
        mon = _parse_month_token(m.group(1))
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else datetime.now(timezone.utc).year
        if 1 <= mon <= 12 and 1 <= day <= 31:
            return _to_iso(year, mon, day)

    # ISO date near "posted"/"updated"
    near = re.search(r"(?:posted|published|updated).*?(\d{4})-(\d{2})-(\d{2})", text)
    if near:
        return _to_iso(int(near.group(1)), int(near.group(2)), int(near.group(3)))

    return None


def _freshness_days(days_ago: int) -> str:
    if days_ago <= 7:
        return "fresh"
    if days_ago <= 30:
        return "aging"
    return "stale"


def extract_deadline(jd: str, posted_override: str = "") -> DeadlineInfo:
    """
    Extract deadline + posting info from a JD. Honest: never fabricates.

    Returns a DeadlineInfo. `posted_override` lets the caller pass a known
    posting date (e.g. from the ATS API) to avoid mis-parsing the JD body.
    """
    deadline = _parse_deadline_text(jd)

    posted = posted_override or _parse_posted_text(jd)

    now = datetime.now(timezone.utc)
    days_remaining = None
    is_urgent = False
    if deadline:
        try:
            dl = datetime.strptime(deadline, "%Y-%m-%d").replace(
                tzinfo=timezone.utc)
            days_remaining = (dl - now).days
            is_urgent = 0 <= days_remaining <= 7
        except ValueError:
            days_remaining = None

    # Freshness from posted date.
    freshness = "fresh"
    if posted:
        try:
            pd = datetime.strptime(posted, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_ago = (now - pd).days
            freshness = _freshness_days(days_ago)
        except ValueError:
            freshness = "unknown"

    source = "none"
    if deadline:
        source = "deadline"
    elif posted:
        source = "posted"

    return DeadlineInfo(
        deadline=deadline,
        posted_at=posted,
        days_remaining=days_remaining,
        is_urgent=is_urgent,
        freshness=freshness,
        source=source,
    )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def sync_deadlines(job_ids: list[str], settings=None) -> int:
    """
    Populate the `job_deadlines` table for the given job ids.

    For each job, parse the JD deterministically and upsert the result.
    Returns the number of rows written. Never fabricates a deadline —
    jobs with no named deadline get a row with deadline=NULL.
    """
    from src.db.repository import execute_sql, now_iso, query_one

    written = 0
    for job_id in job_ids:
        row = query_one(
            "SELECT jd, posted_at FROM raw_jobs WHERE id = ?",
            (job_id,),
            settings=settings,
        )
        if not row:
            continue
        info = extract_deadline(row.get("jd") or "", row.get("posted_at") or "")
        execute_sql(
            """
            INSERT OR REPLACE INTO job_deadlines
                (job_id, deadline, posted_at, days_remaining, is_urgent,
                 freshness, last_checked)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                info.deadline,
                info.posted_at,
                info.days_remaining,
                1 if info.is_urgent else 0,
                info.freshness,
                now_iso(),
            ),
            settings=settings,
        )
        written += 1
    return written
