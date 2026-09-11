"""
Tier 2 fallback — HTML-parse fallback for Greenhouse/Ashby boards.

Used only if the JSON API fails (shape change, rate limit, etc.). Parses the
board's HTML listing page for job links + titles. Lower confidence than the
API, but still deterministic and free.
"""
from __future__ import annotations

import re
import urllib.request
from bs4 import BeautifulSoup

from src.ingestion.posting import Posting
from src.ingestion.targets.base_target import BaseTarget


class HtmlFallbackTarget(BaseTarget):
    """Parse a Greenhouse/Ashby HTML board page for job listings."""

    def __init__(self, name: str, domain: str, board_url: str, role_keywords: list[str]):
        super().__init__(name, domain, role_keywords)
        self.board_url = board_url

    def fetch(self, limit: int = 50) -> list[Posting]:
        try:
            req = urllib.request.Request(
                self.board_url, headers={"User-Agent": "Mozilla/5.0 (hunting-job-system)"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return []

        soup = BeautifulSoup(html, "html.parser")
        postings: list[Posting] = []
        seen = set()

        # Greenhouse boards list jobs in <a> tags with data-job-url.
        anchors = soup.find_all("a", href=True)
        for a in anchors:
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if not text or not self._matches_keywords(text):
                continue
            # Normalize the job URL.
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
                source="html_fallback",
                source_type="html_parse",
                confidence="low",
            ))
            if len(postings) >= limit:
                break
        return postings
