"""
Greenhouse target — tier 1 (highest confidence).

Greenhouse exposes a free, public JSON API for every board:
    https://boards-api.greenhouse.io/v1/boards/<board>/jobs

No API key, no auth, no rate-limit nag for light use. This is the primary
ingestion path. If the API shape changes or a company stops using
Greenhouse, the fallback chain (tiers 2-4) kicks in.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from src.ingestion.posting import Posting, host_of
from src.ingestion.targets.base_target import BaseTarget


class GreenhouseTarget(BaseTarget):
    """Fetches live job postings from a company's Greenhouse board."""

    def __init__(self, name: str, board: str, domain: str, role_keywords: list[str]):
        super().__init__(name, domain, role_keywords)
        self.board = board          # e.g. "gitlab"

    @property
    def api_url(self) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{self.board}/jobs"

    def fetch(self, limit: int = 50) -> list[Posting]:
        """
        Fetch and normalize postings from the Greenhouse API.

        Returns a list of Posting objects with confidence=high.
        """
        url = f"{self.api_url}?limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hunting-job-system)"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # The API returns {"jobs": [...], "meta": {...}}.
        jobs = data.get("jobs", data) if isinstance(data, dict) else data

        postings: list[Posting] = []
        for job in jobs:
            title = job.get("title", "").strip()
            if not title:
                continue
            # Filter by role keywords (case-insensitive substring).
            if self.role_keywords and not self._matches_keywords(title):
                continue

            canonical = job.get("absolute_url") or job.get("canonicalUrl") or job.get("pageUrl") or ""
            postings.append(Posting(
                company=self.name,
                role_title=title,
                url=canonical,
                jd=self._extract_jd(job),
                salary=self._extract_salary(job),
                location=job.get("location", {}).get("name"),
                posted_at=job.get("first_published"),
                canonical_ats_url=canonical,
                source="greenhouse",
                source_type="api",
                confidence="high",
            ))
        return postings

    # ------------------------------------------------------------------ #
    def _matches_keywords(self, title: str) -> bool:
        t = title.lower()
        return any(kw.lower() in t for kw in self.role_keywords)

    def _extract_jd(self, job: dict) -> str:
        """Pull the JD text from the Greenhouse job object."""
        sections = job.get("sections", [])
        parts: list[str] = []
        for section in sections:
            if section.get("name") in ("Job Description", "Description"):
                for block in section.get("content", []):
                    parts.append(block.get("html", ""))
        return " ".join(parts)

    def _extract_salary(self, job: dict) -> Optional[str]:
        """Extract a salary range string if present."""
        for section in job.get("sections", []):
            if section.get("name") == "Compensation":
                for block in section.get("content", []):
                    html = block.get("html", "")
                    if "$" in html:
                        return html.replace("<", " ").replace(">", " ").strip()
        return None
