"""
Ashby target — tier 2.

Ashby exposes a free public JSON API for every board:
    https://<domain>/api/v1/jobs

No API key required. Returns a JSON array of job objects with title,
location, url, createdAt, and description.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from src.ingestion.posting import Posting, host_of
from src.ingestion.targets.base_target import BaseTarget


class AshbyTarget(BaseTarget):
    """Fetches live job postings from a company's Ashby board."""

    def __init__(self, name: str, domain: str, board_url: str, role_keywords: list[str]):
        super().__init__(name, domain, role_keywords)
        self.board_url = board_url

    @property
    def api_url(self) -> str:
        d = host_of(self.board_url) or self.domain
        return f"https://{d}/api/v1/jobs"

    def fetch(self, limit: int = 50) -> list[Posting]:
        url = self.api_url
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (hunting-job-system)",
                "Accept": "application/json",
            },
        )
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
            canonical = job.get("url") or job.get("absoluteUrl") or ""
            postings.append(Posting(
                company=self.name,
                role_title=title,
                url=canonical,
                jd=job.get("description", "") or "",
                salary=self._extract_salary(job),
                location=job.get("location") or job.get("locationName"),
                posted_at=job.get("createdAt") or job.get("publishedAt"),
                canonical_ats_url=canonical,
                source="ashby",
                source_type="api",
                confidence="high",
            ))
        return postings

    def _extract_salary(self, job: dict) -> Optional[str]:
        """Ashby nests salary ranges in a 'salary' list."""
        s = job.get("salary")
        if isinstance(s, list) and s:
            parts = []
            for item in s:
                if isinstance(item, dict):
                    cur = item.get("currency", "USD")
                    lo = item.get("min")
                    hi = item.get("max")
                    if lo and hi:
                        parts.append(f"{lo}-{hi} {cur}")
                    elif lo:
                        parts.append(f"from {lo} {cur}")
            if parts:
                return "; ".join(parts)
        return None
