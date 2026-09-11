"""
Application stager — Module 6 (Execution).

This is the "apply" half of the engine. Because the system has NO email access
and NO ability to fill external submit forms, Execution is designed as a
**human-in-the-loop application staging** flow:

  1. The system assembles a complete submission package for a job:
       - tailored resume (already built in Module 4, or rebuilt here)
       - tailored cover letter (this module)
       - the company's own submit URL (from the JD / canonical ATS URL)
       - a one-page submission checklist
  2. The human reviews the package, clicks the submit URL, and submits on the
     company's OWN page.
  3. The human clicks "I applied" on the dashboard, which records the
     application row and stamps the applied_at date.

No email is ever sent. No form is ever filled by software. The human is always
the one who clicks submit.

State written to the DB (schema already exists from earlier modules):
  - applications(job_id, contact_email, ats_url, status, status_confidence,
    applied_at, interview_count, notes, last_updated)
  - authorized_jobs(job_id, authorized_at, submitted, submitted_at, submit_url)
  - application_outcomes(job_id, status, stage_at, notes, created_at, updated_at)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.db.repository import (
    get_connection,
    now_iso,
    query_one,
    query_all,
    execute_sql,
)
from src.execution.cover_letter import CoverLetterGenerator


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class SubmissionPackage:
    """Everything the human needs to submit one application."""
    job_id: str
    company: str
    role_title: str
    submit_url: Optional[str]
    resume_pdf: Optional[str]
    cover_letter_body: str
    checklist: list[str]
    notes: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class StageResult:
    """Outcome of staging / recording an application."""
    job_id: str
    staged: bool
    applied: bool
    submit_url: Optional[str]
    package: Optional[SubmissionPackage] = None
    message: str = ""


# --------------------------------------------------------------------------- #
# Application stager
# --------------------------------------------------------------------------- #
class ApplicationStager:
    """
    Assemble submission packages and record human-executed applications.

    All writes go to the local SQLite DB. No network calls, no email, no
    external form submission.
    """

    def __init__(self, settings=None):
        self.settings = settings
        self.cover_gen = CoverLetterGenerator(settings)

    # ------------------------------------------------------------------ #
    # Package assembly
    # ------------------------------------------------------------------ #
    def build_package(self, job_id: str, jd_text: str = "",
                      company: str = "", role_title: str = "") -> SubmissionPackage:
        """
        Assemble the full submission package for one job.

        The package is ready for the human to review and submit. It never
        sends anything.
        """
        # Resolve job details from the DB if not passed in.
        raw = query_one(
            "SELECT * FROM raw_jobs WHERE id = ?", (job_id,), settings=self.settings
        )
        if raw:
            company = company or (raw.get("company") or "")
            role_title = role_title or (raw.get("role_title") or "")
            jd_text = jd_text or (raw.get("jd") or "")

        # Cover letter (deterministic, zero-hallucination).
        cover = self.cover_gen.build(
            jd_text=jd_text, company=company, role_title=role_title, job_id=job_id
        )

        # Resume PDF — prefer the validated one from Module 4, else rebuild.
        resume_pdf = self._resolve_resume_pdf(job_id, jd_text)

        # Submit URL — the company's own ATS / careers page.
        submit_url = self._submit_url(job_id, raw)

        warnings = []
        if not resume_pdf:
            warnings.append(
                "No validated resume PDF — build one first (Module 4) before submitting."
            )
        if not submit_url:
            warnings.append(
                "No submit URL found — open the company careers page and paste the link."
            )

        checklist = self._checklist(company, role_title, resume_pdf is not None)

        return SubmissionPackage(
            job_id=job_id,
            company=company,
            role_title=role_title,
            submit_url=submit_url,
            resume_pdf=resume_pdf,
            cover_letter_body=cover.body,
            checklist=checklist,
            notes="; ".join(cover.warnings) if cover.warnings else "",
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # Scored-job bootstrap
    # ------------------------------------------------------------------ #
    def _ensure_scored_job(self, job_id: str) -> None:
        """
        Ensure the job exists in scored_jobs before we write execution rows.

        The execution tables (applications, authorized_jobs,
        application_outcomes) carry a foreign key onto scored_jobs, so a job
        must be scored before it can be applied to. If Module 2 has not run on
        this DB, we upsert a minimal scored_jobs row from raw_jobs so the
        FK chain holds. This never fabricates a match score — it defaults to
        NULL (unreviewed), which the dashboard surfaces as "unproven".
        """
        with get_connection(self.settings) as conn:
            exists = conn.execute(
                "SELECT 1 FROM scored_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if exists:
                return
            raw = conn.execute(
                "SELECT id, company, role_title, jd, salary, source_type, "
                "canonical_ats_url, fetched_at FROM raw_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not raw:
                return
            conn.execute(
                "INSERT INTO scored_jobs "
                "(id, geo_eligible, geo_confidence, fraud_score, fraud_flags, "
                "match_score, status, reviewed_at) "
                "VALUES (?, NULL, NULL, NULL, NULL, NULL, 'pending', ?)",
                (job_id, now_iso()),
            )
            conn.commit()

    # ------------------------------------------------------------------ #
    # Human-executed application recording
    # ------------------------------------------------------------------ #
    def stage_for_submission(self, job_id: str, jd_text: str = "",
                             company: str = "", role_title: str = "",
                             submit_url: Optional[str] = None) -> StageResult:
        """
        Stage a job for submission: record human authorization and the
        submission package in the DB. Does NOT apply — the human submits.

        Returns a StageResult. The human then calls mark_applied() after they
        have submitted on the company's own page.
        """
        package = self.build_package(
            job_id, jd_text=jd_text, company=company, role_title=role_title
        )
        if submit_url:
            package.submit_url = submit_url

        # Ensure the FK chain holds before writing execution rows.
        self._ensure_scored_job(job_id)

        with get_connection(self.settings) as conn:
            # Record authorization to apply.
            existing = conn.execute(
                "SELECT 1 FROM authorized_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO authorized_jobs "
                    "(job_id, authorized_at, submitted, submit_url) "
                    "VALUES (?, ?, 0, ?)",
                    (job_id, now_iso(), package.submit_url),
                )
            else:
                conn.execute(
                    "UPDATE authorized_jobs SET submit_url = ? WHERE job_id = ?",
                    (package.submit_url, job_id),
                )

            # Create the application row (idempotent).
            app = conn.execute(
                "SELECT 1 FROM applications WHERE job_id = ?", (job_id,)
            ).fetchone()
            if not app:
                conn.execute(
                    "INSERT INTO applications "
                    "(job_id, contact_email, ats_url, status, status_confidence, "
                    "applied_at, interview_count, notes, last_updated) "
                    "VALUES (?, ?, ?, 'staged', 0.0, NULL, 0, ?, ?)",
                    (
                        job_id,
                        self._contact_email(),
                        package.submit_url,
                        package.notes,
                        now_iso(),
                    ),
                )
            else:
                conn.execute(
                    "UPDATE applications SET ats_url = ?, last_updated = ? "
                    "WHERE job_id = ?",
                    (package.submit_url, now_iso(), job_id),
                )
            conn.commit()

        return StageResult(
            job_id=job_id,
            staged=True,
            applied=False,
            submit_url=package.submit_url,
            package=package,
            message="Staged for submission. Review the package, then submit on the company's page.",
        )

    def mark_applied(self, job_id: str, notes: str = "",
                     contact_email: Optional[str] = None) -> StageResult:
        """
        Record that the human has submitted the application on the company's
        own page. This is the one-click status update — no email is sent.
        """
        ts = now_iso()
        self._ensure_scored_job(job_id)
        with get_connection(self.settings) as conn:
            app = conn.execute(
                "SELECT 1 FROM applications WHERE job_id = ?", (job_id,)
            ).fetchone()
            if not app:
                conn.execute(
                    "INSERT INTO applications "
                    "(job_id, contact_email, ats_url, status, status_confidence, "
                    "applied_at, interview_count, notes, last_updated) "
                    "VALUES (?, ?, ?, 'applied', 1.0, ?, 0, ?, ?)",
                    (
                        job_id,
                        contact_email or self._contact_email(),
                        None,
                        ts,
                        notes,
                        ts,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE applications SET status = 'applied', applied_at = ?, "
                    "notes = ?, last_updated = ? WHERE job_id = ?",
                    (ts, notes, ts, job_id),
                )

            # Mark authorized_jobs as submitted.
            conn.execute(
                "UPDATE authorized_jobs SET submitted = 1, submitted_at = ? "
                "WHERE job_id = ?",
                (ts, job_id),
            )

            # Record the outcome event.
            conn.execute(
                "INSERT INTO application_outcomes "
                "(job_id, status, stage_at, notes, created_at, updated_at) "
                "VALUES (?, 'applied', 'applied', ?, ?, ?)",
                (job_id, notes, ts, ts),
            )
            conn.commit()

        return StageResult(
            job_id=job_id,
            staged=True,
            applied=True,
            submit_url=None,
            message="Application recorded as submitted.",
        )

    def record_interview(self, job_id: str, notes: str = "") -> StageResult:
        """Record that the job advanced to an interview."""
        ts = now_iso()
        self._ensure_scored_job(job_id)
        with get_connection(self.settings) as conn:
            conn.execute(
                "UPDATE applications SET status = 'interview', "
                "last_updated = ? WHERE job_id = ?",
                (ts, job_id),
            )
            conn.execute(
                "UPDATE scored_jobs SET status = 'interview', reviewed_at = ? "
                "WHERE id = ?",
                (ts, job_id),
            )
            conn.execute(
                "INSERT INTO application_outcomes "
                "(job_id, status, stage_at, notes, created_at, updated_at) "
                "VALUES (?, 'interview', 'interview', ?, ?, ?)",
                (job_id, notes, ts, ts),
            )
            conn.commit()
        return StageResult(
            job_id=job_id, staged=True, applied=True, submit_url=None,
            message="Interview recorded.",
        )

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def staged_jobs(self) -> list[dict]:
        """All jobs staged for submission (authorized, not yet applied)."""
        rows = query_all(
            """
            SELECT aj.job_id, aj.authorized_at, aj.submit_url,
                   rj.company, rj.role_title, rj.fetched_at
            FROM authorized_jobs aj
            JOIN raw_jobs rj ON rj.id = aj.job_id
            WHERE aj.submitted = 0
            ORDER BY aj.authorized_at ASC
            """,
            settings=self.settings,
        )
        return [dict(r) for r in rows]

    def applied_jobs(self) -> list[dict]:
        """All jobs the human has applied to, with status."""
        rows = query_all(
            """
            SELECT a.job_id, a.status, a.applied_at, a.last_updated,
                   a.interview_count, a.notes, rj.company, rj.role_title
            FROM applications a
            JOIN raw_jobs rj ON rj.id = a.job_id
            WHERE a.status = 'applied'
            ORDER BY a.applied_at DESC
            """,
            settings=self.settings,
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _resolve_resume_pdf(self, job_id: str, jd_text: str = "") -> Optional[str]:
        """
        Return the path to a validated resume PDF for the job.

        Prefers the stored Module 4 resume. If none exists, rebuilds one from
        the master record (deterministic, no API key needed for the fallback
        generator path).
        """
        resume = query_one(
            "SELECT * FROM resumes WHERE job_id = ?", (job_id,), settings=self.settings
        )
        if resume and resume.get("pdf_path") and resume.get("validated"):
            return resume["pdf_path"]

        # Try to rebuild from the stored validated variant text.
        variant = query_one(
            "SELECT * FROM resume_variants WHERE job_id = ? AND validated = 1 "
            "ORDER BY id LIMIT 1",
            (job_id,),
            settings=self.settings,
        )
        if variant and variant.get("resume_text"):
            try:
                from src.resume.orchestrator import ResumeOrchestrator
                orch = ResumeOrchestrator(self.settings)
                result = orch.build_for_job(
                    jd_text or "", job_id=job_id, variant_name="skills-first"
                )
                if result.pdf_path:
                    return result.pdf_path
            except Exception:
                pass

        return None

    def _submit_url(self, job_id: str, raw: Optional[dict]) -> Optional[str]:
        """Best-effort submit URL from the raw job's canonical ATS URL."""
        if raw:
            return raw.get("canonical_ats_url") or raw.get("url")
        row = query_one(
            "SELECT canonical_ats_url, url FROM raw_jobs WHERE id = ?",
            (job_id,),
            settings=self.settings,
        )
        if row:
            return row.get("canonical_ats_url") or row.get("url")
        return None

    def _contact_email(self) -> str:
        profile = self.cover_gen.master.profile
        return profile.get("email", "")

    def _checklist(self, company: str, role_title: str, has_resume: bool) -> list[str]:
        """One-page submission checklist for the human."""
        items = []
        if has_resume:
            items.append("Review the tailored resume PDF.")
        else:
            items.append("Build a validated resume first (Module 4).")
        items.append("Read the cover letter and personalize the opening if you want.")
        items.append("Open the company's careers / ATS page.")
        items.append("Fill in the company's own application form.")
        items.append("Attach the tailored resume PDF.")
        items.append("Submit on the company's OWN page.")
        items.append("Come back and click 'I applied' on the dashboard.")
        return items
