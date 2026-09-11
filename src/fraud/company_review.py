"""
Module 3b — Company Review (legitimacy + hiring activity).

A per-company reputation score, SEPARATE from any single posting's fraud
score. Aggregated across all of a company's live postings.

Composite score 0–100 → green / amber / red badge shown on every job card
and surfaced in the Analytics leaderboard.

Two dimensions:
  - Legitimacy: official website vs posting domain, presence of real contact
    info / physical address / company profiles, company-page existence.
  - Hiring activity: is the company actively and continuously hiring
    (multiple live postings) vs a single stale posting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompanyReview:
    company: str
    legitimacy_score: int
    activity_score: int
    composite_score: int
    level: str                      # green | amber | red
    live_postings: int = 0
    flags: list[str] = field(default_factory=list)
    reason: str = ""


class CompanyReviewEngine:
    """Aggregates legitimacy + hiring activity per company."""

    def __init__(self):
        # Per-company accumulators.
        self._postings: dict[str, list[dict]] = {}

    def add_posting(self, company: str, domain: str, has_contact: bool,
                    has_address: bool, has_company_page: bool,
                    is_live: bool = True) -> None:
        """Record one posting's signals for a company."""
        self._postings.setdefault(company, []).append({
            "domain": domain,
            "has_contact": has_contact,
            "has_address": has_address,
            "has_company_page": has_company_page,
            "is_live": is_live,
        })

    def review(self, company: str) -> CompanyReview:
        """Compute the composite company review for a company."""
        postings = self._postings.get(company, [])
        if not postings:
            return CompanyReview(
                company=company,
                legitimacy_score=0,
                activity_score=0,
                composite_score=0,
                level="red",
                reason="no postings recorded",
            )

        # ----- Legitimacy (0–100) -----
        legit = 0
        flags: list[str] = []

        # Company-page existence is the single strongest legitimacy signal.
        pages = sum(1 for p in postings if p["has_company_page"])
        if pages >= 1:
            legit += 40
        else:
            flags.append("no company page found")

        # Real contact info.
        contacts = sum(1 for p in postings if p["has_contact"])
        if contacts >= 1:
            legit += 30
        else:
            legit += 10
            flags.append("no contact info")

        # Physical address.
        addresses = sum(1 for p in postings if p["has_address"])
        if addresses >= 1:
            legit += 30
        else:
            legit += 5
            flags.append("no physical address")

        legitimacy = max(0, min(100, legit))

        # ----- Hiring activity (0–100) -----
        live = sum(1 for p in postings if p["is_live"])
        total = len(postings)
        if live >= 5:
            activity = 100
        elif live >= 3:
            activity = 75
        elif live >= 1:
            activity = 50
        else:
            activity = 20
        if total == 1:
            activity = min(activity, 40)
            flags.append("single posting — low activity signal")

        # ----- Composite -----
        composite = int(round(0.6 * legitimacy + 0.4 * activity))
        level = _score_to_level(composite)
        reason = (
            f"legitimacy {legitimacy}, activity {activity}, "
            f"{live} live of {total} postings"
        )
        if flags:
            reason += " | " + "; ".join(flags)

        return CompanyReview(
            company=company,
            legitimacy_score=legitimacy,
            activity_score=activity,
            composite_score=composite,
            level=level,
            live_postings=live,
            flags=flags,
            reason=reason,
        )

    def all_reviews(self) -> dict[str, CompanyReview]:
        """Compute reviews for every company seen."""
        return {c: self.review(c) for c in sorted(self._postings)}


def _score_to_level(score: int) -> str:
    if score >= 70:
        return "green"
    if score >= 40:
        return "amber"
    return "red"
