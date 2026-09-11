"""
Module 3 tests — Anti-Fraud / Risk Engine.

Verifies:
  - Domain mismatch → red (the strongest signal).
  - Payment / equipment requests → red.
  - Salary absurdity → amber (anomaly, not red).
  - Free-domain contact email → amber.
  - Composite score maps to green / amber / red.
  - Red postings are auto-rejected by the pipeline.
  - Company review aggregates legitimacy + hiring activity.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.fraud.fraud_engine import FraudEngine, parse_salary, email_domain
from src.fraud.company_review import CompanyReviewEngine
from src.fraud.pipeline import FraudPipeline


# --------------------------------------------------------------------------- #
# Salary parsing
# --------------------------------------------------------------------------- #
def test_parse_salary_k():
    assert parse_salary("$120k") == 120000


def test_parse_salary_full():
    assert parse_salary("$120,000") == 120000


def test_parse_salary_range_returns_lower():
    assert parse_salary("$80k–$120k") == 80000


def test_parse_salary_none():
    assert parse_salary(None) is None
    assert parse_salary("competitive") is None


# --------------------------------------------------------------------------- #
# Domain match (strongest signal)
# --------------------------------------------------------------------------- #
def test_domain_mismatch_is_red():
    eng = FraudEngine()
    r = eng.evaluate(
        url="https://apply-scammy.com/job/1",
        jd="Data Analyst position",
        company_domain="stripe.com",
    )
    assert r.level == "red"
    assert r.rejected is True
    assert any("domain mismatch" in f for f in r.flags)


def test_domain_match_is_green():
    eng = FraudEngine()
    r = eng.evaluate(
        url="https://stripe.com/jobs/search?gh_jid=1",
        jd="Data Analyst position",
        company_domain="stripe.com",
    )
    assert r.level == "green"
    assert r.score == 100


# --------------------------------------------------------------------------- #
# Payment / equipment requests
# --------------------------------------------------------------------------- #
def test_buy_equipment_is_red():
    eng = FraudEngine()
    r = eng.evaluate(
        url="https://stripe.com/jobs/search?gh_jid=1",
        jd="You must buy your own equipment before starting.",
        company_domain="stripe.com",
    )
    assert r.level == "red"
    assert any("equipment" in f.lower() for f in r.flags)


def test_gift_card_is_red():
    eng = FraudEngine()
    r = eng.evaluate(
        url="https://stripe.com/jobs/search?gh_jid=1",
        jd="A gift card is required to process your first paycheck.",
        company_domain="stripe.com",
    )
    assert r.level == "red"


def test_telegram_interview_is_red():
    eng = FraudEngine()
    r = eng.evaluate(
        url="https://stripe.com/jobs/search?gh_jid=1",
        jd="Interviews are conducted via Telegram only.",
        company_domain="stripe.com",
    )
    assert r.level == "red"


# --------------------------------------------------------------------------- #
# Salary absurdity (amber, not red)
# --------------------------------------------------------------------------- #
def test_salary_absurdity_is_amber():
    eng = FraudEngine(min_salary=30000)
    r = eng.evaluate(
        url="https://stripe.com/jobs/search?gh_jid=1",
        jd="Data Analyst",
        salary="$2,000,000",
        company_domain="stripe.com",
    )
    assert r.level == "amber"
    assert r.score < 100
    assert any("anomaly" in f.lower() for f in r.flags)


def test_normal_salary_is_green():
    eng = FraudEngine(min_salary=30000)
    r = eng.evaluate(
        url="https://stripe.com/jobs/search?gh_jid=1",
        jd="Data Analyst",
        salary="$70k",
        company_domain="stripe.com",
    )
    assert r.level == "green"


# --------------------------------------------------------------------------- #
# Free-domain contact email (amber)
# --------------------------------------------------------------------------- #
def test_free_email_contact_is_amber():
    eng = FraudEngine()
    r = eng.evaluate(
        url="https://stripe.com/jobs/search?gh_jid=1",
        jd="Apply to hiringmanager@gmail.com",
        contact_email="hiringmanager@gmail.com",
        company_domain="stripe.com",
    )
    assert r.level == "amber"
    assert any("free-domain" in f.lower() for f in r.flags)


def test_company_email_is_not_flagged():
    eng = FraudEngine()
    r = eng.evaluate(
        url="https://stripe.com/jobs/search?gh_jid=1",
        jd="Apply to recruiting@stripe.com",
        contact_email="recruiting@stripe.com",
        company_domain="stripe.com",
    )
    assert r.level == "green"


# --------------------------------------------------------------------------- #
# Company review
# --------------------------------------------------------------------------- #
def test_company_review_active_hiring_is_green():
    cr = CompanyReviewEngine()
    for _ in range(6):
        cr.add_posting("Stripe", "stripe.com", True, True, True, is_live=True)
    review = cr.review("Stripe")
    assert review.composite_score >= 70
    assert review.level == "green"


def test_company_review_single_stale_posting_is_amber():
    cr = CompanyReviewEngine()
    cr.add_posting("Stripe", "stripe.com", False, False, False, is_live=False)
    review = cr.review("Stripe")
    assert review.composite_score < 70
    assert review.level in ("amber", "red")


def test_company_review_no_postings_is_red():
    cr = CompanyReviewEngine()
    review = cr.review("Unknown")
    assert review.level == "red"


# --------------------------------------------------------------------------- #
# Pipeline — auto-reject red postings
# --------------------------------------------------------------------------- #
@pytest.fixture
def fraud_env(tmp_path, monkeypatch):
    from src.db import repository
    monkeypatch.setattr(repository, "db_path", lambda settings=None: tmp_path / "t.db")
    repository.init_db(settings={})

    # A legit posting.
    repository.execute_sql(
        """
        INSERT INTO raw_jobs
            (id, source, source_type, url, company, role_title, jd, salary,
             location, posted_at, canonical_ats_url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "stripe|data-analyst|1",
            "greenhouse", "api",
            "https://stripe.com/jobs/search?gh_jid=1",
            "Stripe", "Data Analyst",
            "Senior Data Analyst. Must have SQL. Remote worldwide. B2B.",
            "$70k", "Remote", "2024-01-01T00:00:00Z",
            "https://stripe.com/jobs/search?gh_jid=1", "2024-01-01T00:00:00Z",
        ),
        settings={},
    )
    # A scam posting on a fake domain.
    repository.execute_sql(
        """
        INSERT INTO raw_jobs
            (id, source, source_type, url, company, role_title, jd, salary,
             location, posted_at, canonical_ats_url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "stripe|data-analyst|2",
            "greenhouse", "api",
            "https://apply-scammy.com/job/99",
            "Stripe", "Data Analyst",
            "Buy your own equipment. Gift card required.",
            "$150k", "Remote", "2024-01-02T00:00:00Z",
            "https://apply-scammy.com/job/99", "2024-01-02T00:00:00Z",
        ),
        settings={},
    )
    return tmp_path


def test_pipeline_scores_and_rejects_scam(fraud_env):
    from src.db import repository

    pipe = FraudPipeline(settings={})
    summary = pipe.process()

    assert summary["processed"] == 2
    assert summary["red"] == 1
    assert summary["green"] == 1

    scam = repository.query_one(
        "SELECT fraud_score, fraud_flags, status FROM scored_jobs WHERE id = ?",
        ("stripe|data-analyst|2",),
        settings={},
    )
    assert scam["fraud_score"] == 0
    assert scam["status"] == "rejected"

    legit = repository.query_one(
        "SELECT fraud_score, status FROM scored_jobs WHERE id = ?",
        ("stripe|data-analyst|1",),
        settings={},
    )
    assert legit["fraud_score"] == 100
    assert legit["status"] != "rejected"


def test_pipeline_writes_company_review(fraud_env):
    from src.db import repository

    pipe = FraudPipeline(settings={})
    pipe.process()

    review = repository.query_one(
        "SELECT composite, level FROM company_reviews WHERE company = ?",
        ("Stripe",),
        settings={},
    )
    assert review is not None
    assert review["level"] in ("green", "amber", "red")
