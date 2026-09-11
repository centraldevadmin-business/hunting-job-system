"""
Workable target — tier 2.

Workable exposes a free public JSON endpoint for every board:
    https://apply.workable.com/<board>/jobs.json

No API key required. Returns a JSON array of job objects with title,
link, location, and description.
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Optional

from src.ingestion.posting import Posting, host_of
from src.ingestion.targets.base_target import BaseTarget


class WorkableTarget(BaseTarget):
    """Fetches live job postings from a company's Workable board."""

    def __init__(self, name: str, domain: str, board_url: str, role_keywords: list[str]):
        super().__init__(name, domain, role_keywords)
        self.board_url = board_url

    @property
    def api_url(self) -> str:
        # Board slug is the segment after apply.workable.com/
        m = re.search(r"apply\.workable\.com/([a-z0-9-]+)", self.board_url, re.I)
        board = m.group(1) if m else host_of(self.board_url)
        return f"https://apply.workable.com/{board}/jobs.json"

    def fetch(self, limit: int = 50) -> list[Posting]:
        url = self.api_url
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hunting-job-system)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return []

        jobs = data if isinstance(data, list) else []
        postings: list[Posting] = []
        for job in jobs[:limit]:
            title = (job.get("title") or "").strip()
            if not title:
                continue
            if self.role_keywords and not self._matches_keywords(title):
                continue
            canonical = job.get("link") or job.get("url") or ""
            postings.append(Posting(
                company=self.name,
                role_title=title,
                url=canonical,
                jd=job.get("description", "") or "",
                salary=self._extract_salary(job),
                location=job.get("location"),
                posted_at=job.get("created"),
                canonical_ats_url=canonical,
                source="workable",
                source_type="api",
                confidence="high",
            ))
        return postings

    def _extract_salary(self, job: dict) -> Optional[str]:
        """Workable nests salary in a 'salary' dict."""
        s = job.get("salary")
        if isinstance(s, dict):
            lo = s.get("min")
            hi = s.get("max")
            cur = s.get("currency", "USD")
            if lo and hi:
                return f"{lo}-{hi} {cur}"
            if lo:
                return f"from {lo} {cur}"
        return None
