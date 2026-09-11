"""
Base target interface — every target implements `fetch() -> list[Posting]`.

Tier 1 (Greenhouse API) is the highest confidence. Tiers 2-4 are fallbacks
used only when tier 1 fails. Each target carries a `platform` label so the
fallback chain knows which strategy to try next.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.ingestion.posting import Posting


class BaseTarget(ABC):
    """A single target company's ingestion strategy."""

    def __init__(self, name: str, domain: str, role_keywords: list[str]):
        self.name = name
        self.domain = domain
        self.role_keywords = role_keywords

    @abstractmethod
    def fetch(self, limit: int = 50) -> list[Posting]:
        """Return normalized postings for this target."""
        raise NotImplementedError

    def _matches_keywords(self, title: str) -> bool:
        """True if the role title matches any configured keyword."""
        if not self.role_keywords:
            return True
        t = title.lower()
        return any(kw.lower() in t for kw in self.role_keywords)
