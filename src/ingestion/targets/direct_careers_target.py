"""
Tier 3 fallback — direct career-page parser for Lever / Workday / direct domains.

Parses a company's own careers HTML page (Lever, Workday, Greenhouse direct,
etc.) for job listings. Lower confidence than the Greenhouse JSON API.
"""
from __future__ import annotations

import re
import urllib.request
from bs4 import BeautifulSoup

from src.ingestion.posting import Posting
from src.ingestion.targets.base_target import BaseTarget


class DirectCareersTarget(BaseTarget):
    """Parse a company's own careers page for job listings."""

    def __init__(self, name: str, domain: str, careers_url: str, role_keywords: list[str]):
        super().__init__(name, domain, role_keywords)
        self.careers_url = careers_url

    def fetch(self, limit: int = 50) -> list[Posting]:
        try:
            req = urllib.request.Request(
                self.careers_url, headers={"User-Agent": "Mozilla/5.0 (hunting-job-system)"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return []

        soup = BeautifulSoup(html, "html.parser")
        postings: list[Posting] = []
        seen = set()

        # Common selectors across career platforms.
        selectors = [
            "a[href*='job']",
            "a[href*='position']",
            ".job-link",
            ".job-title a",
            "li a",
        ]
        anchors = []
        for sel in selectors:
            anchors.extend(soup.select(sel))

        for a in anchors:
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if not text or not self._matches_keywords(text):
                continue
            if href.startswith("/"):
                href = f"https://{self.domain}{href}"
            key = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
            if key in seen:
                continue
            seen.add(key)
            postings.append(Posting(
                company=self.name,
                role_title=text,
                url=href,
                source="direct_careers",
                source_type="html_parse",
                confidence="low",
            ))
            if len(postings) >= limit:
                break
        return postings
