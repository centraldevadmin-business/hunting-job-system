"""
Module 2 tests — Geo-Compliance & JD Distiller.

Verifies:
  - Geo-compliance filter returns ACCEPT / REJECT / REVIEW deterministically.
  - The Country Eligibility Matrix tags target countries.
  - The JD distiller returns structured requirements (regex fallback path).
  - The pipeline writes to scored_jobs and jd_distill.

No LLM key is required — the regex fallback is exercised directly.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.compliance.geo_compliance import GeoComplianceFilter, TARGET_COUNTRIES
from src.distiller.jd_distiller import JDDistiller
from src.compliance.pipeline import CompliancePipeline


# --------------------------------------------------------------------------- #
# Geo-compliance filter
# --------------------------------------------------------------------------- #
def test_geo_accepts_worldwide():
    f = GeoComplianceFilter()
    r = f.evaluate("We hire globally. Remote anywhere. B2B contractor welcome.")
    assert r.verdict == "ACCEPT"
    assert r.eligible is True
    assert r.confidence > 0.5


def test_geo_accepts_eor_contractor():
    f = GeoComplianceFilter()
    r = f.evaluate("This is a B2B engagement. We use an Employer of Record.")
    assert r.verdict == "ACCEPT"
    assert r.eligible is True


def test_geo_rejects_us_citizen():
    f = GeoComplianceFilter()
    r = f.evaluate("Must be a US citizen. Domestic role only.")
    assert r.verdict == "REJECT"
    assert r.eligible is False
    assert "us citizen" in r.matched_reject


def test_geo_rejects_requires_sponsorship():
    f = GeoComplianceFilter()
    r = f.evaluate("Requires sponsorship. Must reside in the USA.")
    assert r.verdict == "REJECT"
    assert "requires sponsorship" in r.matched_reject


def test_geo_reviews_ambiguous():
    f = GeoComplianceFilter()
    r = f.evaluate("Remote — some countries only. Visa sponsorship needed.")
    assert r.verdict == "REVIEW"
    assert r.confidence < 0.7


def test_geo_reviews_no_signal():
    f = GeoComplianceFilter()
    r = f.evaluate("Looking for a sharp analyst to join our team.")
    assert r.verdict == "REVIEW"
    assert r.confidence < 0.7


def test_geo_reject_beats_accept():
    """Reject keywords win even if accept keywords are also present."""
    f = GeoComplianceFilter()
    r = f.evaluate("Worldwide hiring. Must be a US citizen.")
    assert r.verdict == "REJECT"


def test_geo_eligible_countries_named():
    f = GeoComplianceFilter()
    r = f.evaluate("Remote in the United Kingdom and Canada only.")
    countries = r.eligible_countries
    assert "United Kingdom" in countries
    assert "Canada" in countries


def test_geo_eligible_countries_all_when_unspecified():
    f = GeoComplianceFilter()
    r = f.evaluate("B2B contractor. Remote worldwide.")
    # No specific country named -> all target countries.
    assert set(TARGET_COUNTRIES).issubset(set(r.eligible_countries))


# --------------------------------------------------------------------------- #
# JD distiller (regex fallback — no LLM key needed)
# --------------------------------------------------------------------------- #
def test_distiller_detects_seniority():
    d = JDDistiller()
    reqs = d.distill(
        "Senior Business Analyst\n"
        "Must have 5+ years of experience with SQL.\n"
        "Nice to have: Tableau certification.\n"
        "We need someone who can lead projects."
    )
    assert reqs.actual_seniority == "senior"
    assert reqs.source == "regex"


def test_distiller_must_have_vs_nice_to_have():
    d = JDDistiller()
    reqs = d.distill(
        "Must have a bachelor's degree in a quantitative field.\n"
        "Nice to have: experience with Python.\n"
        "Minimum 3 years working as an analyst."
    )
    must = ", ".join(reqs.must_have).lower()
    nice = ", ".join(reqs.nice_to_have).lower()
    assert "bachelor" in must
    assert "python" in nice


def test_distiller_captures_red_flags():
    d = JDDistiller()
    reqs = d.distill(
        "Unpaid role. You need to buy equipment. "
        "Interview via Telegram only."
    )
    joined = ", ".join(reqs.red_flags).lower()
    assert "unpaid" in joined
    assert "equipment" in joined
    assert "telegram" in joined


def test_distiller_top_requirements_bounded():
    d = JDDistiller()
    reqs = d.distill(
        "Must have experience with SQL.\n"
        "Must have experience with Python.\n"
        "Must have experience with Tableau.\n"
        "Must have experience with AWS.\n"
        "Must have experience with Kafka.\n"
        "Must have experience with Spark.\n"
        "Must have experience with Airflow."
    )
    assert len(reqs.top_requirements) <= 5


def test_distiller_never_invents():
    """Every top requirement must be traceable to the JD text."""
    d = JDDistiller()
    jd = (
        "Must have experience with SQL.\n"
        "Nice to have: experience with Python."
    )
    reqs = d.distill(jd)
    for req in reqs.top_requirements:
        assert req.lower() in jd.lower()


# --------------------------------------------------------------------------- #
# Pipeline (writes to scored_jobs + jd_distill)
# --------------------------------------------------------------------------- #
@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    """Point the DB at a temp file and seed one raw posting."""
    from src.db import repository
    monkeypatch.setattr(repository, "db_path", lambda settings=None: tmp_path / "t.db")

    repository.init_db(settings={})
    repository.execute_sql(
        """
        INSERT INTO raw_jobs
            (id, source, source_type, url, company, role_title, jd, salary,
             location, posted_at, canonical_ats_url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "stripe|data-analyst|1",
            "greenhouse",
            "api",
            "https://stripe.com/jobs/search?gh_jid=1",
            "Stripe",
            "Data Analyst",
            "Senior Data Analyst. Must have 5+ years SQL. "
            "Nice to have Tableau. Remote worldwide. B2B contractor.",
            "$200k",
            "Remote",
            "2024-01-01T00:00:00Z",
            "https://stripe.com/jobs/search?gh_jid=1",
            "2024-01-01T00:00:00Z",
        ),
        settings={},
    )
    return tmp_path


def test_pipeline_writes_scored_and_distill(pipeline_env):
    from src.db import repository

    pipe = CompliancePipeline(settings={})
    summary = pipe.process()

    assert summary["processed"] == 1
    assert summary["accepted"] == 1

    scored = repository.query_one(
        "SELECT * FROM scored_jobs WHERE id = ?",
        ("stripe|data-analyst|1",),
        settings={},
    )
    assert scored is not None
    assert scored["geo_eligible"] == 1
    assert scored["status"] == "accepted"

    distill = repository.query_one(
        "SELECT * FROM jd_distill WHERE job_id = ?",
        ("stripe|data-analyst|1",),
        settings={},
    )
    assert distill is not None
    assert distill["actual_seniority"] == "senior"


def test_pipeline_rejects_citizenship_role(pipeline_env, monkeypatch):
    from src.db import repository

    # Replace the JD with a citizenship-required one.
    repository.execute_sql(
        "UPDATE raw_jobs SET jd = ? WHERE id = ?",
        ("Must be a US citizen. Domestic role.", "stripe|data-analyst|1"),
        settings={},
    )

    pipe = CompliancePipeline(settings={})
    summary = pipe.process()

    assert summary["rejected"] == 1
    scored = repository.query_one(
        "SELECT geo_eligible, status FROM scored_jobs WHERE id = ?",
        ("stripe|data-analyst|1",),
        settings={},
    )
    assert scored["geo_eligible"] == 0
    assert scored["status"] == "rejected"
