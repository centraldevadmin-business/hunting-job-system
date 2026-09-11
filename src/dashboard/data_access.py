"""
Dashboard data-access layer — Module 5.

Reads the career.db tables and assembles the objects the dashboard renders:
per-job cards, queue controls, funnel counts, calibration data, and the
company-review leaderboard.

This module is pure data access — no UI, no business logic. Every query is a
simple SELECT against the schema established in earlier modules.

Design note: scored_jobs may be empty (Module 2 not yet run on this DB). The
dashboard must still open and show the raw jobs collected, so this layer
degrades gracefully: it surfaces raw_jobs with computed/empty fields rather
than crashing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.db.repository import (
    query_all,
    query_one,
    get_connection,
    now_iso,
    execute_sql,
)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class JobCard:
    """One row in the Hunt queue — everything the card needs to render."""
    job_id: str
    company: str
    role_title: str
    url: str
    canonical_ats_url: Optional[str]
    jd: str
    salary: Optional[str]
    source_type: Optional[str]
    fetched_at: Optional[str]

    # Pipeline results (may be None if not yet scored).
    match_score: Optional[float]
    geo_eligible: Optional[bool]
    geo_confidence: Optional[float]
    fraud_score: Optional[int]
    fraud_flags: Optional[str]
    status: Optional[str]
    reviewed_at: Optional[str]

    # Derived.
    distill: dict = field(default_factory=dict)
    company_review: Optional[dict] = None
    resume: Optional[dict] = None          # {pdf_path, validated, resume_text}
    possibility_pct: Optional[float] = None
    # Estimated/real salary range (SalaryEstimate or None).
    salary_estimate: object = None
    possibility_components: dict = field(default_factory=dict)
    possibility_trusted: bool = False

    # Tracking dates (populated from applications / authorized_jobs).
    authorized_at: Optional[str] = None
    applied_at: Optional[str] = None
    submitted_at: Optional[str] = None
    last_updated: Optional[str] = None
    ats_url: Optional[str] = None
    contact_email: Optional[str] = None
    interview_count: Optional[int] = None
    notes: Optional[str] = None

    # Deadline + freshness (Module 2b). None when the JD names no deadline.
    deadline: Optional[str] = None
    posted_at: Optional[str] = None
    days_remaining: Optional[int] = None
    is_urgent: bool = False
    freshness: Optional[str] = None


@dataclass
class QueueFilter:
    """Controls for the Hunt queue."""
    min_possibility: float = 0.0
    country: str = "All"
    fraud_status: str = "All"           # All|green|amber|red
    min_fetched_days_ago: Optional[int] = None
    sort: str = "possibility_desc"       # possibility_desc|match_desc|newest|oldest
    only_unreviewed: bool = False


# --------------------------------------------------------------------------- #
# Job cards
# --------------------------------------------------------------------------- #
def list_job_cards(settings=None) -> list[JobCard]:
    """
    Return every collected job as a JobCard, joined with scored_jobs,
    jd_distill, company_reviews, and resumes where present.

    Falls back to raw_jobs when scored_jobs is empty.
    """
    cards: list[JobCard] = []

    scored = query_all(
        """
        SELECT sj.id, sj.geo_eligible, sj.geo_confidence, sj.fraud_score,
               sj.fraud_flags, sj.match_score, sj.status, sj.reviewed_at,
               rj.company, rj.role_title, rj.url, rj.canonical_ats_url,
               rj.jd, rj.salary, rj.source_type, rj.fetched_at
        FROM scored_jobs sj
        JOIN raw_jobs rj ON rj.id = sj.id
        ORDER BY sj.match_score DESC, sj.id
        """,
        settings=settings,
    )

    if scored:
        rows = scored
    else:
        # No scored jobs yet — surface raw jobs directly.
        rows = query_all(
            """
            SELECT id, company, role_title, url, canonical_ats_url, jd,
                   salary, source_type, fetched_at
            FROM raw_jobs
            ORDER BY COALESCE(fetched_at, '') DESC, id
            """,
            settings=settings,
        )

    for row in rows:
        cards.append(_build_card(row, settings))

    return cards


def _build_card(row: dict, settings=None) -> JobCard:
    """Assemble a JobCard from a joined DB row."""
    job_id = row["id"]

    distill = query_one(
        "SELECT * FROM jd_distill WHERE job_id = ?", (job_id,), settings=settings
    ) or {}

    company = row.get("company") or ""
    company_review = None
    if company:
        company_review = query_one(
            "SELECT * FROM company_reviews WHERE company = ?", (company,),
            settings=settings,
        )

    resume = query_one(
        "SELECT * FROM resumes WHERE job_id = ?", (job_id,), settings=settings
    )

    # Tracking dates from applications + authorized_jobs.
    app = query_one(
        "SELECT * FROM applications WHERE job_id = ?", (job_id,), settings=settings
    )
    auth = query_one(
        "SELECT * FROM authorized_jobs WHERE job_id = ?", (job_id,), settings=settings
    )

    # Deadline + freshness (Module 2b).
    dl = query_one(
        "SELECT * FROM job_deadlines WHERE job_id = ?", (job_id,), settings=settings
    )

    card = JobCard(
        job_id=job_id,
        company=company,
        role_title=row.get("role_title") or "",
        url=row.get("url") or "",
        canonical_ats_url=row.get("canonical_ats_url"),
        jd=row.get("jd") or "",
        salary=row.get("salary"),
        source_type=row.get("source_type"),
        fetched_at=row.get("fetched_at"),
        match_score=row.get("match_score"),
        geo_eligible=_as_bool(row.get("geo_eligible")),
        geo_confidence=row.get("geo_confidence"),
        fraud_score=row.get("fraud_score"),
        fraud_flags=row.get("fraud_flags"),
        status=row.get("status"),
        reviewed_at=row.get("reviewed_at"),
        distill=distill,
        company_review=company_review,
        resume=_summarize_resume(resume),
        authorized_at=auth.get("authorized_at") if auth else None,
        applied_at=app.get("applied_at") if app else None,
        submitted_at=app.get("applied_at") if app else None,
        last_updated=app.get("last_updated") if app else None,
        ats_url=app.get("ats_url") if app else None,
        contact_email=app.get("contact_email") if app else None,
        interview_count=app.get("interview_count") if app else None,
        notes=app.get("notes") if app else None,
        deadline=dl.get("deadline") if dl else None,
        posted_at=dl.get("posted_at") if dl else None,
        days_remaining=dl.get("days_remaining") if dl else None,
        is_urgent=bool(dl.get("is_urgent")) if dl else False,
        freshness=dl.get("freshness") if dl else None,
    )

    # Estimated salary (real if present, else market estimate).
    from src.dashboard.salary_estimator import estimate_salary
    card.salary_estimate = estimate_salary(
        company=company,
        role_title=row.get("role_title") or "",
        real_salary=row.get("salary"),
    )

    # Compute brutal possibility.
    from src.dashboard.possibility import PossibilityEngine
    engine = _possibility_engine(settings)
    pr = engine.compute(
        match_score=card.match_score if card.match_score is not None else 0.0,
        geo_eligible=bool(card.geo_eligible),
        fraud_score=card.fraud_score,
    )
    card.possibility_pct = pr.pct
    card.possibility_components = pr.components
    card.possibility_trusted = pr.trusted
    return card


def _summarize_resume(resume: Optional[dict]) -> Optional[dict]:
    if not resume:
        return None
    return {
        "pdf_path": resume.get("pdf_path"),
        "validated": bool(resume.get("validated")),
        "resume_text": resume.get("resume_text"),
    }


def _as_bool(v) -> Optional[bool]:
    if v is None:
        return None
    return bool(v)


def _possibility_engine(settings=None) -> "PossibilityEngine":
    """Build a possibility engine with the learned historical rate.

    Delegates to the Module 8 self-tuning LearningEngine so the historical
    rate, maintenance-mode flag, and calibration are computed in one place.
    """
    from src.dashboard.possibility import PossibilityEngine
    from src.learning.match_engine import LearningEngine

    engine = LearningEngine(settings)
    result = engine.learn()
    return PossibilityEngine(
        historical_rate=result.historical_rate,
        outcomes=result.sample_count,
        outcomes_before_trusted=engine.outcomes_before_trusted,
    )


# --------------------------------------------------------------------------- #
# Queue controls
# --------------------------------------------------------------------------- #
def filter_cards(cards: list[JobCard], f: QueueFilter) -> list[JobCard]:
    """Apply the Hunt queue filters and sort."""
    out = cards

    if f.min_possibility and f.min_possibility > 0:
        out = [c for c in out if (c.possibility_pct or 0.0) >= f.min_possibility]

    if f.country and f.country != "All":
        out = [c for c in out if _job_country(c) == f.country]

    if f.fraud_status and f.fraud_status != "All":
        out = [c for c in out if _fraud_level(c) == f.fraud_status]

    if f.min_fetched_days_ago:
        cutoff = _days_ago_iso(f.min_fetched_days_ago)
        out = [c for c in out if c.fetched_at and c.fetched_at >= cutoff]

    if f.only_unreviewed:
        out = [c for c in out if not c.status]

    out = _sort_cards(out, f.sort)
    return out


def _sort_cards(cards: list[JobCard], sort: str) -> list[JobCard]:
    if sort == "match_desc":
        return sorted(cards, key=lambda c: c.match_score or 0.0, reverse=True)
    if sort == "newest":
        return sorted(cards, key=lambda c: c.fetched_at or "", reverse=True)
    if sort == "oldest":
        return sorted(cards, key=lambda c: c.fetched_at or "")
    # default: possibility desc
    return sorted(cards, key=lambda c: c.possibility_pct or 0.0, reverse=True)


def _job_country(card: JobCard) -> str:
    """Best-effort country tag from geo eligibility / distill."""
    if card.geo_eligible:
        return "Eligible"
    if card.geo_eligible is False:
        return "Ineligible"
    return "Unknown"


def _fraud_level(card: JobCard) -> str:
    """Map a fraud_score to a level label for filtering."""
    s = card.fraud_score
    if s is None:
        return "amber"  # unknown → treat as amber (review)
    if s >= 70:
        return "green"
    if s >= 40:
        return "amber"
    return "red"


def _days_ago_iso(days: int) -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------- #
# Status transitions
# --------------------------------------------------------------------------- #
STATUS_FLOW = ["new", "authorized", "applied", "interview", "final", "offer", "reject", "silent"]


def set_status(job_id: str, new_status: str, settings=None) -> bool:
    """
    One-click status update on a job. Writes to scored_jobs (and mirrors to
    applications if a row exists). No email access.

    Returns True on success.
    """
    if new_status not in STATUS_FLOW:
        return False

    with get_connection(settings) as conn:
        conn.execute(
            "UPDATE scored_jobs SET status = ?, reviewed_at = ? WHERE id = ?",
            (new_status, now_iso(), job_id),
        )
        # Mirror to applications if present.
        conn.execute(
            "UPDATE applications SET status = ?, last_updated = ? WHERE job_id = ?",
            (new_status, now_iso(), job_id),
        )
        conn.commit()
    return True


def authorize_job(job_id: str, settings=None) -> bool:
    """Record human authorization to apply to a job."""
    existing = query_one(
        "SELECT 1 FROM authorized_jobs WHERE job_id = ?", (job_id,), settings=settings
    )
    if existing:
        return True
    execute_sql(
        "INSERT INTO authorized_jobs (job_id, authorized_at, submitted) VALUES (?, ?, 0)",
        (job_id, now_iso()),
        settings=settings,
    )
    # Also bump the job status to 'authorized'.
    set_status(job_id, "authorized", settings=settings)
    return True


def is_authorized(job_id: str, settings=None) -> bool:
    return query_one(
        "SELECT 1 FROM authorized_jobs WHERE job_id = ?", (job_id,), settings=settings
    ) is not None


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
def funnel_counts(settings=None) -> dict:
    """Collected → authorized → submitted → interview → offer."""
    collected = query_one("SELECT COUNT(*) AS n FROM raw_jobs", settings=settings)
    collected_n = collected["n"] if collected else 0

    scored = query_one("SELECT COUNT(*) AS n FROM scored_jobs", settings=settings)
    scored_n = scored["n"] if scored else 0

    accepted = query_one(
        "SELECT COUNT(*) AS n FROM scored_jobs WHERE status IN ('accepted','authorized','applied','interview','final','offer')",
        settings=settings,
    )
    accepted_n = accepted["n"] if accepted else 0

    submitted = query_one(
        "SELECT COUNT(*) AS n FROM applications WHERE status IN ('applied','screen','interview','final','offer')",
        settings=settings,
    )
    submitted_n = submitted["n"] if submitted else 0

    interviews = query_one(
        "SELECT COUNT(*) AS n FROM applications WHERE status IN ('interview','final','offer')",
        settings=settings,
    )
    interviews_n = interviews["n"] if interviews else 0

    offers = query_one(
        "SELECT COUNT(*) AS n FROM applications WHERE status = 'offer'",
        settings=settings,
    )
    offers_n = offers["n"] if offers else 0

    return {
        "collected": collected_n,
        "scored": scored_n,
        "accepted": accepted_n,
        "submitted": submitted_n,
        "interviews": interviews_n,
        "offers": offers_n,
    }


def calibration_data(settings=None) -> list[dict]:
    """
    Predicted-probability vs actual-converted scatter for the calibration plot.

    One point per job that has a tracked outcome. predicted = possibility_pct
    at the time; actual = 1.0 if it produced an interview, else 0.0.
    """
    rows = query_all(
        """
        SELECT oj.job_id, oj.status, oj.stage_at, oj.created_at
        FROM application_outcomes oj
        """,
        settings=settings,
    )
    points = []
    for o in rows:
        card = _build_card(
            {
                "id": o["job_id"],
                "match_score": None,
                "geo_eligible": None,
                "fraud_score": None,
            },
            settings,
        )
        actual = 1.0 if o.get("status") in ("interview", "final", "offer") else 0.0
        points.append({
            "job_id": o["job_id"],
            "predicted": card.possibility_pct or 0.0,
            "actual": actual,
            "status": o.get("status"),
        })
    return points


def top_levers(settings=None) -> list[dict]:
    """
    Which countries / seniorities actually got YOU interviews.

    Derived from real outcomes in application_outcomes joined to jd_distill.
    Falls back to empty when there is no data.
    """
    rows = query_all(
        """
        SELECT oj.job_id, oj.status, jd.actual_seniority
        FROM application_outcomes oj
        LEFT JOIN jd_distill jd ON jd.job_id = oj.job_id
        """,
        settings=settings,
    )
    by_seniority: dict[str, int] = {}
    for r in rows:
        if r.get("status") in ("interview", "final", "offer"):
            sen = (r.get("actual_seniority") or "unknown").strip().lower() or "unknown"
            by_seniority[sen] = by_seniority.get(sen, 0) + 1
    return [
        {"seniority": k, "interviews": v}
        for k, v in sorted(by_seniority.items(), key=lambda x: -x[1])
    ]


def company_leaderboard(settings=None) -> list[dict]:
    """Company review leaderboard — ranked by composite score."""
    rows = query_all(
        "SELECT * FROM company_reviews ORDER BY composite DESC", settings=settings
    )
    return [
        {
            "company": r["company"],
            "composite": r["composite"],
            "legitimacy": r["legitimacy"],
            "activity": r["activity"],
            "level": r["level"],
        }
        for r in rows
    ]


def followup_list(settings=None, after_days: int = 14) -> list[dict]:
    """Jobs stuck at 'applied' longer than N days → silent, flagged for nudge."""
    cutoff = _days_ago_iso(after_days)
    rows = query_all(
        """
        SELECT a.job_id, a.status, a.last_updated, a.applied_at,
               rj.role_title, rj.company
        FROM applications a
        JOIN raw_jobs rj ON rj.id = a.job_id
        WHERE a.status = 'applied' AND COALESCE(a.last_updated, a.applied_at) < ?
        ORDER BY a.applied_at ASC
        """,
        (cutoff,),
        settings=settings,
    )
    return [
        {
            "job_id": r["job_id"],
            "company": r["company"],
            "role_title": r["role_title"],
            "last_updated": r["last_updated"],
            "applied_at": r["applied_at"],
        }
        for r in rows
    ]


def collection_health(settings=None) -> list[dict]:
    """
    Flag target companies whose pages stopped updating (dead monitor).

    A company with raw_jobs whose newest fetched_at is older than the
    maintenance-mode window is flagged.
    """
    rows = query_all(
        """
        SELECT company, COUNT(*) AS n, MAX(fetched_at) AS newest
        FROM raw_jobs
        GROUP BY company
        ORDER BY newest DESC
        """,
        settings=settings,
    )
    return [
        {
            "company": r["company"],
            "postings": r["n"],
            "newest": r["newest"],
        }
        for r in rows
    ]


def dry_market_signal(settings=None, days: int = 7) -> dict:
    """
    Market-sentiment signal: zero eligible jobs for N days → "dry market,
    not a system problem."
    """
    scored = query_one("SELECT COUNT(*) AS n FROM scored_jobs", settings=settings)
    scored_n = scored["n"] if scored else 0
    eligible = query_one(
        "SELECT COUNT(*) AS n FROM scored_jobs WHERE geo_eligible = 1",
        settings=settings,
    )
    eligible_n = eligible["n"] if eligible else 0
    return {
        "eligible_jobs": eligible_n,
        "dry": eligible_n == 0,
        "note": (
            "Dry market — not a system problem. Expand role keywords or add "
            "target companies."
        ) if eligible_n == 0 else f"{eligible_n} eligible jobs in the queue.",
    }


# --------------------------------------------------------------------------- #
# Application timeline & tracking dates
# --------------------------------------------------------------------------- #
def application_timeline(settings=None) -> list[dict]:
    """
    Every application with its key dates: when it was authorized, submitted,
    and last status change. Ordered by submitted_at desc.

    Each row:
      job_id, company, role_title, status, authorized_at, applied_at,
      last_updated, ats_url, interview_count
    """
    rows = query_all(
        """
        SELECT a.job_id, a.status, a.applied_at, a.last_updated, a.ats_url,
               a.contact_email, a.interview_count, a.notes,
               au.authorized_at,
               rj.company, rj.role_title
        FROM applications a
        LEFT JOIN authorized_jobs au ON au.job_id = a.job_id
        JOIN raw_jobs rj ON rj.id = a.job_id
        ORDER BY COALESCE(a.applied_at, a.last_updated, '') DESC
        """,
        settings=settings,
    )
    return [
        {
            "job_id": r["job_id"],
            "company": r["company"],
            "role_title": r["role_title"],
            "status": r["status"],
            "authorized_at": r["authorized_at"],
            "applied_at": r["applied_at"],
            "submitted_at": r["applied_at"],
            "last_updated": r["last_updated"],
            "ats_url": r["ats_url"],
            "contact_email": r["contact_email"],
            "interview_count": r["interview_count"],
            "notes": r["notes"],
        }
        for r in rows
    ]


def status_breakdown(settings=None) -> list[dict]:
    """Count of applications per status, ordered by count desc."""
    rows = query_all(
        "SELECT status, COUNT(*) AS n FROM applications GROUP BY status",
        settings=settings,
    )
    return [
        {"status": r["status"], "count": r["n"]}
        for r in rows
    ]


def company_application_summary(settings=None) -> list[dict]:
    """
    Per-company application summary: how many applied, how many interviews,
    the submit date range, and the latest status.
    """
    rows = query_all(
        """
        SELECT rj.company,
               COUNT(*) AS applied,
               SUM(CASE WHEN a.status IN ('interview','final','offer') THEN 1 ELSE 0 END) AS interviews,
               MIN(a.applied_at) AS first_submitted,
               MAX(a.applied_at) AS last_submitted,
               MAX(a.last_updated) AS last_activity
        FROM applications a
        JOIN raw_jobs rj ON rj.id = a.job_id
        GROUP BY rj.company
        ORDER BY applied DESC
        """,
        settings=settings,
    )
    return [
        {
            "company": r["company"],
            "applied": r["applied"],
            "interviews": r["interviews"],
            "first_submitted": r["first_submitted"],
            "last_submitted": r["last_submitted"],
            "last_activity": r["last_activity"],
        }
        for r in rows
    ]


def activity_by_date(settings=None, window_days: int = 30) -> list[dict]:
    """
    Applications submitted per day over the last `window_days`. Used for an
    activity heatmap / bar chart. Each row: {date, count}.
    """
    cutoff = _days_ago_iso(window_days)
    rows = query_all(
        """
        SELECT substr(a.applied_at, 1, 10) AS day, COUNT(*) AS count
        FROM applications a
        WHERE a.applied_at >= ?
        GROUP BY day
        ORDER BY day
        """,
        (cutoff,),
        settings=settings,
    )
    return [{"date": r["day"], "count": r["count"]} for r in rows]


def conversion_by_country(settings=None) -> list[dict]:
    """
    Interview conversion rate by target country, derived from outcomes joined
    to the JD distill (which carries country hints). Falls back to geo
    eligibility when no distill data exists.
    """
    rows = query_all(
        """
        SELECT a.job_id, a.status, rj.company,
               sj.geo_eligible,
               jd.actual_seniority
        FROM applications a
        JOIN raw_jobs rj ON rj.id = a.job_id
        LEFT JOIN scored_jobs sj ON sj.id = a.job_id
        LEFT JOIN jd_distill jd ON jd.job_id = a.job_id
        """,
        settings=settings,
    )
    by_country: dict[str, dict] = {}
    for r in rows:
        elig = r["geo_eligible"]
        key = "Eligible" if elig == 1 else ("Ineligible" if elig == 0 else "Unknown")
        bucket = by_country.setdefault(
            key, {"applied": 0, "interviews": 0}
        )
        bucket["applied"] += 1
        if r["status"] in ("interview", "final", "offer"):
            bucket["interviews"] += 1
    out = []
    for k, v in by_country.items():
        rate = (v["interviews"] / v["applied"] * 100) if v["applied"] else 0.0
        out.append({"country": k, **v, "rate": round(rate, 1)})
    return sorted(out, key=lambda x: -x["applied"])


def application_age_report(settings=None) -> list[dict]:
    """
    Applications with how long they've been in their current status (days),
    so you can see which ones are stale. Ordered by age desc.
    """
    rows = query_all(
        """
        SELECT a.job_id, a.status, a.applied_at, a.last_updated,
               rj.company, rj.role_title
        FROM applications a
        JOIN raw_jobs rj ON rj.id = a.job_id
        ORDER BY COALESCE(a.last_updated, a.applied_at) ASC
        """,
        settings=settings,
    )
    out = []
    for r in rows:
        ref = r["last_updated"] or r["applied_at"] or ""
        age = _days_between(ref) if ref else None
        out.append({
            "job_id": r["job_id"],
            "company": r["company"],
            "role_title": r["role_title"],
            "status": r["status"],
            "applied_at": r["applied_at"],
            "last_updated": r["last_updated"],
            "age_days": age,
        })
    return out


def _days_between(iso_str: str) -> Optional[int]:
    """Whole days between an ISO timestamp and now."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return max(0, delta.days)


# --------------------------------------------------------------------------- #
# Learning & Self-Improvement (Modules 8 + 14)
# --------------------------------------------------------------------------- #
def learning_result(settings=None) -> dict:
    """
    Run the self-tuning match engine and return a serializable snapshot.

    Wraps LearningEngine.learn() so the dashboard never imports the engine
    directly. Returns a plain dict of KPIs + weights + dimension rates.
    """
    from src.learning.match_engine import LearningEngine

    engine = LearningEngine(settings)
    r = engine.learn()

    return {
        "sample_count": r.sample_count,
        "historical_rate": round(r.historical_rate * 100, 1),
        "weights": r.weights,
        "bias": round(r.bias, 4),
        "calibration_mae": round(r.calibration_mae * 100, 1),
        "trusted": r.trusted,
        "maintenance_mode": r.maintenance_mode,
        "message": r.message,
        "dimension_rates": r.dimension_rates,
    }


def weekly_review(settings=None) -> dict:
    """Run the self-improvement weekly review and return a serializable dict."""
    from src.self_improve.loop import SelfImproveLoop

    loop = SelfImproveLoop(settings)
    r = loop.weekly_review()
    return {
        "period_days": r.period_days,
        "applications": r.applications,
        "interviews": r.interviews,
        "conversion_rate": round(r.conversion_rate * 100, 1),
        "top_companies": r.top_companies,
        "top_resume_patterns": r.top_resume_patterns,
        "summary": r.summary,
    }


def drift_report(settings=None) -> dict:
    """Run drift detection and return a serializable dict."""
    from src.self_improve.loop import SelfImproveLoop

    loop = SelfImproveLoop(settings)
    r = loop.detect_drift()
    return {
        "drifted": r.drifted,
        "current_rate": round(r.current_rate * 100, 1),
        "prior_rate": round(r.prior_rate * 100, 1),
        "drop_pct": round(r.drop_pct * 100, 1),
        "likely_causes": r.likely_causes,
        "recommendation": r.recommendation,
    }


def prompt_recommendations(settings=None) -> list[dict]:
    """Return flagged prompt recommendations as serializable dicts."""
    from src.self_improve.loop import SelfImproveLoop

    loop = SelfImproveLoop(settings)
    recs = loop.prompt_recommendations()
    return [
        {
            "task": r.task,
            "current_version": r.current_version,
            "current_outcome": round(r.current_outcome * 100, 1),
            "samples": r.samples,
            "flagged": r.flagged,
            "proposed_version": r.proposed_version,
            "proposed_outcome": round(r.proposed_outcome * 100, 1),
            "reason": r.reason,
        }
        for r in recs
    ]


def security_status(settings=None) -> dict:
    """Return encryption/backup/audit state for the Settings page."""
    from src import security

    return security.encrypt.status(settings)


def audit_summary(settings=None) -> dict:
    """Return audit event counts by type."""
    from src import security

    return security.audit.summarize(settings)
