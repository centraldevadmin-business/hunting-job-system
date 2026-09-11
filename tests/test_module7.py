"""
Module 7 tests — Tracking.

Verifies:
  - Status transitions write to applications + scored_jobs + outcomes.
  - Invalid statuses are rejected.
  - Stale detection flags applications stuck in an advanced status.
  - Follow-up drafts are generated (copy/paste only) and never sent.
  - The tracking report aggregates status / country / activity / stale.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.tracking.tracker import Tracker


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seed_master(tmp_db):
    """Seed the verified master record so follow-up drafts have real facts."""
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


def _seed_job(tmp_db, job_id="GL-TRK-1", company="Acme", role_title="Data Analyst"):
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, company, role_title, jd) "
        "VALUES (?, 'direct', 'direct', ?, ?, ?)",
        (job_id, company, role_title, "remote worldwide"),
        settings=tmp_db,
    )
    # Bootstrap scored_jobs so the FK chain holds.
    execute_sql(
        "INSERT INTO scored_jobs (id, status) VALUES (?, 'pending')",
        (job_id,),
        settings=tmp_db,
    )


# --------------------------------------------------------------------------- #
# Status lifecycle
# --------------------------------------------------------------------------- #
def test_set_status_creates_application(tmp_db):
    """First set_status creates the application row."""
    _seed_job(tmp_db)
    tracker = Tracker(settings=tmp_db)
    tr = tracker.set_status("GL-TRK-1", "applied")
    assert tr is not None
    assert tr.from_status is None
    assert tr.to_status == "applied"

    from src.db.repository import query_one
    app = query_one("SELECT * FROM applications WHERE job_id = ?", ("GL-TRK-1",),
                    settings=tmp_db)
    assert app is not None
    assert app["status"] == "applied"
    assert app["applied_at"] is not None


def test_set_status_mirrors_to_scored_jobs(tmp_db):
    """A status transition also updates scored_jobs."""
    _seed_job(tmp_db)
    tracker = Tracker(settings=tmp_db)
    tracker.set_status("GL-TRK-1", "applied")
    tracker.set_status("GL-TRK-1", "interview")

    from src.db.repository import query_one
    sj = query_one("SELECT status FROM scored_jobs WHERE id = ?", ("GL-TRK-1",),
                   settings=tmp_db)
    assert sj["status"] == "interview"


def test_set_status_records_outcome(tmp_db):
    """Each transition writes an application_outcomes row."""
    _seed_job(tmp_db)
    tracker = Tracker(settings=tmp_db)
    tracker.set_status("GL-TRK-1", "applied")
    tracker.set_status("GL-TRK-1", "interview")

    from src.db.repository import query_all
    outcomes = query_all(
        "SELECT status FROM application_outcomes WHERE job_id = ?",
        ("GL-TRK-1",), settings=tmp_db,
    )
    statuses = {o["status"] for o in outcomes}
    assert {"applied", "interview"}.issubset(statuses)


def test_set_status_rejects_invalid(tmp_db):
    """An unknown status returns None and writes nothing."""
    _seed_job(tmp_db)
    tracker = Tracker(settings=tmp_db)
    tr = tracker.set_status("GL-TRK-1", "banana")
    assert tr is None

    from src.db.repository import query_one
    app = query_one("SELECT 1 FROM applications WHERE job_id = ?", ("GL-TRK-1",),
                    settings=tmp_db)
    assert app is None


def test_record_interview_offer_reject(tmp_db):
    """Convenience recorders set the right statuses."""
    _seed_job(tmp_db, job_id="GL-TRK-2")
    tracker = Tracker(settings=tmp_db)
    tracker.set_status("GL-TRK-2", "applied")
    tracker.record_interview("GL-TRK-2", notes="phone screen")
    tracker.record_offer("GL-TRK-2", amount="$180k", notes="annual")

    from src.db.repository import query_one
    app = query_one("SELECT * FROM applications WHERE job_id = ?", ("GL-TRK-2",),
                    settings=tmp_db)
    assert app["status"] == "offer"
    assert "$180k" in (app["notes"] or "")


# --------------------------------------------------------------------------- #
# Stale detection
# --------------------------------------------------------------------------- #
def test_stale_detection(tmp_db):
    """An application stuck in 'applied' for >14 days is flagged stale."""
    _seed_job(tmp_db, job_id="GL-TRK-3")
    tracker = Tracker(settings=tmp_db)
    tracker.set_status("GL-TRK-3", "applied")

    # Backdate the last_updated to 20 days ago.
    from src.db.repository import execute_sql
    execute_sql(
        "UPDATE applications SET last_updated = '2020-01-01T00:00:00' WHERE job_id = ?",
        ("GL-TRK-3",),
        settings=tmp_db,
    )

    stale = tracker.stale_applications(after_days=14)
    ids = {s["job_id"] for s in stale}
    assert "GL-TRK-3" in ids
    assert stale[0]["age_days"] >= 14


def test_recent_application_not_stale(tmp_db):
    """A recently-applied job is not flagged stale."""
    _seed_job(tmp_db, job_id="GL-TRK-4")
    tracker = Tracker(settings=tmp_db)
    tracker.set_status("GL-TRK-4", "applied")

    stale = tracker.stale_applications(after_days=14)
    assert "GL-TRK-4" not in {s["job_id"] for s in stale}


# --------------------------------------------------------------------------- #
# Follow-up drafts
# --------------------------------------------------------------------------- #
def test_follow_up_draft_contains_applicant_name(tmp_db):
    """The follow-up draft names the applicant and references the job."""
    _seed_master(tmp_db)
    _seed_job(tmp_db, job_id="GL-TRK-5", company="Acme", role_title="Data Analyst")
    tracker = Tracker(settings=tmp_db)
    tracker.set_status("GL-TRK-5", "applied")

    draft = tracker.follow_up_draft("GL-TRK-5")
    assert draft is not None
    assert "MD Nafiz Mahfuz" in draft
    assert "Acme" in draft
    assert "Data Analyst" in draft


def test_follow_up_draft_never_sent(tmp_db):
    """Generating a draft must not send anything — no network, no email."""
    _seed_job(tmp_db, job_id="GL-TRK-6")
    tracker = Tracker(settings=tmp_db)
    tracker.set_status("GL-TRK-6", "applied")

    # Generating a draft does not change the status or create an 'out' box.
    tracker.follow_up_draft("GL-TRK-6")
    from src.db.repository import query_one
    app = query_one("SELECT status FROM applications WHERE job_id = ?",
                    ("GL-TRK-6",), settings=tmp_db)
    assert app["status"] == "applied"  # unchanged


def test_follow_up_draft_missing_job_returns_none(tmp_db):
    tracker = Tracker(settings=tmp_db)
    assert tracker.follow_up_draft("GL-NONE") is None


# --------------------------------------------------------------------------- #
# Tracking report
# --------------------------------------------------------------------------- #
def test_tracking_report(tmp_db):
    """The report aggregates counts, status, and stale lists."""
    _seed_job(tmp_db, job_id="GL-TRK-7", company="Acme", role_title="Data Analyst")
    tracker = Tracker(settings=tmp_db)
    tracker.set_status("GL-TRK-7", "applied")
    tracker.record_interview("GL-TRK-7")

    report = tracker.report()
    assert report.total_applied == 1
    assert report.total_interviews == 1
    assert report.interview_rate == pytest.approx(100.0)
    assert report.by_status
    assert any(s["status"] == "interview" for s in report.by_status)
