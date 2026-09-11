"""
JD enrichment — fill empty job descriptions from the actual job page.

The Greenhouse (and most ATS) JSON APIs return only metadata: title,
location, URL, and a publish date. The full job description lives on the
individual job page HTML. This module fetches each job page and extracts
the description body, then writes it back into `raw_jobs.jd`.

It is honest and defensive:
  - Never raises on a single failure (one bad page doesn't stop the rest).
  - Never fabricates text — if extraction fails, the JD stays empty.
  - Idempotent — safe to run repeatedly.
"""
from __future__ import annotations

import re
import urllib.request
from typing import Optional

from bs4 import BeautifulSoup

from src.db.repository import query_all, execute_sql, now_iso


# HTML containers that hold the job description body on common ATS platforms.
JD_SELECTORS = [
    "div.Job",                       # Okta / Greenhouse
    "div.show-job",                  # Greenhouse
    ".job-description",
    ".description",
    "[class*='job-description']",
    "[class*='show-job']",
    "article",
]


def _fetch(url: str, timeout: int = 20) -> Optional[str]:
    """Fetch a URL and return decoded HTML, or None on failure."""
    if not url:
        return None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _clean_html(html: str) -> str:
    """Strip tags + boilerplate, collapse whitespace."""
    soup = BeautifulSoup(html, "html.parser")
    # Drop script/style/nav/footer noise.
    for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    # Collapse runs of 3+ newlines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_jd(html: str) -> Optional[str]:
    """
    Extract the job-description body from a job page.

    Strategy: find the largest container among JD selectors, extract its
    text, and return it if it looks like a real JD (has body-length text
    and no obvious navigation boilerplate).
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # 1. Try explicit JD selectors, pick the one with the most text.
    best = None
    best_len = 0
    for sel in JD_SELECTORS:
        for el in soup.select(sel):
            t = _clean_html(str(el))
            if len(t) > best_len:
                best, best_len = t, len(t)
    if best and best_len >= 400:
        return best

    # 2. Fall back to the largest <div> that isn't pure nav.
    divs = soup.find_all("div")
    for d in divs:
        t = _clean_html(str(d))
        if 600 <= len(t) <= 20000:
            # Prefer blocks that mention common JD phrases.
            low = t.lower()
            if any(k in low for k in ("responsibilities", "qualifications", "about", "you", "job", "description")):
                return t
    return None


def enrich_job(job_id: str, url: str, timeout: int = 20) -> bool:
    """
    Fetch a single job page and write its JD back into raw_jobs.

    Returns True if a JD was extracted and stored.
    """
    html = _fetch(url, timeout=timeout)
    jd = _extract_jd(html)
    if not jd:
        return False
    execute_sql(
        "UPDATE raw_jobs SET jd = ?, fetched_at = ? WHERE id = ?",
        (jd, now_iso(), job_id),
    )
    return True


def enrich_all(timeout: int = 20, limit: Optional[int] = None) -> dict:
    """
    Enrich every raw job that has an empty JD.

    Returns a summary dict: {total, enriched, skipped, failed}.
    """
    sql = "SELECT id, url FROM raw_jobs WHERE jd IS NULL OR length(jd) < 50 ORDER BY fetched_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = query_all(sql)

    summary = {"total": len(rows), "enriched": 0, "skipped": 0, "failed": 0}
    for row in rows:
        job_id = row["id"]
        url = row.get("url") or ""
        if not url:
            summary["skipped"] += 1
            continue
        try:
            if enrich_job(job_id, url, timeout=timeout):
                summary["enriched"] += 1
            else:
                summary["failed"] += 1
        except Exception:
            summary["failed"] += 1
    return summary
