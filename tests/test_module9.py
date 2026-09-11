"""
Module 9 — Knowledge Graph tests.

Verifies graph sync, similar-company discovery, referral-path BFS, and
company clustering. Pure graph theory over local SQLite — no network, no LLM.
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


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_sync_creates_nodes(tmp_db):
    from src.knowledge.graph import KnowledgeGraph
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql power bi", "https://acme.com/j1", "remote worldwide")
    g = KnowledgeGraph(tmp_db)
    stats = g.sync()
    assert stats.companies >= 1
    assert stats.skills >= 3
    assert stats.edges >= 1


def test_sync_is_idempotent(tmp_db):
    from src.knowledge.graph import KnowledgeGraph
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql power bi", "https://acme.com/j1", "remote worldwide")
    g = KnowledgeGraph(tmp_db)
    s1 = g.sync()
    s2 = g.sync()
    assert s2.companies == s1.companies
    assert s2.edges == s1.edges


def test_similar_companies(tmp_db):
    from src.knowledge.graph import KnowledgeGraph
    _seed_master(tmp_db)
    # Two companies both requiring python + sql.
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql power bi", "https://acme.com/j1", "remote worldwide")
    _seed_job(tmp_db, "J2", "Globex", "Data Analyst",
              "python sql", "https://globex.com/j2", "remote worldwide")
    g = KnowledgeGraph(tmp_db)
    g.sync()
    sims = g.similar_companies("Acme Corp")
    names = [s["company"] for s in sims]
    assert "Globex" in names
    assert "Acme Corp" not in names  # excludes seed


def test_referral_path_found(tmp_db):
    from src.db.repository import execute_sql, query_one
    from src.knowledge.graph import KnowledgeGraph
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql", "https://acme.com/j1", "remote worldwide")
    _seed_job(tmp_db, "J2", "Globex", "Data Analyst",
              "python sql", "https://globex.com/j2", "remote worldwide")
    g = KnowledgeGraph(tmp_db)
    g.sync()
    # Add a person node connected to Globex, and a knows edge from you to them.
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
    # You know Alice.
    you = query_one("SELECT id FROM kg_nodes WHERE entity_id = 'candidate'",
                    settings=tmp_db)
    execute_sql(
        "INSERT INTO kg_edges (from_node, to_node, rel, weight, created_at) "
        "VALUES (?, ?, 'knows', 1.0, '2026-01-01')",
        (you["id"], alice["id"]), settings=tmp_db)
    g.sync()
    path = g.referral_path("Globex")
    assert path is not None
    assert path[0] == "you"
    assert path[-1] == "Globex"


def test_referral_path_none_when_unreachable(tmp_db):
    from src.knowledge.graph import KnowledgeGraph
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql", "https://acme.com/j1", "remote worldwide")
    g = KnowledgeGraph(tmp_db)
    g.sync()
    # No edge to "Isoland" → no path.
    assert g.referral_path("Isoland") is None


def test_company_clusters(tmp_db):
    from src.knowledge.graph import KnowledgeGraph
    _seed_master(tmp_db)
    _seed_job(tmp_db, "J1", "Acme Corp", "Data Analyst",
              "python sql", "https://acme.com/j1", "remote worldwide")
    _seed_job(tmp_db, "J2", "Globex", "Data Analyst",
              "python sql", "https://globex.com/j2", "remote worldwide")
    g = KnowledgeGraph(tmp_db)
    g.sync()
    clusters = g.company_clusters()
    skills = [c["skill"] for c in clusters]
    assert "python" in skills
    py_cluster = next(c for c in clusters if c["skill"] == "python")
    assert "Acme Corp" in py_cluster["companies"]
    assert "Globex" in py_cluster["companies"]
