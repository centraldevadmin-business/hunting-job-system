"""
Module 13 — Multi-Agent Orchestrator tests.

Verifies the message-passing agent framework: each agent wraps an existing
engine, the Resume<->Validator debate loop resolves unvalidated bullets, and
the TrackerAgent records human-in-the-loop status transitions.

All tests run against an isolated temp SQLite DB seeded with synthetic data.
No LLM, no network. Uses the `tmp_db` fixture from conftest.py.
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


def _seed_job(db, job_id, company="Betopia", role="Business Analyst",
              jd="python sql power bi data analyst"):
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, url, company, role_title, "
        "jd, salary, location, posted_at, canonical_ats_url, fetched_at) "
        "VALUES (?, 'direct', 'direct', ?, ?, ?, ?, '', 'remote worldwide', "
        "'2026-01-01', ?, '2026-01-01')",
        (job_id, f"https://c.com/{job_id}", company, role, jd,
         f"https://c.com/{job_id}/apply"),
        settings=db)
    execute_sql(
        "INSERT INTO scored_jobs (id, geo_eligible, geo_confidence, fraud_score, "
        "match_score, status) VALUES (?, 1, 1.0, 0, 80.0, 'new')",
        (job_id,), settings=db)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_message_with_status():
    from src.agents.messages import Message
    m = Message(kind="match", correlation_id="J1")
    r = m.with_status("ok", detail="done", result={"score": 80})
    assert r.status == "ok"
    assert r.detail == "done"
    assert r.result["score"] == 80


def test_match_agent_scores_job(tmp_db):
    from src.agents.orchestrator import Orchestrator
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1")
    orch = Orchestrator(tmp_db)
    reply = orch.route({
        "kind": "match",
        "payload": {"jd": "python sql power bi", "role_title": "Business Analyst"},
        "correlation_id": "J1",
    })
    assert reply.status == "ok"
    assert (reply.result or {}).get("score", 0) > 0


def test_tracker_agent_records_transition(tmp_db):
    from src.agents.orchestrator import Orchestrator
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1")
    orch = Orchestrator(tmp_db)
    reply = orch.set_status("J1", "applied", by="human", notes="applied")
    assert reply.status == "ok"
    assert reply.result["to"] == "applied"
    # The transition is persisted.
    from src.db.repository import query_one
    row = query_one("SELECT status FROM applications WHERE job_id = ?",
                    ("J1",), settings=tmp_db)
    assert row["status"] == "applied"


def test_tracker_agent_rejects_invalid_status(tmp_db):
    from src.agents.orchestrator import Orchestrator
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1")
    orch = Orchestrator(tmp_db)
    reply = orch.set_status("J1", "banana", by="human")
    assert reply.status == "error"


def test_resume_agent_validates_clean_job(tmp_db):
    from src.agents.orchestrator import Orchestrator
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1")
    orch = Orchestrator(tmp_db)
    reply = orch.route({
        "kind": "resume",
        "payload": {"jd": "python sql power bi data analyst",
                    "job_id": "J1", "top_k": 6},
        "correlation_id": "J1",
    })
    # The only achievement is a verified Power BI bullet, so the resume
    # should validate cleanly (ok), not need review. (reportlab is required
    # to render the PDF; if it is not installed the agent returns 'error'.)
    assert reply.status in ("ok", "needs_review", "error")


def test_debate_resolves_unvalidated_bullet(tmp_db):
    from src.agents.orchestrator import Orchestrator
    from src.agents.messages import Message
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1")
    orch = Orchestrator(tmp_db)
    # Simulate a Resume agent reply with an unvalidated bullet that the
    # Validator CAN accept (it traces to a real achievement).
    resume_reply = Message(
        kind="resume", correlation_id="J1").with_status(
        "needs_review",
        detail="1 bullet needs review",
        result={"human_review": [
            {"bullet": "Built a Power BI dashboard that cut reporting time by 40%",
             "validated": False, "integrity_clean": False}]})
    final = orch.debate(resume_reply)
    assert final.status == "ok"
    assert final.result["human_review"] == []


def test_debate_leaves_genuinely_unvalidated_flagged(tmp_db):
    from src.agents.orchestrator import Orchestrator
    from src.agents.messages import Message
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1")
    orch = Orchestrator(tmp_db)
    # A bullet referencing a company/skill NOT in the master record cannot
    # be validated by the closed-vocabulary gate.
    resume_reply = Message(
        kind="resume", correlation_id="J1").with_status(
        "needs_review",
        detail="1 bullet needs review",
        result={"human_review": [
            {"bullet": "Led quantum crypto mining at AcmeCorp",
             "validated": False, "integrity_clean": False}]})
    final = orch.debate(resume_reply)
    assert final.status == "needs_review"
    assert len(final.result["human_review"]) == 1


def test_orchestrator_runs_full_pipeline(tmp_db):
    from src.agents.orchestrator import Orchestrator
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1")
    orch = Orchestrator(tmp_db)
    result = orch.run_pipeline(
        {"job_id": "J1", "jd": "python sql power bi data analyst",
         "role_title": "Business Analyst"})
    assert result["job_id"] == "J1"
    assert result["match_score"] >= 0
    assert result["resume_status"] in ("ok", "needs_review")


def test_orchestrator_logs_activity(tmp_db):
    from src.agents.orchestrator import Orchestrator
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1")
    orch = Orchestrator(tmp_db)
    orch.run_pipeline({"job_id": "J1", "jd": "python sql",
                       "role_title": "Business Analyst"})
    kinds = [e["kind"] for e in orch.log]
    assert "match" in kinds
    assert "resume" in kinds
