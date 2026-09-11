"""
Raw posting model + normalization.

A `Posting` is one job listing pulled from a target company's career page.
It carries a `confidence` label (high/medium/low) and a `source` label so the
dashboard can show how trustworthy each listing is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Posting:
    company: str
    role_title: str
    url: str
    jd: str = ""
    salary: Optional[str] = None
    location: Optional[str] = None
    posted_at: Optional[str] = None
    canonical_ats_url: Optional[str] = None
    source: str = "manual"          # greenhouse | ashby_html | direct | manual
    source_type: str = "career_page"
    confidence: str = "medium"       # high | medium | low

    # ----- normalization -----
    @property
    def normalized_id(self) -> str:
        """
        Stable id for dedup: (company + role_title + canonical_url).

        Two postings that differ only in URL tracking params still dedup.
        """
        base = self.canonical_ats_url or self.url
        key = _normalize_url(base)
        return f"{_slug(self.company)}|{_slug(self.role_title)}|{key}"

    def to_dict(self) -> dict:
        return {
            "company": self.company,
            "role_title": self.role_title,
            "url": self.url,
            "jd": self.jd,
            "salary": self.salary,
            "location": self.location,
            "posted_at": self.posted_at,
            "canonical_ats_url": self.canonical_ats_url,
            "source": self.source,
            "source_type": self.source_type,
            "confidence": self.confidence,
        }


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def _normalize_url(url: str) -> str:
    """
    Normalize a URL for dedup: strip scheme, trailing slash, and query
    parameters. Keeps host + path.

    Preserves `gh_jid` (Greenhouse's unique job id) so two distinct jobs are
    not wrongly collapsed. Tracking params (utm_*, ref, etc.) are stripped.
    """
    if not url:
        return ""
    # strip scheme + www
    u = re.sub(r"^https?://", "", url)
    u = re.sub(r"^www\.", "", u)
    # keep only gh_jid query param, drop everything else
    if "?" in u:
        path, query = u.split("?", 1)
        keep = []
        for part in query.split("&"):
            if part.startswith("gh_jid="):
                keep.append(part)
        u = path.rstrip("/") + ("?" + "&".join(keep) if keep else "")
    return u.lower()


def host_of(url: str) -> str:
    """Return the host (lowercased, no www) of a URL — used by the fraud check."""
    if not url:
        return ""
    u = re.sub(r"^https?://", "", url)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0].lower()
