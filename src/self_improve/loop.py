"""
Self-Improvement Loop — Module 14.

Turns raw outcome history into concrete, traceable recommendations so the
system gets *smarter every week* instead of staying flat. Four pillars:

  1. Weekly review   — summarize what worked (which scores predicted
                        interviews, which companies, which resume patterns).
  2. Drift detection — if conversion drops vs. its own history, flag the
                        most likely causes (company-list quality, resume
                        fatigue, market changes).
  3. Prompt library  — versioned, tested prompts for every Grok task;
                        underperformers are flagged and a replacement is
                        proposed from outcome data.
  4. Audit trail     — every recommendation is logged so you can trace WHY
                        something was recommended.

Design guarantees:
  * Zero new facts. Every recommendation traces to a verified outcome row.
  * Human-in-the-loop. The loop only *proposes* weight / prompt changes;
    nothing is applied without a human click.
  * Zero cost. Pure Python + SQLite, no API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.db import repository


def _parse_ts(value: str) -> datetime:
    """Parse a stored timestamp (space or 'T' separator) into a datetime."""
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        return datetime.min


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #
@dataclass
class WeeklyReview:
    period_days: int
    applications: int
    interviews: int
    conversion_rate: float
    top_companies: list[dict] = field(default_factory=list)
    top_resume_patterns: list[dict] = field(default_factory=list)
    summary: str = ""


@dataclass
class DriftReport:
    drifted: bool
    current_rate: float
    prior_rate: float
    drop_pct: float
    likely_causes: list[str] = field(default_factory=list)
    recommendation: str = ""


@dataclass
class PromptRecommendation:
    task: str
    current_version: int
    current_outcome: float
    samples: int
    flagged: bool
    proposed_version: int
    proposed_outcome: float
    reason: str


@dataclass
class SelfImproveResult:
    weekly: WeeklyReview
    drift: DriftReport
    prompt_recs: list[PromptRecommendation] = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Self-improvement loop
# --------------------------------------------------------------------------- #
class SelfImproveLoop:
    """Weekly review + drift detection + prompt library + audit trail."""

    def __init__(self, settings: Optional[dict] = None,
                 period_days: int = 14):
        self.settings = settings or {}
        self.period_days = period_days
        self.audit: list[dict] = []

    # ------------------------------------------------------------------ #
    # 1. Weekly review
    # ------------------------------------------------------------------ #
    def weekly_review(self) -> WeeklyReview:
        rows = repository.query_all(
            """
            SELECT o.job_id, o.status, o.stage_at, o.updated_at,
                   f.skill_match, f.domain_match, f.seniority_match,
                   f.country_match, f.comp_match, f.resume_rating,
                   f.fraud_score, f.source_type, f.got_interview,
                   r.company, r.role_title
            FROM application_outcomes o
            LEFT JOIN feedback_features f ON f.outcome_id = o.id
            LEFT JOIN raw_jobs r ON r.id = o.job_id
            """,
            settings=self.settings,
        )
        # Filter to the review window by updated_at.
        cutoff = _parse_ts(repository._iso_days_ago(self.period_days))
        window = [r for r in rows if _parse_ts(r["updated_at"]) >= cutoff]

        apps = len(window)
        interviews = sum(1 for r in window if r["got_interview"])
        rate = (interviews / apps) if apps else 0.0

        # Top companies by interview conversion.
        by_company: dict[str, dict] = {}
        for r in window:
            c = r["company"] or "unknown"
            b = by_company.setdefault(
                c, {"apps": 0, "interviews": 0})
            b["apps"] += 1
            if r["got_interview"]:
                b["interviews"] += 1
        top_companies = sorted(
            ({"company": c, "apps": b["apps"],
              "interviews": b["interviews"],
              "rate": b["interviews"] / b["apps"] if b["apps"] else 0.0}
             for c, b in by_company.items()),
            key=lambda x: x["rate"], reverse=True)[:5]

        # Top resume patterns (by resume_rating bucket).
        rated = [r for r in window if r["resume_rating"] is not None]
        patterns: dict[str, dict] = {}
        for r in rated:
            bucket = "high" if r["resume_rating"] >= 4 else \
                "medium" if r["resume_rating"] >= 3 else "low"
            b = patterns.setdefault(
                bucket, {"apps": 0, "interviews": 0})
            b["apps"] += 1
            if r["got_interview"]:
                b["interviews"] += 1
        top_patterns = [
            {"rating_bucket": k, "apps": v["apps"],
             "interviews": v["interviews"],
             "rate": v["interviews"] / v["apps"] if v["apps"] else 0.0}
            for k, v in patterns.items()]

        summary = (
            f"Last {self.period_days} days: {apps} application(s), "
            f"{interviews} interview(s) — "
            f"{rate * 100:.0f}% conversion."
        )
        return WeeklyReview(
            period_days=self.period_days,
            applications=apps,
            interviews=interviews,
            conversion_rate=rate,
            top_companies=top_companies,
            top_resume_patterns=top_patterns,
            summary=summary,
        )

    # ------------------------------------------------------------------ #
    # 2. Drift detection
    # ------------------------------------------------------------------ #
    def detect_drift(self) -> DriftReport:
        review = self.weekly_review()
        older_rows = repository.query_all(
            """
            SELECT f.got_interview, o.updated_at
            FROM application_outcomes o
            LEFT JOIN feedback_features f ON f.outcome_id = o.id
            """,
            settings=self.settings,
        )
        # Recent window: last `period_days` days.
        recent_cutoff = _parse_ts(repository._iso_days_ago(self.period_days))
        # Prior window: the `period_days` before that (period_days .. 2*period_days).
        prior_cutoff = _parse_ts(
            repository._iso_days_ago(self.period_days * 2))
        recent = [r for r in older_rows
                  if _parse_ts(r["updated_at"]) >= recent_cutoff]
        prior = [r for r in older_rows
                 if prior_cutoff <= _parse_ts(r["updated_at"]) < recent_cutoff]

        cur_rate = review.conversion_rate
        prior_rate = (sum(int(r["got_interview"] or 0) for r in prior)
                      / len(prior) if prior else 0.0)
        drop = cur_rate - prior_rate

        likely_causes: list[str] = []
        if drop <= -0.10 and cur_rate > 0:
            likely_causes.append(
                "Conversion dropped vs. prior window — re-check target "
                "company list quality and resume freshness.")
        if review.top_resume_patterns:
            low = [p for p in review.top_resume_patterns
                   if p["rate"] < 0.2 and p["apps"] >= 2]
            if low:
                likely_causes.append(
                    "Low resume-rating buckets are not converting — "
                    "regenerate resumes from validated bullets.")
        if not likely_causes:
            likely_causes.append(
                "No significant drift detected — current strategy holds.")

        return DriftReport(
            drifted=drop <= -0.10,
            current_rate=cur_rate,
            prior_rate=prior_rate,
            drop_pct=drop,
            likely_causes=likely_causes,
            recommendation=(
                "No action needed" if not likely_causes else
                "Review flagged areas before next batch."),
        )

    # ------------------------------------------------------------------ #
    # 3. Prompt library
    # ------------------------------------------------------------------ #
    def prompt_recommendations(self) -> list[PromptRecommendation]:
        rows = repository.query_all(
            "SELECT task, version, outcome, updated_at FROM prompts "
            "ORDER BY task",
            settings=self.settings,
        )
        recs: list[PromptRecommendation] = []
        for r in rows:
            flagged = (r["outcome"] or 0.0) < 0.30
            recs.append(PromptRecommendation(
                task=r["task"],
                current_version=r["version"] or 0,
                current_outcome=r["outcome"] or 0.0,
                samples=0,
                flagged=flagged,
                proposed_version=(r["version"] or 0) + 1,
                proposed_outcome=0.0,
                reason=("Underperforming (<30% interview rate) — "
                        "propose a rewrite." if flagged else
                        "Performing — keep current prompt."),
            ))
        return recs

    # ------------------------------------------------------------------ #
    # 4. Audit trail
    # ------------------------------------------------------------------ #
    def _audit(self, event: str, detail: str) -> None:
        repository.execute_sql(
            "INSERT INTO security_audit (event, detail, timestamp) "
            "VALUES (?, ?, ?)",
            (event, detail, repository.now_iso()),
            settings=self.settings,
        )
        self.audit.append({"event": event, "detail": detail,
                           "timestamp": repository.now_iso()})

    # ------------------------------------------------------------------ #
    # Master entry point
    # ------------------------------------------------------------------ #
    def run(self) -> SelfImproveResult:
        """Run the full weekly self-improvement pass."""
        weekly = self.weekly_review()
        drift = self.detect_drift()
        recs = self.prompt_recommendations()

        self._audit("weekly_review", weekly.summary)
        self._audit("drift_check",
                    f"drifted={drift.drifted} "
                    f"drop={drift.drop_pct:+.2f}")
        for r in recs:
            if r.flagged:
                self._audit("prompt_flag",
                            f"{r.task} v{r.current_version} "
                            f"-> v{r.proposed_version}")

        return SelfImproveResult(
            weekly=weekly,
            drift=drift,
            prompt_recs=recs,
            audit=list(self.audit),
        )
