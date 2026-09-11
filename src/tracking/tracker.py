"""
Tracking module — Module 7.

Human-in-the-loop application tracking. The system records status transitions,
flags stale applications, and DRAFTS follow-up messages for the human to copy
and send. It NEVER sends email or touches any inbox.

Everything is derived from the local SQLite DB (applications, application_outcomes,
scored_jobs, raw_jobs). No network calls, no email, no OAuth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.db.repository import (
    get_connection,
    now_iso,
    query_one,
    query_all,
    execute_sql,
    log_audit,
)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class StatusTransition:
    job_id: str
    from_status: Optional[str]
    to_status: str
    at: str
    by: str = "human"


@dataclass
class TrackingReport:
    """At-a-glance tracking summary."""
    total_applied: int = 0
    total_interviews: int = 0
    total_offers: int = 0
    interview_rate: float = 0.0
    stale: list[dict] = field(default_factory=list)
    by_status: list[dict] = field(default_factory=list)
    by_country: list[dict] = field(default_factory=list)
    activity: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Tracker
# --------------------------------------------------------------------------- #
class Tracker:
    """
    Track application status, detect stale applications, and draft follow-ups.

    All writes go to the local SQLite DB. No email is ever sent.
    """

    # Valid status transitions (a directed graph of allowed moves).
    STATUS_FLOW = [
        "staged", "applied", "screen", "interview", "final",
        "offer", "reject", "silent",
    ]

    ADVANCED = {"screen", "interview", "final", "offer"}

    def __init__(self, settings=None):
        self.settings = settings

    # ------------------------------------------------------------------ #
    # Status lifecycle
    # ------------------------------------------------------------------ #
    def set_status(self, job_id: str, new_status: str, by: str = "human",
                   notes: str = "") -> Optional[StatusTransition]:
        """
        Record a status transition for a job. Writes to applications,
        scored_jobs, and an audit trail. Returns the transition, or None if
        the status is invalid.
        """
        if new_status not in self.STATUS_FLOW:
            return None

        existing = query_one(
            "SELECT status FROM applications WHERE job_id = ?", (job_id,),
            settings=self.settings,
        )
        old_status = existing["status"] if existing else None

        ts = now_iso()
        with get_connection(self.settings) as conn:
            app = conn.execute(
                "SELECT 1 FROM applications WHERE job_id = ?", (job_id,)
            ).fetchone()
            if app:
                conn.execute(
                    "UPDATE applications SET status = ?, last_updated = ?, notes = COALESCE(notes, '') || ? "
                    "WHERE job_id = ?",
                    (new_status, ts, (f" | {notes}" if notes else ""), job_id),
                )
            else:
                # First time tracking this job — create the row.
                conn.execute(
                    "INSERT INTO applications "
                    "(job_id, contact_email, ats_url, status, status_confidence, "
                    "applied_at, interview_count, notes, last_updated) "
                    "VALUES (?, NULL, NULL, ?, 1.0, ?, 0, ?, ?)",
                    (job_id, new_status, ts, notes, ts),
                )

            # Mirror to scored_jobs so the Hunt queue reflects it.
            conn.execute(
                "UPDATE scored_jobs SET status = ?, reviewed_at = ? WHERE id = ?",
                (new_status, ts, job_id),
            )

            # Record the outcome event.
            conn.execute(
                "INSERT INTO application_outcomes "
                "(job_id, status, stage_at, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, new_status, new_status, notes, ts, ts),
            )
            conn.commit()

        transition = StatusTransition(
            job_id=job_id, from_status=old_status, to_status=new_status,
            at=ts, by=by,
        )
        log_audit("status_transition", f"{job_id}: {old_status} -> {new_status}",
                  settings=self.settings)
        return transition

    def record_interview(self, job_id: str, interview_date: Optional[str] = None,
                         notes: str = "") -> Optional[StatusTransition]:
        """Record that a job advanced to an interview."""
        note = notes or interview_date or ""
        return self.set_status(job_id, "interview", notes=note)

    def record_offer(self, job_id: str, amount: Optional[str] = None,
                     notes: str = "") -> Optional[StatusTransition]:
        """Record an offer."""
        note = notes
        if amount:
            note = (f"{note} | " if note else "") + f"Offer: {amount}"
        return self.set_status(job_id, "offer", notes=note)

    def record_reject(self, job_id: str, notes: str = "") -> Optional[StatusTransition]:
        """Record a rejection."""
        return self.set_status(job_id, "reject", notes=notes)

    def record_silent(self, job_id: str, notes: str = "") -> Optional[StatusTransition]:
        """Mark a application as gone silent (no response after follow-up)."""
        return self.set_status(job_id, "silent", notes=notes)

    # ------------------------------------------------------------------ #
    # Stale detection
    # ------------------------------------------------------------------ #
    def stale_applications(self, after_days: int = 14) -> list[dict]:
        """
        Applications stuck in an advanced (non-terminal) status longer than
        `after_days` → candidates for a follow-up.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=after_days)).isoformat()
        rows = query_all(
            """
            SELECT a.job_id, a.status, a.last_updated, a.applied_at,
                   rj.company, rj.role_title
            FROM applications a
            JOIN raw_jobs rj ON rj.id = a.job_id
            WHERE a.status IN ('applied','screen','interview','final')
              AND COALESCE(a.last_updated, a.applied_at) < ?
            ORDER BY COALESCE(a.last_updated, a.applied_at) ASC
            """,
            (cutoff,),
            settings=self.settings,
        )
        out = []
        for r in rows:
            ref = r["last_updated"] or r["applied_at"] or ""
            out.append({
                "job_id": r["job_id"],
                "company": r["company"],
                "role_title": r["role_title"],
                "status": r["status"],
                "last_updated": r["last_updated"],
                "age_days": self._days_between(ref),
            })
        return out

    # ------------------------------------------------------------------ #
    # Follow-up drafts (NEVER sent — copy/paste only)
    # ------------------------------------------------------------------ #
    def follow_up_draft(self, job_id: str) -> Optional[str]:
        """
        Generate a polite, professional follow-up message draft for a stale
        application. The human copies and sends it — this function never
        sends anything.

        Zero-hallucination: only the applicant's own name (from the master
        record) and the job/company (from the DB) appear. No fabricated facts.
        """
        row = query_one(
            """
            SELECT a.status, a.applied_at, rj.company, rj.role_title
            FROM applications a
            JOIN raw_jobs rj ON rj.id = a.job_id
            WHERE a.job_id = ?
            """,
            (job_id,),
            settings=self.settings,
        )
        if not row:
            return None

        from src.resume.master import load_master_record
        name = load_master_record(self.settings).profile.get("full_name", "there")
        company = row.get("company") or "your team"
        role = row.get("role_title") or "the role"
        status = row.get("status") or "my application"

        return (
            f"Subject: Following up on my application for {role}\n\n"
            f"Dear Hiring Manager,\n\n"
            f"I hope this message is finding you well. I applied a while ago for "
            f"the {role} position at {company} and wanted to reconfirm my "
            f"interest. The work your team is doing strongly aligns with where "
            f"I want to take my career, and I remain very enthusiastic about "
            f"the opportunity.\n\n"
            f"I understand hiring timelines can shift, so there's no pressure — "
            f"but if there's any additional information I can provide, or if "
            f"the position is still open, I'd love to hear from you.\n\n"
            f"Thank you for your time and consideration.\n\n"
            f"Best regards,\n{name}"
        )

    def all_follow_ups(self, after_days: int = 14) -> list[dict]:
        """Stale applications with a generated follow-up draft."""
        drafts = []
        for stale in self.stale_applications(after_days=after_days):
            drafts.append({
                **stale,
                "draft": self.follow_up_draft(stale["job_id"]),
            })
        return drafts

    # ------------------------------------------------------------------ #
    # Tracking report
    # ------------------------------------------------------------------ #
    def report(self, after_days: int = 14) -> TrackingReport:
        """Assemble the full tracking report."""
        from src.dashboard import data_access as da

        report = TrackingReport()
        apps = query_all(
            "SELECT status, interview_count FROM applications",
            settings=self.settings,
        )
        report.total_applied = len(apps)
        report.total_interviews = sum(
            1 for a in apps
            if a["status"] in ("interview", "final", "offer")
        )
        report.total_offers = sum(1 for a in apps if a["status"] == "offer")
        report.interview_rate = (
            (report.total_interviews / report.total_applied * 100)
            if report.total_applied else 0.0
        )
        report.by_status = da.status_breakdown(settings=self.settings)
        report.by_country = da.conversion_by_country(settings=self.settings)
        report.activity = da.activity_by_date(settings=self.settings, window_days=30)
        report.stale = self.stale_applications(after_days=after_days)
        return report

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _days_between(self, iso_str: str) -> Optional[int]:
        from datetime import datetime, timezone, timedelta
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0, delta.days)
