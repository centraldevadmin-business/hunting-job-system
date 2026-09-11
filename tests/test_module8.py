"""
Module 8 — Self-Tuning Match Engine tests.

Verifies the compounding learning core: historical rate, per-dimension
conversion rates, regularized logistic-regression weight learning,
calibration MAE, and maintenance-mode detection.

All tests run against an isolated temp SQLite DB seeded with synthetic
outcomes. No LLM, no network. Uses the `tmp_db` fixture from conftest.py.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seed_master(db):
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO career_profile (id, full_name, email, phone, location) "
        "VALUES (1, 'Test', 't@e.com', '1', 'Dhaka')",
        settings=db,
    )
    execute_sql(
        "INSERT INTO employment (id, company, role, start_date, end_date, current, location) "
        "VALUES (1, 'Betopia', 'Business Analyst', '02/2024', NULL, 1, 'Dhaka')",
        settings=db,
    )
    execute_sql(
        "INSERT INTO skill (name, level) VALUES ('python', 'advanced'), "
        "('sql', 'advanced'), ('power bi', 'intermediate')",
        settings=db,
    )
    execute_sql(
        "INSERT INTO education (id, institution, degree, year, verified) "
        "VALUES (1, 'UOD', 'BBA', 2022, 1)",
        settings=db,
    )
    execute_sql(
        "INSERT INTO constraints (id, target_roles, target_countries, min_salary, "
        "notice_period_days, visa_needs) VALUES "
        "(1, 'business analyst, data analyst', 'UK, EU, USA', 30000, 14, NULL)",
        settings=db,
    )


def _seed_job(db, job_id, company, role, jd, url, location, salary=""):
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, url, company, role_title, jd, "
        "salary, location, posted_at, canonical_ats_url, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "direct", "direct", url, company, role, jd, salary, location,
         "2026-01-01", url, "2026-01-01"),
        settings=db,
    )
    execute_sql(
        "INSERT INTO scored_jobs (id, geo_eligible, geo_confidence, fraud_score, "
        "match_score, status) VALUES (?, ?, ?, ?, ?, 'applied')",
        (job_id, 1, 1.0, 90, 80.0),
        settings=db,
    )
    execute_sql(
        "INSERT INTO jd_distill (job_id, top_requirements, must_have, nice_to_have, "
        "red_flags, actual_seniority, distilled_at) VALUES "
        "(?, 'python, sql', 'python', 'bi', '', 'mid', '2026-01-01')",
        (job_id,),
        settings=db,
    )


def _seed_application(db, job_id, status, applied_at="2026-01-15"):
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO applications (job_id, contact_email, ats_url, status, "
        "status_confidence, applied_at, interview_count, notes, last_updated) "
        "VALUES (?, NULL, NULL, ?, 1.0, ?, 0, '', ?)",
        (job_id, status, applied_at, applied_at),
        settings=db,
    )
    execute_sql(
        "INSERT INTO application_outcomes (job_id, status, stage_at, notes, "
        "created_at, updated_at) VALUES (?, ?, ?, '', ?, ?)",
        (job_id, status, status, applied_at, applied_at),
        settings=db,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_no_data_returns_prior(tmp_db):
    from src.learning.match_engine import LearningEngine
    eng = LearningEngine(tmp_db)
    assert eng.historical_rate() == pytest.approx(0.15, abs=1e-9)
    res = eng.learn()
    assert res.sample_count == 0
    assert res.trusted is False
    assert res.maintenance_mode is True


def test_historical_rate_matches_data(tmp_db):
    from src.learning.match_engine import LearningEngine
    _seed_master(tmp_db)
    for i in range(10):
        _seed_job(tmp_db, f"J{i}", "Cmp", "Data Analyst", "python sql",
                  f"https://c.com/{i}", "remote worldwide")
        status = "interview" if i < 3 else "applied"
        _seed_application(tmp_db, f"J{i}", status)
    eng = LearningEngine(tmp_db)
    rate = eng.historical_rate()
    assert 0.15 < rate < 0.30
    res = eng.learn()
    assert res.sample_count == 10
    assert res.trusted is False


def test_dimension_rates(tmp_db):
    from src.learning.match_engine import LearningEngine
    _seed_master(tmp_db)
    for i in range(6):
        _seed_job(tmp_db, f"J{i}", "Cmp", "Data Analyst", "python sql",
                  f"https://c.com/{i}", "UK")
        _seed_application(tmp_db, f"J{i}", "interview" if i < 4 else "applied")
    eng = LearningEngine(tmp_db)
    dims = eng.dimension_rates()
    assert "by_country" in dims and "by_seniority" in dims
    uk = dims["by_country"].get("uk", {})
    assert uk["applied"] == 6
    assert uk["interviews"] == 4
    assert 0.0 <= uk["rate"] <= 1.0


def test_learn_weights_with_signal(tmp_db):
    from src.learning.match_engine import LearningEngine
    _seed_master(tmp_db)
    for i in range(12):
        match = 85.0 if i < 6 else 40.0
        _seed_job(tmp_db, f"J{i}", "Cmp", "Data Analyst", "python sql",
                  f"https://c.com/{i}", "remote worldwide")
        from src.db.repository import execute_sql
        execute_sql(
            "UPDATE scored_jobs SET match_score = ? WHERE id = ?",
            (match, f"J{i}"),
            settings=tmp_db,
        )
        _seed_application(tmp_db, f"J{i}", "interview" if i < 6 else "applied")
    eng = LearningEngine(tmp_db)
    weights, bias = eng.learn_weights()
    assert weights["match"] > 0
    assert weights["match"] >= weights["seniority"]
    assert bias != 0


def test_learn_weights_falls_back_with_no_signal(tmp_db):
    from src.learning.match_engine import LearningEngine
    from src.learning.match_engine import _PRIOR_WEIGHTS
    _seed_master(tmp_db)
    for i in range(4):
        _seed_job(tmp_db, f"J{i}", "Cmp", "Data Analyst", "python sql",
                  f"https://c.com/{i}", "remote worldwide")
        _seed_application(tmp_db, f"J{i}", "applied")
    eng = LearningEngine(tmp_db)
    weights, bias = eng.learn_weights()
    assert weights == _PRIOR_WEIGHTS


def test_no_positive_label_uses_priors(tmp_db):
    from src.learning.match_engine import LearningEngine
    from src.learning.match_engine import _PRIOR_WEIGHTS
    _seed_master(tmp_db)
    for i in range(8):
        _seed_job(tmp_db, f"J{i}", "Cmp", "Data Analyst", "python sql",
                  f"https://c.com/{i}", "remote worldwide")
        _seed_application(tmp_db, f"J{i}", "applied")
    eng = LearningEngine(tmp_db)
    weights, bias = eng.learn_weights()
    assert weights == _PRIOR_WEIGHTS


def test_calibration_mae_between_0_and_1(tmp_db):
    from src.learning.match_engine import LearningEngine
    _seed_master(tmp_db)
    for i in range(10):
        _seed_job(tmp_db, f"J{i}", "Cmp", "Data Analyst", "python sql",
                  f"https://c.com/{i}", "remote worldwide")
        _seed_application(tmp_db, f"J{i}", "interview" if i < 5 else "applied")
    eng = LearningEngine(tmp_db)
    mae = eng.calibration()
    assert 0.0 <= mae <= 1.0


def test_maintenance_mode(tmp_db):
    from src.learning.match_engine import LearningEngine
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J0", "Cmp", "Data Analyst", "python sql",
              "https://c.com/0", "remote worldwide")
    _seed_application(tmp_db, "J0", "applied", applied_at="2020-01-01")
    eng = LearningEngine(tmp_db)
    assert eng.maintenance_mode() is True


def test_save_and_load_weights(tmp_db):
    from src.learning.match_engine import LearningEngine
    _seed_master(tmp_db)
    for i in range(12):
        _seed_job(tmp_db, f"J{i}", "Cmp", "Data Analyst", "python sql",
                  f"https://c.com/{i}", "remote worldwide")
        _seed_application(tmp_db, f"J{i}", "interview" if i < 6 else "applied")
    eng = LearningEngine(tmp_db)
    weights, bias = eng.learn_weights()
    eng.save_weights(weights, bias)
    loaded_w, loaded_b = eng.load_weights()
    assert loaded_w["match"] == weights["match"]
    assert loaded_b == bias


def test_full_learn_pass(tmp_db):
    from src.learning.match_engine import LearningEngine
    _seed_master(tmp_db)
    for i in range(10):
        _seed_job(tmp_db, f"J{i}", "Cmp", "Data Analyst", "python sql",
                  f"https://c.com/{i}", "remote worldwide")
        _seed_application(tmp_db, f"J{i}", "interview" if i < 3 else "applied")
    eng = LearningEngine(tmp_db)
    res = eng.learn()
    assert res.sample_count == 10
    assert 0.0 <= res.historical_rate <= 1.0
    assert "by_country" in res.dimension_rates
    assert res.trusted is False
    assert res.message
