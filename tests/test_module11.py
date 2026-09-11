"""
Module 11 — Interview Intelligence tests.

Verifies the STAR bullet bank (zero-hallucination core), predicted questions,
the deterministic mock-interview grader, and prep persistence.
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
        "VALUES (1, 'Acme Corp', 'Business Analyst', '02/2024', NULL, 1, 'Dhaka')",
        settings=db)
    execute_sql(
        "INSERT INTO skill (name, level) VALUES ('python', 'advanced'), "
        "('sql', 'advanced'), ('power bi', 'intermediate')", settings=db)
    # A success achievement with a metric.
    execute_sql(
        "INSERT INTO achievement (id, employment_id, bullet, tools, metric) "
        "VALUES (1, 1, "
        "'Built a Power BI dashboard that cut reporting time by 40%', "
        "'power bi, sql', '40%')", settings=db)
    # A failure achievement.
    execute_sql(
        "INSERT INTO achievement (id, employment_id, bullet, tools, metric) "
        "VALUES (2, 1, "
        "'A report failed because of bad data; I found the source and fixed it', "
        "'sql', '')", settings=db)
    execute_sql(
        "INSERT INTO education (id, institution, degree, year, verified) "
        "VALUES (1, 'UOD', 'BBA', 2022, 1)", settings=db)
    execute_sql(
        "INSERT INTO constraints (id, target_roles, target_countries, min_salary, "
        "notice_period_days, visa_needs) VALUES "
        "(1, 'business analyst, data analyst', 'UK, EU, USA', 30000, 14, NULL)",
        settings=db)


def test_star_bank_uses_only_verified_facts(tmp_db):
    from src.interview.intelligence import InterviewEngine
    _seed_master(tmp_db)
    engine = InterviewEngine(tmp_db)
    bank = engine.build_star_bank()
    assert len(bank) >= 2
    # Every bullet traces to a real achievement — no fabricated text.
    all_text = " ".join(b.bullet for b in bank)
    assert "Power BI" in all_text
    assert "40%" in all_text


def test_star_bank_categorization(tmp_db):
    from src.interview.intelligence import InterviewEngine
    _seed_master(tmp_db)
    engine = InterviewEngine(tmp_db)
    bank = engine.build_star_bank()
    categories = {b.question_category for b in bank}
    assert "success" in categories
    assert "failure" in categories


def test_predict_questions_fallback(tmp_db):
    from src.interview.intelligence import InterviewEngine
    _seed_master(tmp_db)
    engine = InterviewEngine(tmp_db)
    qs = engine.predict_questions(
        "Data Analyst role requiring python and sql", "Globex", "Data Analyst")
    assert len(qs) >= 5
    assert any("yourself" in q.lower() for q in qs)


def test_grade_answer_structure(tmp_db):
    from src.interview.intelligence import InterviewEngine
    _seed_master(tmp_db)
    engine = InterviewEngine(tmp_db)
    # A strong STAR answer with a metric.
    strong = (
        "At Acme Corp I was tasked with slow reporting. I built a Power BI "
        "dashboard that cut reporting time by 40%."
    )
    result = engine.grade_answer("Tell me about a success", strong)
    assert 0 <= result["score"] <= 100
    assert result["score"] >= 50  # has STAR + metric


def test_grade_answer_weak(tmp_db):
    from src.interview.intelligence import InterviewEngine
    _seed_master(tmp_db)
    engine = InterviewEngine(tmp_db)
    weak = "I did some stuff. It was fine."
    result = engine.grade_answer("Tell me about a success", weak)
    assert result["score"] < 50


def test_save_prep_persists(tmp_db):
    from src.db.repository import query_one, execute_sql
    from src.interview.intelligence import InterviewEngine
    _seed_master(tmp_db)
    # raw_jobs + scored_jobs so the FK chain for interview_prep.job_id is satisfied.
    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, url, company, role_title, jd, "
        "salary, location, posted_at, canonical_ats_url, fetched_at) "
        "VALUES ('J1', 'direct', 'direct', 'https://globex.com/j1', 'Globex', "
        "'Data Analyst', 'python sql', '', 'remote worldwide', '2026-01-01', "
        "'https://globex.com/j1', '2026-01-01')",
        settings=tmp_db)
    execute_sql(
        "INSERT INTO scored_jobs (id, geo_eligible, geo_confidence, fraud_score, "
        "match_score, status) VALUES ('J1', 1, 1.0, 90, 80.0, 'applied')",
        settings=tmp_db)
    engine = InterviewEngine(tmp_db)
    prep = engine.build_prep(
        "J1", "Data Analyst role requiring python and sql", "Globex", "Data Analyst")
    engine.save_prep(prep)
    row = query_one(
        "SELECT job_id, likely_questions, questions_to_ask FROM interview_prep WHERE job_id = 'J1'",
        settings=tmp_db)
    assert row is not None
    assert "J1" in str(row)


def test_save_prep_idempotent(tmp_db):
    from src.db.repository import query_all, execute_sql
    from src.interview.intelligence import InterviewEngine
    _seed_master(tmp_db)
    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, url, company, role_title, jd, "
        "salary, location, posted_at, canonical_ats_url, fetched_at) "
        "VALUES ('J1', 'direct', 'direct', 'https://globex.com/j1', 'Globex', "
        "'Data Analyst', 'python sql', '', 'remote worldwide', '2026-01-01', "
        "'https://globex.com/j1', '2026-01-01')",
        settings=tmp_db)
    execute_sql(
        "INSERT INTO scored_jobs (id, geo_eligible, geo_confidence, fraud_score, "
        "match_score, status) VALUES ('J1', 1, 1.0, 90, 80.0, 'applied')",
        settings=tmp_db)
    engine = InterviewEngine(tmp_db)
    prep = engine.build_prep(
        "J1", "Data Analyst role requiring python and sql", "Globex", "Data Analyst")
    engine.save_prep(prep)
    engine.save_prep(prep)  # second call should not duplicate
    rows = query_all("SELECT id FROM interview_prep WHERE job_id = 'J1'", settings=tmp_db)
    assert len(rows) == 1
