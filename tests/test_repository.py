"""
Module 0 test: database connection + schema migration.

DoD: the schema initializes, and basic CRUD works against a temp DB.
"""
from src.db.repository import query_all, query_one, execute_sql


def test_schema_initializes(tmp_db):
    """All tables exist after init_db."""
    rows = query_all("SELECT name FROM sqlite_master WHERE type='table'")
    table_names = {r["name"] for r in rows}
    expected = {
        "career_profile", "employment", "achievement", "skill",
        "education", "constraints", "raw_jobs", "scored_jobs",
        "jd_distill", "resume_variants", "resumes", "authorized_jobs",
        "applications", "application_outcomes", "feedback_features",
        "scoring_model", "kg_nodes", "kg_edges", "referrals",
        "interview_prep", "negotiations", "security_audit", "prompts",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


def test_insert_and_query(tmp_db):
    """A profile row can be inserted and read back."""
    execute_sql(
        "INSERT INTO career_profile (full_name, email, location) VALUES (?, ?, ?)",
        ("Nafiz Mahfuz", "nafiz@example.com", "Dhaka, Bangladesh"),
    )
    row = query_one("SELECT * FROM career_profile WHERE full_name = ?", ("Nafiz Mahfuz",))
    assert row is not None
    assert row["email"] == "nafiz@example.com"
    assert row["location"] == "Dhaka, Bangladesh"
