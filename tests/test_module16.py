"""
Module 16 — Dashboard Learning page (data-access wiring).

Verifies the data_access helpers that wire the self-tuning match engine
(Module 8) and the self-improvement loop (Module 14) into the dashboard.
These are pure data-access functions — no UI, no LLM, no network.

All tests run against an isolated temp SQLite DB seeded with synthetic data.
Uses the `tmp_db` fixture from conftest.py.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _seed_job(db, job_id):
    """Seed a raw_job + scored_job so outcomes can reference them."""
    from src.db.repository import execute_sql, now_iso

    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, url, company, role_title, "
        "jd, salary, location, posted_at, canonical_ats_url, fetched_at) "
        "VALUES (?, 'direct', 'direct', ?, ?, ?, ?, '', 'remote worldwide', "
        "'2026-01-01', ?, '2026-01-01')",
        (job_id, f"https://c.com/{job_id}", "Betopia", "Business Analyst",
         "python sql power bi", f"https://c.com/{job_id}/apply"),
        settings=db,
    )
    execute_sql(
        "INSERT INTO scored_jobs (id, geo_eligible, geo_confidence, fraud_score, "
        "match_score, status) VALUES (?, 1, 1.0, 0, 80.0, 'new')",
        (job_id,), settings=db,
    )


def _seed_outcome(db, job_id, got_interview, rating=4):
    """Seed an application + feedback_features for a job.

    The learning engine (Module 8) reads from the `applications` table; the
    self-improvement loop (Module 14) reads from `application_outcomes` +
    `feedback_features`. We seed both so both engines have data.

    The `got_interview` flag lives in `feedback_features`, not
    `application_outcomes` (per the schema).
    """
    from src.db import repository
    from src.db.repository import execute_sql, now_iso

    ts = (datetime.fromisoformat(now_iso()) - timedelta(days=1))
    status = "interview" if got_interview else "applied"

    # applications table (read by the self-tuning match engine)
    execute_sql(
        "INSERT INTO applications (job_id, status, interview_count, applied_at) "
        "VALUES (?, ?, ?, ?)",
        (job_id, status, 1 if got_interview else 0, ts),
        settings=db,
    )

    # application_outcomes + feedback_features (read by the self-improvement loop)
    execute_sql(
        "INSERT INTO application_outcomes (job_id, status, stage_at, notes, "
        "created_at, updated_at) VALUES (?, ?, ?, '', ?, ?)",
        (job_id, status, status, ts, ts),
        settings=db,
    )
    outcome_id = repository.query_one(
        "SELECT id FROM application_outcomes WHERE job_id = ?",
        (job_id,), settings=db)["id"]
    execute_sql(
        "INSERT INTO feedback_features (outcome_id, skill_match, domain_match, "
        "seniority_match, country_match, comp_match, resume_rating, fraud_score, "
        "source_type, applied_at, got_interview) "
        "VALUES (?, 0.9, 0.8, 0.7, 0.9, 0.6, ?, 90, 'direct', ?, ?)",
        (outcome_id, rating, ts, 1 if got_interview else 0), settings=db,
    )


# --------------------------------------------------------------------------- #
# Learning engine (Module 8)
# --------------------------------------------------------------------------- #
def test_learning_result_empty(tmp_db):
    """With no outcomes, learning returns priors and an unproven status."""
    from src.dashboard import data_access as da

    lr = da.learning_result(tmp_db)
    assert lr["sample_count"] == 0
    assert lr["trusted"] is False
    # With no data, historical_rate reverts to the conservative prior (0.15).
    assert lr["historical_rate"] == 15.0
    # Weights fall back to conservative priors.
    assert lr["weights"]["match"] > 0


def test_learning_result_with_outcomes(tmp_db):
    """With outcomes, learning returns a real sample count and rate."""
    from src.dashboard import data_access as da

    _seed_job(tmp_db, "JOB-1")
    _seed_job(tmp_db, "JOB-2")
    _seed_outcome(tmp_db, "JOB-1", got_interview=False)
    _seed_outcome(tmp_db, "JOB-2", got_interview=True)

    lr = da.learning_result(tmp_db)
    assert lr["sample_count"] == 2
    # Bayesian-smoothed: (1 + 0.15*10) / (2 + 10) = 20.83%
    assert lr["historical_rate"] == pytest.approx(20.83, abs=0.1)


# --------------------------------------------------------------------------- #
# Self-improvement loop (Module 14)
# --------------------------------------------------------------------------- #
def test_weekly_review_empty(tmp_db):
    """With no data, weekly review returns zeros."""
    from src.dashboard import data_access as da

    wr = da.weekly_review(tmp_db)
    assert wr["applications"] == 0
    assert wr["interviews"] == 0
    assert wr["conversion_rate"] == 0.0


def test_weekly_review_counts(tmp_db):
    """Weekly review counts applications and interviews from outcomes."""
    from src.dashboard import data_access as da

    _seed_job(tmp_db, "JOB-1")
    _seed_job(tmp_db, "JOB-2")
    _seed_outcome(tmp_db, "JOB-1", got_interview=True)
    _seed_outcome(tmp_db, "JOB-2", got_interview=False)

    wr = da.weekly_review(tmp_db)
    assert wr["applications"] == 2
    assert wr["interviews"] == 1


def test_drift_report_no_drift(tmp_db):
    """With no data, drift report shows no drift."""
    from src.dashboard import data_access as da

    dr = da.drift_report(tmp_db)
    assert dr["drifted"] is False


def test_prompt_recommendations_empty(tmp_db):
    """With no prompts, no recommendations."""
    from src.dashboard import data_access as da

    pr = da.prompt_recommendations(tmp_db)
    assert pr == []


# --------------------------------------------------------------------------- #
# Security status (Module 15)
# --------------------------------------------------------------------------- #
def test_security_status(tmp_db):
    """Security status returns a dict with encryption state."""
    from src.dashboard import data_access as da

    st = da.security_status(tmp_db)
    assert "db_bytes" in st
    assert "key_exists" in st
    assert st["encrypted_exists"] is False


def test_audit_summary_empty(tmp_db):
    """Audit summary returns an empty dict when no events."""
    from src.dashboard import data_access as da

    audit = da.audit_summary(tmp_db)
    assert audit == {}
