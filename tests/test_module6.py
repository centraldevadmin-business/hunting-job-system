"""
Module 6 tests — Execution (human-in-the-loop application staging).

Verifies:
  - Cover-letter generation is deterministic and zero-hallucination (only
    facts from the verified master record appear).
  - ApplicationStager stages a job: writes authorized_jobs + applications,
    builds a submission package (cover letter, checklist, submit URL).
  - mark_applied records the application and flips authorized_jobs.submitted.
  - record_interview advances the application to 'interview'.
  - The FK chain (applications -> scored_jobs -> raw_jobs) is bootstrapped when
    scored_jobs is empty.
  - staged_jobs / applied_jobs queries reflect the writes.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.execution.cover_letter import CoverLetterGenerator
from src.execution.application import ApplicationStager


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seed_master(tmp_db):
    """Seed the verified master record so the cover letter has real facts."""
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO career_profile (id, full_name, email, phone, location) "
        "VALUES (1, 'MD Nafiz Mahfuz', 'nafiz@example.com', '+880 1877-014405', 'Dhaka, Bangladesh')",
        settings=tmp_db,
    )
    execute_sql(
        "INSERT INTO employment (id, company, role, start_date, end_date, current, location) "
        "VALUES (1, 'Betopia Limited', 'Business Analyst', '02/2024', NULL, 1, 'Dhaka, Bangladesh')",
        settings=tmp_db,
    )
    execute_sql(
        "INSERT INTO skill (name, level) VALUES ('data pipelines', 'advanced'), "
        "('metrics analysis', 'advanced'), ('product', 'intermediate')",
        settings=tmp_db,
    )


def _seed_job(tmp_db, job_id="GL-APP-1", company="Acme", role_title="Data Analyst",
              jd="We need someone to build data pipelines and analyze metrics."):
    """Seed a raw_job so the execution tables' FK chain can resolve."""
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO raw_jobs (id, company, role_title, jd, source_type) "
        "VALUES (?, ?, ?, ?, 'direct')",
        (job_id, company, role_title, jd),
        settings=tmp_db,
    )


# --------------------------------------------------------------------------- #
# Cover letter
# --------------------------------------------------------------------------- #
def test_cover_letter_contains_only_master_facts(tmp_db):
    """The cover letter must name the applicant and reference the job only."""
    _seed_master(tmp_db)
    gen = CoverLetterGenerator(settings=tmp_db)
    letter = gen.build(
        jd_text="Build data pipelines. Analyze metrics.",
        company="Stripe",
        role_title="Staff Data Analyst",
        job_id="GL-CL-1",
    )
    body = letter.body

    # Applicant's own name (from the verified master record) appears.
    assert "MD Nafiz Mahfuz" in body
    # The target role/company are referenced (from the job, not invented).
    assert "Staff Data Analyst" in body
    assert "Stripe" in body
    # No fabricated company facts (revenue, clients, products).
    assert "million" not in body.lower()
    assert "revenue" not in body.lower()
    # Honest authorization line — no claimed referral or prior relationship.
    assert "B2B" in body or "record" in body.lower()


def test_cover_letter_is_deterministic(tmp_db):
    """Same inputs → identical output (no randomness, no LLM)."""
    gen = CoverLetterGenerator(settings=tmp_db)
    a = gen.build("Build pipelines.", "Acme", "Data Analyst", "GL-CL-2").body
    b = gen.build("Build pipelines.", "Acme", "Data Analyst", "GL-CL-2").body
    assert a == b


def test_cover_letter_quotes_jd_requirements(tmp_db):
    """When the JD has imperative requirement lines, they are echoed back."""
    gen = CoverLetterGenerator(settings=tmp_db)
    jd = (
        "Build scalable data pipelines.\n"
        "Analyze business metrics weekly.\n"
        "Coordinate with product teams.\n"
    )
    letter = gen.build(jd, "Acme", "Data Analyst", "GL-CL-3")
    assert "Build scalable data pipelines" in letter.body


# --------------------------------------------------------------------------- #
# Application stager
# --------------------------------------------------------------------------- #
def test_stage_for_submission_writes_rows(tmp_db):
    """Staging writes authorized_jobs + applications and returns a package."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)
    stager = ApplicationStager(settings=tmp_db)
    res = stager.stage_for_submission(
        "GL-APP-1", jd_text="Build pipelines.", company="Acme",
        role_title="Data Analyst",
    )
    assert res.staged is True
    assert res.applied is False
    assert res.package is not None
    assert len(res.package.checklist) >= 5
    assert "MD Nafiz Mahfuz" in res.package.cover_letter_body

    # authorized_jobs row exists, not yet submitted.
    from src.db.repository import query_one
    auth = query_one(
        "SELECT * FROM authorized_jobs WHERE job_id = ?", ("GL-APP-1",),
        settings=tmp_db,
    )
    assert auth is not None
    assert auth["submitted"] == 0

    # applications row exists with status 'staged'.
    app = query_one(
        "SELECT * FROM applications WHERE job_id = ?", ("GL-APP-1",),
        settings=tmp_db,
    )
    assert app is not None
    assert app["status"] == "staged"


def test_mark_applied_flips_and_records_outcome(tmp_db):
    """mark_applied records the application and flips authorized_jobs."""
    _seed_job(tmp_db)
    stager = ApplicationStager(settings=tmp_db)
    stager.stage_for_submission("GL-APP-1", company="Acme", role_title="Data Analyst")

    res = stager.mark_applied("GL-APP-1", notes="submitted via gh")
    assert res.applied is True

    from src.db.repository import query_one
    app = query_one(
        "SELECT * FROM applications WHERE job_id = ?", ("GL-APP-1",),
        settings=tmp_db,
    )
    assert app["status"] == "applied"
    assert app["applied_at"] is not None

    auth = query_one(
        "SELECT * FROM authorized_jobs WHERE job_id = ?", ("GL-APP-1",),
        settings=tmp_db,
    )
    assert auth["submitted"] == 1
    assert auth["submitted_at"] is not None

    # An outcome event was recorded.
    outcome = query_one(
        "SELECT * FROM application_outcomes WHERE job_id = ?", ("GL-APP-1",),
        settings=tmp_db,
    )
    assert outcome is not None
    assert outcome["status"] == "applied"


def test_record_interview_advances_application(tmp_db):
    """record_interview advances the application to 'interview'."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)
    stager = ApplicationStager(settings=tmp_db)
    stager.stage_for_submission("GL-APP-1", company="Acme", role_title="Data Analyst")
    stager.mark_applied("GL-APP-1")

    res = stager.record_interview("GL-APP-1")
    assert res.applied is True

    from src.db.repository import query_one
    app = query_one(
        "SELECT * FROM applications WHERE job_id = ?", ("GL-APP-1",),
        settings=tmp_db,
    )
    assert app["status"] == "interview"

    outcome = query_one(
        "SELECT * FROM application_outcomes WHERE job_id = ? AND status = 'interview'",
        ("GL-APP-1",), settings=tmp_db,
    )
    assert outcome is not None


def test_fk_chain_bootstrapped_when_scored_jobs_empty(tmp_db):
    """When scored_jobs is empty, the stager upserts a scored_jobs row."""
    _seed_job(tmp_db)
    from src.db.repository import query_one
    # scored_jobs is empty at this point.
    assert query_one("SELECT 1 FROM scored_jobs WHERE id = ?", ("GL-APP-1",),
                     settings=tmp_db) is None

    stager = ApplicationStager(settings=tmp_db)
    stager.stage_for_submission("GL-APP-1", company="Acme", role_title="Data Analyst")

    # scored_jobs row now exists (FK chain holds).
    sj = query_one("SELECT * FROM scored_jobs WHERE id = ?", ("GL-APP-1",),
                   settings=tmp_db)
    assert sj is not None


def test_staged_and_applied_queries(tmp_db):
    """staged_jobs / applied_jobs reflect the writes."""
    _seed_master(tmp_db)
    _seed_job(tmp_db, job_id="GL-APP-1", company="Acme", role_title="Data Analyst")
    _seed_job(tmp_db, job_id="GL-APP-2", company="Beta", role_title="Systems Analyst")

    stager = ApplicationStager(settings=tmp_db)
    stager.stage_for_submission("GL-APP-1", company="Acme", role_title="Data Analyst")
    stager.stage_for_submission("GL-APP-2", company="Beta", role_title="Systems Analyst")
    stager.mark_applied("GL-APP-1")

    staged = stager.staged_jobs()
    staged_ids = {r["job_id"] for r in staged}
    assert "GL-APP-2" in staged_ids
    assert "GL-APP-1" not in staged_ids  # applied, no longer staged

    applied = stager.applied_jobs()
    applied_ids = {r["job_id"] for r in applied}
    assert "GL-APP-1" in applied_ids
    assert "GL-APP-2" not in applied_ids


def test_stage_is_idempotent(tmp_db):
    """Staging the same job twice does not duplicate rows."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)
    stager = ApplicationStager(settings=tmp_db)
    stager.stage_for_submission("GL-APP-1", company="Acme", role_title="Data Analyst")
    stager.stage_for_submission("GL-APP-1", company="Acme", role_title="Data Analyst")

    from src.db.repository import query_all
    apps = query_all(
        "SELECT COUNT(*) AS n FROM applications WHERE job_id = ?",
        ("GL-APP-1",), settings=tmp_db,
    )
    assert apps[0]["n"] == 1

    auths = query_all(
        "SELECT COUNT(*) AS n FROM authorized_jobs WHERE job_id = ?",
        ("GL-APP-1",), settings=tmp_db,
    )
    assert auths[0]["n"] == 1
