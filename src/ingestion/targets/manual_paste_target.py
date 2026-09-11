"""
Tier 4 fallback — manual paste.

Takes JD text the user pastes (copied from a company career page) and returns
a normalized Posting. This is the last-resort path when no automated fetch
works. Confidence is low, but the facts are human-verified.
"""
from __future__ import annotations

import re

from src.ingestion.posting import Posting
from src.ingestion.targets.base_target import BaseTarget


class ManualPasteTarget(BaseTarget):
    """Normalize a pasted JD into a Posting."""

    def __init__(self, name: str, domain: str, role_keywords: list[str], url: str = ""):
        super().__init__(name, domain, role_keywords)
        self.url = url

    def fetch(self, jd_text: str, limit: int = 50) -> list[Posting]:
        """
        Parse pasted JD text into a Posting.

        Heuristics: the first line is treated as the role title; look for
        salary/location hints in the body. The rest is the JD.
        """
        lines = [ln.strip() for ln in jd_text.strip().splitlines() if ln.strip()]
        if not lines:
            return []

        title = lines[0]
        body = "\n".join(lines[1:])

        salary = self._extract_salary(body)
        location = self._extract_location(body)

        if not self._matches_keywords(title):
            return []

        return [Posting(
            company=self.name,
            role_title=title,
            url=self.url,
            jd=body,
            salary=salary,
            location=location,
            source="manual",
            source_type="paste",
            confidence="low",
        )]

    @staticmethod
    def _extract_salary(text: str) -> str | None:
        m = re.search(r"\$[\d,]+(?:\s*[-–]\s*\$?[\d,]+)?(?:\s*/\s*(?:hr|yr|year))?", text, re.I)
        return m.group(0) if m else None

    @staticmethod
    def _extract_location(text: str) -> str | None:
        m = re.search(r"(?:Remote|Work[\s_-]from[\s_-]home)[^\n]{0,60}", text, re.I)
        return m.group(0).strip() if m else None
