"""
Module 2 — Geo-Compliance & Country Eligibility Filter.

Deterministic rules (no LLM) decide whether a Bangladesh-based B2B/EOR
contractor can legitimately apply for a posting.

Verdicts:
  - ACCEPT: clear accept keywords, no reject keywords.
  - REJECT: clear reject keywords (citizenship / PR / domestic-only).
  - REVIEW: ambiguous — e.g. "some countries only", "visa sponsorship needed".

The Country Eligibility Matrix (PLAN.md §14) tags each posting with the
target countries it could apply to and scores against the B2B/EOR status.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Bangladesh-based candidate applies as B2B independent contractor or via EOR.
# Does NOT hold a work visa for the target countries.

ACCEPT_KEYWORDS = [
    "worldwide",
    "global",
    "remote anywhere",
    "remote-anywhere",
    "anywhere in the world",
    "b2b",
    "eor",
    "employer of record",
    "contractor",
    "we hire globally",
    "hire globally",
    "remote - worldwide",
    "remote worldwide",
    "location-independent",
    "work from anywhere",
]

REJECT_KEYWORDS = [
    "us citizen",
    "citizen required",
    "must be a us citizen",
    "must reside in",
    "must reside within",
    "domestic",
    "local tax",
    "requires sponsorship",
    "visa sponsorship required",
    "work authorization you do not hold",
    "permanent residency",
    "pr required",
    "must have work authorization",
    "authorized to work in",
    "need active work authorization",
]

REVIEW_KEYWORDS = [
    "visa sponsorship needed",
    "some countries only",
    "certain countries",
    "limited to",
    "restricted to",
    "only residents",
    "country-specific",
]

# Target countries from the Country Eligibility Matrix (PLAN.md §14).
TARGET_COUNTRIES = [
    "United Kingdom",
    "European Union",
    "USA",
    "Canada",
    "Australia",
    "New Zealand",
    "Middle East",
]


@dataclass
class GeoComplianceResult:
    verdict: str                      # ACCEPT | REJECT | REVIEW
    confidence: float                 # 0.0 (ambiguous) .. 1.0 (certain)
    matched_accept: list[str] = field(default_factory=list)
    matched_reject: list[str] = field(default_factory=list)
    matched_review: list[str] = field(default_factory=list)
    eligible_countries: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def eligible(self) -> bool:
        return self.verdict == "ACCEPT"


class GeoComplianceFilter:
    """Deterministic geo-compliance gate for a Bangladesh-based B2B/EOR contractor."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    def evaluate(self, jd_text: str, company_domain: str = "",
                 posting_url: str = "") -> GeoComplianceResult:
        """
        Evaluate a JD against the geo-compliance rules.

        jd_text: the full job description (or role title + JD).
        company_domain: the target company's known domain (for fraud cross-check).
        posting_url: the posting URL (host must match company_domain).
        """
        text = (jd_text or "").lower()

        matched_accept = [k for k in ACCEPT_KEYWORDS if k in text]
        matched_reject = [k for k in REJECT_KEYWORDS if k in text]
        matched_review = [k for k in REVIEW_KEYWORDS if k in text]

        # Priority: REJECT beats everything (safety). Then ACCEPT. Then REVIEW.
        if matched_reject:
            return GeoComplianceResult(
                verdict="REJECT",
                confidence=0.95,
                matched_reject=matched_reject,
                eligible_countries=[],
                reason=f"Reject keywords found: {', '.join(matched_reject)}",
            )

        if matched_accept:
            return GeoComplianceResult(
                verdict="ACCEPT",
                confidence=0.9,
                matched_accept=matched_accept,
                eligible_countries=self._eligible_countries(text),
                reason=f"Accept keywords found: {', '.join(matched_accept)}",
            )

        if matched_review:
            return GeoComplianceResult(
                verdict="REVIEW",
                confidence=0.5,
                matched_review=matched_review,
                eligible_countries=self._eligible_countries(text),
                reason=f"Ambiguous: {', '.join(matched_review)}",
            )

        # No clear signal — queue for human review rather than guessing.
        return GeoComplianceResult(
            verdict="REVIEW",
            confidence=0.3,
            eligible_countries=self._eligible_countries(text),
            reason="No clear geo signal; queued for human review.",
        )

    def _eligible_countries(self, text: str) -> list[str]:
        """
        Which target countries this posting could apply to.

        Heuristic: if the JD names specific countries, restrict to those.
        Otherwise, assume all target countries (B2B/EOR covers them).
        """
        named = []
        for country in TARGET_COUNTRIES:
            if country == "Middle East":
                if re.search(r"\b(uae|qatar|dubai|saudi|oman|bahrain|kuwait)\b", text):
                    named.append("Middle East")
                continue
            for token in country.lower().split():
                if token and re.search(rf"\b{token}\b", text):
                    named.append(country)
                    break

        if named:
            return named
        return list(TARGET_COUNTRIES)
