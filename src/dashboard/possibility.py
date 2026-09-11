"""
Brutal-possibility engine — Module 5.

The single most honest number on the dashboard: the real probability that a
job gets you an interview. It is NOT optimistic. If the math says 12%, it
says 12%.

Formula (multiplicative — every factor must pass):

    possibility_pct = match_score
                      × geo_gate
                      × fraud_gate
                      × historical_rate

  * match_score      : 0-100, the deterministic+LLM match (skills/domain/
                       seniority/country/comp breakdown).
  * geo_gate         : 1.0 if the candidate is geo-eligible, else 0.0. A hard
                       gate — you cannot interview for a job you cannot apply
                       to.
  * fraud_gate       : fraud_score / 100. A red posting (fraud_score 0) is
                       rejected outright → 0.
  * historical_rate  : YOUR real interview conversion rate, learned from past
                       outcomes (Module 8). Defaults to a conservative 0.15
                       until you have enough data.

The result is a percentage (0-100). It is "brutal" because it compounds four
independent gates — a great match still dies if the country is wrong, the
posting is a scam, or your historical conversion is low.

Calibration: the dashboard compares this predicted probability against the
actual conversion rate (Module 8). If it is consistently wrong, the weights
self-correct. Until you have `outcomes_before_trusted` real outcomes, the
number is labelled "unproven" and uses the default historical rate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Conservative prior interview-conversion rate for a well-targeted BA/B2B role.
# This is what the dashboard uses until you have 30+ real outcomes to learn from.
DEFAULT_HISTORICAL_RATE = 0.15

# A posting is "fraud-clean" at green (>=70). Amber (40-69) is allowed but
# discounts the possibility proportionally. Red (red) → 0.
FRAUD_GREEN_THRESHOLD = 70


@dataclass
class PossibilityResult:
    pct: float                          # 0-100, the brutal possibility
    match_score: float
    geo_gate: float
    fraud_gate: float
    historical_rate: float
    components: dict = field(default_factory=dict)
    trusted: bool = False               # True only with enough real outcomes
    gated_out: Optional[str] = None     # which hard gate zeroed it out (if any)

    @property
    def rounded(self) -> float:
        return round(self.pct, 1)


class PossibilityEngine:
    """Computes the brutal-possibility percentage for a single job."""

    def __init__(self, historical_rate: float = DEFAULT_HISTORICAL_RATE,
                 outcomes: int = 0, outcomes_before_trusted: int = 30):
        self.historical_rate = max(0.0, min(1.0, historical_rate))
        self.outcomes = int(outcomes)
        self.outcomes_before_trusted = int(outcomes_before_trusted)

    def compute(self, match_score: float, geo_eligible: bool,
                fraud_score: Optional[int] = None,
                historical_rate: Optional[float] = None) -> PossibilityResult:
        """
        Compute the brutal possibility for one job.

        match_score     : 0-100 deterministic+LLM match.
        geo_eligible    : can the candidate legally apply? (hard gate)
        fraud_score     : 0-100 fraud engine score. None → treated as unknown
                          (gate = 1.0, but flagged).
        historical_rate : override the engine's learned rate for this call.
        """
        rate = self.historical_rate if historical_rate is None \
            else max(0.0, min(1.0, historical_rate))
        trusted = self.outcomes >= self.outcomes_before_trusted

        match_score = max(0.0, min(100.0, float(match_score or 0.0)))

        # Hard gate 1 — geo eligibility.
        geo_gate = 1.0 if geo_eligible else 0.0
        gated_out = None if geo_eligible else "geo-ineligible"

        # Hard gate 2 — fraud. Red (score 0) → 0. Otherwise scale by score.
        if fraud_score is None:
            fraud_gate = 1.0
        else:
            fraud_score = max(0, min(100, int(fraud_score)))
            if fraud_score < FRAUD_GREEN_THRESHOLD:
                gated_out = gated_out or ("fraud" if fraud_score == 0 else "fraud-amber")
            fraud_gate = fraud_score / 100.0

        pct = match_score * geo_gate * fraud_gate * rate
        return PossibilityResult(
            pct=pct,
            match_score=match_score,
            geo_gate=geo_gate,
            fraud_gate=fraud_gate,
            historical_rate=rate,
            components={
                "match_score": match_score,
                "geo_gate": geo_gate,
                "fraud_gate": fraud_gate,
                "historical_rate": rate,
            },
            trusted=trusted,
            gated_out=gated_out,
        )

    def historical_rate_from_outcomes(self, interview_count: int,
                                      applied_count: int) -> float:
        """
        Learn your real interview-conversion rate from tracked outcomes.

        Returns a rate in [0, 1]. Falls back to the conservative prior when
        there is no data. Uses Bayesian smoothing so a handful of outcomes
        cannot swing the number wildly.
        """
        prior = DEFAULT_HISTORICAL_RATE
        prior_weight = 10  # pseudo-outcomes from the prior
        n = max(0, int(applied_count))
        k = max(0, int(interview_count))

        if n <= 0:
            return prior
        # Laplace-smoothed posterior: (k + prior*weight) / (n + weight)
        posterior = (k + prior * prior_weight) / (n + prior_weight)
        return max(0.0, min(1.0, posterior))
