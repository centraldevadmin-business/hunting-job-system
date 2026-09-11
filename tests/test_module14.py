"""
Module 14 — Self-Improvement Loop tests.

Verifies the four pillars: weekly review, drift detection, prompt library
recommendations, and the audit trail. All tests run against an isolated temp
SQLite DB seeded with synthetic outcomes. No LLM, no network.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _seed_master(db):
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO career_profile (id, full_name, email, phone, location) "
        "VALUES (1, 'Test', 't@e.com', '1', 'Dhaka')", settings=db)
    execute_sql(
        "INSERT INTO employment (id, company, role, start_date, end_date, current, location) "
        "VALUES (1, 'Betopia', 'Business Analyst', '02/2024', NULL, 1, 'Dhaka')",
        settings=db)
    execute_sql(
        "INSERT INTO achievement (id, employment_id, bullet, tools, metric) "
        "VALUES (1, 1, "
        "'Built a Power BI dashboard that cut reporting time by 40%', "
        "'power bi, sql', '40%')", settings=db)
    execute_sql(
        "INSERT INTO skill (name, level) VALUES ('python', 'advanced'), "
        "('sql', 'advanced'), ('power bi', 'intermediate')", settings=db)
    execute_sql(
        "INSERT INTO education (id, institution, degree, year, verified) "
        "VALUES (1, 'UOD', 'BBA', 2022, 1)", settings=db)
    execute_sql(
        "INSERT INTO constraints (id, target_roles, target_countries, min_salary, "
        "notice_period_days, visa_needs) VALUES "
        "(1, 'business analyst, data analyst', 'UK, EU, USA', 30000, 14, NULL)",
        settings=db)


def _seed_job(db, job_id):
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, url, company, role_title, "
        "jd, salary, location, posted_at, canonical_ats_url, fetched_at) "
        "VALUES (?, 'direct', 'direct', ?, ?, ?, ?, '', 'remote worldwide', "
        "'2026-01-01', ?, '2026-01-01')",
        (job_id, f"https://c.com/{job_id}", "Betopia", "Business Analyst",
         "python sql power bi", f"https://c.com/{job_id}/apply"),
        settings=db)
    execute_sql(
        "INSERT INTO scored_jobs (id, geo_eligible, geo_confidence, fraud_score, "
        "match_score, status) VALUES (?, 1, 1.0, 0, 80.0, 'new')",
        (job_id,), settings=db)


def _seed_outcome(db, job_id, got_interview, rating=4, days_ago=1):
    from src.db import repository
    from src.db.repository import execute_sql, now_iso
    from datetime import timedelta, datetime
    ts = (datetime.fromisoformat(now_iso()) - timedelta(days=days_ago))
    execute_sql(
        "INSERT INTO application_outcomes (job_id, status, stage_at, notes, "
        "created_at, updated_at) VALUES (?, ?, ?, '', ?, ?)",
        (job_id, "interview" if got_interview else "applied",
         "interview" if got_interview else "applied", ts, ts),
        settings=db)
    # Fetch the auto-generated outcome id so feedback_features references it.
    outcome_id = repository.query_one(
        "SELECT id FROM application_outcomes WHERE job_id = ?",
        (job_id,), settings=db)["id"]
    execute_sql(
        "INSERT INTO feedback_features (outcome_id, skill_match, domain_match, "
        "seniority_match, country_match, comp_match, resume_rating, fraud_score, "
        "source_type, applied_at, got_interview) "
        "VALUES (?, 0.9, 0.8, 0.7, 0.9, 0.6, ?, 90, 'direct', ?, ?)",
        (outcome_id, rating, ts, 1 if got_interview else 0), settings=db)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_weekly_review_empty(tmp_db):
    from src.self_improve.loop import SelfImproveLoop
    loop = SelfImproveLoop(tmp_db)
    review = loop.weekly_review()
    assert review.applications == 0
    assert review.conversion_rate == 0.0


def test_weekly_review_counts(tmp_db):
    from src.self_improve.loop import SelfImproveLoop
    _seed_master(tmp_db)
    for i in range(5):
        _seed_job(tmp_db, f"J{i}")
        _seed_outcome(tmp_db, f"J{i}", got_interview=(i < 2))
    loop = SelfImproveLoop(tmp_db)
    review = loop.weekly_review()
    assert review.applications == 5
    assert review.interviews == 2
    assert review.conversion_rate == pytest.approx(0.4)


def test_drift_detection_no_drift(tmp_db):
    from src.self_improve.loop import SelfImproveLoop
    _seed_master(tmp_db)
    # Recent window: 50% conversion.
    for i in range(4):
        _seed_job(tmp_db, f"R{i}")
        _seed_outcome(tmp_db, f"R{i}", got_interview=(i < 2))
    loop = SelfImproveLoop(tmp_db)
    drift = loop.detect_drift()
    # No prior window to compare against → no drift.
    assert drift.drifted is False


def test_drift_detection_flags_drop(tmp_db):
    from src.self_improve.loop import SelfImproveLoop
    _seed_master(tmp_db)
    # Prior window (older): high conversion.
    for i in range(4):
        _seed_job(tmp_db, f"P{i}")
        _seed_outcome(tmp_db, f"P{i}", got_interview=1, days_ago=20)
    # Recent window: low conversion.
    for i in range(4):
        _seed_job(tmp_db, f"X{i}")
        _seed_outcome(tmp_db, f"X{i}", got_interview=0, days_ago=1)
    loop = SelfImproveLoop(tmp_db)
    drift = loop.detect_drift()
    assert drift.drifted is True
    assert drift.drop_pct < 0
    assert len(drift.likely_causes) >= 1


def test_prompt_recommendations_flag_low(tmp_db):
    from src.self_improve.loop import SelfImproveLoop
    from src.db.repository import execute_sql
    _seed_master(tmp_db)
    execute_sql(
        "INSERT INTO prompts (task, version, outcome, updated_at) VALUES "
        "('jd_distiller', 3, 0.10, '2026-01-01'), "
        "('resume_rewrite', 2, 0.80, '2026-01-01')",
        settings=tmp_db)
    loop = SelfImproveLoop(tmp_db)
    recs = {r.task: r for r in loop.prompt_recommendations()}
    assert recs["jd_distiller"].flagged is True
    assert recs["jd_distiller"].proposed_version == 4
    assert recs["resume_rewrite"].flagged is False


def test_audit_trail_records_events(tmp_db):
    from src.self_improve.loop import SelfImproveLoop
    from src.db.repository import execute_sql, query_one
    _seed_master(tmp_db)
    execute_sql(
        "INSERT INTO prompts (task, version, outcome, updated_at) VALUES "
        "('jd_distiller', 1, 0.10, '2026-01-01')", settings=tmp_db)
    loop = SelfImproveLoop(tmp_db)
    result = loop.run()
    events = [a["event"] for a in result.audit]
    assert "weekly_review" in events
    assert "drift_check" in events
    assert "prompt_flag" in events
    # The audit is persisted to the security_audit table.
    rows = query_one("SELECT COUNT(*) AS c FROM security_audit")
    assert rows["c"] >= 3


def test_run_returns_full_result(tmp_db):
    from src.self_improve.loop import SelfImproveLoop
    _seed_master(tmp_db)
    for i in range(3):
        _seed_job(tmp_db, f"J{i}")
        _seed_outcome(tmp_db, f"J{i}", got_interview=(i < 2))
    loop = SelfImproveLoop(tmp_db)
    result = loop.run()
    assert result.weekly.applications == 3
    assert result.weekly.interviews == 2
    assert result.drift is not None
    assert isinstance(result.prompt_recs, list)
