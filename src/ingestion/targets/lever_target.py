"""
Lever target — tier 2.

Lever exposes a free public JSON endpoint for every board:
    https://www.lever.co/<domain>/jobs.json

No API key required. Returns {"status": "ok", "data": [ {...} ]}.
Each job has title, absoluteUrl, location, createdAt, and a body with the JD.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from src.ingestion.posting import Posting, host_of
from src.ingestion.targets.base_target import BaseTarget


class LeverTarget(BaseTarget):
    """Fetches live job postings from a company's Lever board."""

    def __init__(self, name: str, domain: str, board_url: str, role_keywords: list[str]):
        super().__init__(name, domain, role_keywords)
        self.board_url = board_url

    @property
    def api_url(self) -> str:
        d = host_of(self.board_url) or self.domain
        # www.lever.co/<domain>/jobs.json
        return f"https://www.lever.co/{d}/jobs.json"

    def fetch(self, limit: int = 50) -> list[Posting]:
        url = self.api_url
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hunting-job-system)"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception:
            return []

        jobs = data.get("data", []) if isinstance(data, dict) else []
        postings: list[Posting] = []
        for job in jobs[:limit]:
            title = (job.get("title") or "").strip()
            if not title:
                continue
            if self.role_keywords and not self._matches_keywords(title):
                continue
            canonical = job.get("absoluteUrl") or ""
            body = job.get("body", "") or ""
            postings.append(Posting(
                company=self.name,
                role_title=title,
                url=canonical,
                jd=body,
                salary=self._extract_salary(job),
                location=job.get("locationName") or job.get("location"),
                posted_at=job.get("createdAt"),
                canonical_ats_url=canonical,
                source="lever",
                source_type="api",
                confidence="high",
            ))
        return postings

    def _extract_salary(self, job: dict) -> Optional[str]:
        """Lever nests salary in a nested field under 'salary'."""
        s = job.get("salary")
        if isinstance(s, str) and "$" in s:
            return s
        return None
