"""
Module 10 — Referral & Network tests.

Verifies warm-path discovery, referral drafts, and persistence. The engine
never sends anything — it only finds paths and drafts messages.
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
    execute_sql(
        "INSERT INTO education (id, institution, degree, year, verified) "
        "VALUES (1, 'UOD', 'BBA', 2022, 1)", settings=db)
    execute_sql(
        "INSERT INTO constraints (id, target_roles, target_countries, min_salary, "
        "notice_period_days, visa_needs) VALUES "
        "(1, 'business analyst, data analyst', 'UK, EU, USA', 30000, 14, NULL)",
        settings=db)


def _seed_job(db, job_id, company, role, jd, url, location):
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, url, company, role_title, jd, "
        "salary, location, posted_at, canonical_ats_url, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "direct", "direct", url, company, role, jd, "", location,
         "2026-01-01", url, "2026-01-01"),
        settings=db)


def test_find_paths_with_warm_connection(tmp_db):
    from src.db.repository import execute_sql, query_one
    from src.network.referrals import ReferralEngine
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql", "https://acme.com/j1", "remote worldwide")
    _seed_job(tmp_db, "J2", "Globex", "Data Analyst",
              "python sql", "https://globex.com/j2", "remote worldwide")
    g_engine = ReferralEngine(tmp_db)
    g_engine.graph.sync()
    # Add a person connected to Globex, and a knows edge from you.
    execute_sql(
        "INSERT INTO kg_nodes (kind, name, entity_id, payload, first_seen) "
        "VALUES ('person', 'Alice', 'alice', NULL, '2026-01-01')",
        settings=tmp_db)
    alice = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'alice'",
                      settings=tmp_db)
    globex = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'Globex'",
                       settings=tmp_db)
    execute_sql(
        "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
        "VALUES (?, ?, 'knows', 1.0, '2026-01-01')",
        (alice["id"], globex["id"]), settings=tmp_db)
    you = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'candidate'",
                    settings=tmp_db)
    execute_sql(
        "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
        "VALUES (?, ?, 'knows', 1.0, '2026-01-01')",
        (you["id"], alice["id"]), settings=tmp_db)
    g_engine.graph.sync()

    paths = g_engine.find_paths()
    companies = [p.company for p in paths]
    assert "Globex" in companies
    # The draft should be non-empty and reference the contact.
    globex_path = next(p for p in paths if p.company == "Globex")
    assert globex_path.contact == "Alice"
    assert "Alice" in globex_path.draft


def test_referral_draft_is_deterministic(tmp_db):
    from src.network.referrals import ReferralEngine
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql", "https://acme.com/j1", "remote worldwide")
    _seed_job(tmp_db, "J2", "Globex", "Data Analyst",
              "python sql", "https://globex.com/j2", "remote worldwide")
    engine = ReferralEngine(tmp_db)
    engine.graph.sync()
    from src.db.repository import execute_sql, query_one
    execute_sql(
        "INSERT INTO kg_nodes (kind, name, entity_id, payload, first_seen) "
        "VALUES ('person', 'Alice', 'alice', NULL, '2026-01-01')",
        settings=tmp_db)
    alice = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'alice'",
                      settings=tmp_db)
    globex = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'Globex'",
                       settings=tmp_db)
    execute_sql(
        "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
        "VALUES (?, ?, 'knows', 1.0, '2026-01-01')",
        (alice["id"], globex["id"]), settings=tmp_db)
    you = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'candidate'",
                    settings=tmp_db)
    execute_sql(
        "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
        "VALUES (?, ?, 'knows', 1.0, '2026-01-01')",
        (you["id"], alice["id"]), settings=tmp_db)
    engine.graph.sync()
    paths = engine.find_paths()
    globex_path = next(p for p in paths if p.company == "Globex")
    # Deterministic: same input → same draft.
    assert globex_path.draft == engine._draft("Alice", "Globex", [])


def test_save_referrals_persists(tmp_db):
    from src.db.repository import query_all
    from src.network.referrals import ReferralEngine
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql", "https://acme.com/j1", "remote worldwide")
    _seed_job(tmp_db, "J2", "Globex", "Data Analyst",
              "python sql", "https://globex.com/j2", "remote worldwide")
    engine = ReferralEngine(tmp_db)
    engine.graph.sync()
    from src.db.repository import execute_sql, query_one
    execute_sql(
        "INSERT INTO kg_nodes (kind, name, entity_id, payload, first_seen) "
        "VALUES ('person', 'Alice', 'alice', NULL, '2026-01-01')",
        settings=tmp_db)
    alice = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'alice'",
                      settings=tmp_db)
    globex = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'Globex'",
                       settings=tmp_db)
    execute_sql(
        "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
        "VALUES (?, ?, 'knows', 1.0, '2026-01-01')",
        (alice["id"], globex["id"]), settings=tmp_db)
    you = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'candidate'",
                    settings=tmp_db)
    execute_sql(
        "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
        "VALUES (?, ?, 'knows', 1.0, '2026-01-01')",
        (you["id"], alice["id"]), settings=tmp_db)
    engine.graph.sync()
    paths = engine.find_paths()
    saved = engine.save_referrals(paths)
    rows = query_all("SELECT id FROM referrals", settings=tmp_db)
    assert len(rows) == saved
    assert saved >= 1


def test_report_counts(tmp_db):
    from src.network.referrals import ReferralEngine
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql", "https://acme.com/j1", "remote worldwide")
    _seed_job(tmp_db, "J2", "Globex", "Data Analyst",
              "python sql", "https://globex.com/j2", "remote worldwide")
    engine = ReferralEngine(tmp_db)
    engine.graph.sync()
    from src.db.repository import execute_sql, query_one
    execute_sql(
        "INSERT INTO kg_nodes (kind, name, entity_id, payload, first_seen) "
        "VALUES ('person', 'Alice', 'alice', NULL, '2026-01-01')",
        settings=tmp_db)
    alice = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'alice'",
                      settings=tmp_db)
    globex = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'Globex'",
                       settings=tmp_db)
    execute_sql(
        "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
        "VALUES (?, ?, 'knows', 1.0, '2026-01-01')",
        (alice["id"], globex["id"]), settings=tmp_db)
    you = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'candidate'",
                    settings=tmp_db)
    execute_sql(
        "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
        "VALUES (?, ?, 'knows', 1.0, '2026-01-01')",
        (you["id"], alice["id"]), settings=tmp_db)
    engine.graph.sync()
    report = engine.report()
    assert report.warm_paths >= 1
    assert report.drafts_ready >= 1
