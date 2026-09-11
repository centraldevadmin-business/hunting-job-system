"""
Module 12 — Negotiation & Compensation Advisor tests.

Verifies the market benchmark (reused deterministic estimator), offer
analysis (gap math, verdict, flags), the negotiation script (LLM +
deterministic fallback), and persistence.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _seed_job(db):
    from src.db.repository import execute_sql
    # raw_jobs + scored_jobs so negotiations.job_id FK resolves.
    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, url, company, role_title, "
        "jd, salary, location, posted_at, canonical_ats_url, fetched_at) "
        "VALUES ('job1', 'web', 'web', 'https://x', 'Globex', 'Senior Business "
        "Analyst', 'BA role', '$130k - $160k', 'Remote', '2026-01-01', "
        "'https://x/apply', '2026-01-01')", settings=db)
    # scored_jobs.id must match raw_jobs.id (FK).
    execute_sql(
        "INSERT INTO scored_jobs (id, geo_eligible, geo_confidence, fraud_score, "
        "fraud_flags, match_score, status, reviewed_at) "
        "VALUES ('job1', 1, 1.0, 0.0, '', 80, 'new', '2026-01-01')",
        settings=db)
    return "job1"


def test_benchmark_uses_real_salary(tmp_db):
    from src.comp.advisor import CompAdvisor
    _seed_job(tmp_db)
    advisor = CompAdvisor(tmp_db)
    bench = advisor.benchmark("Globex", "Senior Business Analyst", "$130k - $160k")
    assert bench.method == "real"
    # Real salary $130k-$160k -> mid ~145k, low ~110k.
    assert bench.mid > 130_000
    assert bench.low < bench.mid < bench.high


def test_benchmark_falls_back_to_estimate(tmp_db):
    from src.comp.advisor import CompAdvisor
    _seed_job(tmp_db)
    advisor = CompAdvisor(tmp_db)
    bench = advisor.benchmark("Globex", "Business Analyst")  # no real salary
    assert bench.method == "estimated"
    assert bench.low < bench.mid < bench.high


def test_analyze_at_market_offer(tmp_db):
    from src.comp.advisor import CompAdvisor, Offer
    _seed_job(tmp_db)
    advisor = CompAdvisor(tmp_db)
    offer = Offer(base=145_000, equity=0, signing=0)
    a = advisor.analyze(offer, "Globex", "Business Analyst", "$130k - $160k")
    # Total CTC = base (no equity/other).
    assert abs(a.total_ctc - 145_000) < 1
    # Verdict should be at-market or strong for a fair offer.
    assert a.verdict in ("at-market", "strong")
    # Target range should be above the offer for a below/at-market offer.
    assert a.target_low > 0
    assert a.target_high > a.target_low


def test_analyze_below_market_flags_lowball(tmp_db):
    from src.comp.advisor import CompAdvisor, Offer
    _seed_job(tmp_db)
    advisor = CompAdvisor(tmp_db)
    offer = Offer(base=80_000, equity=0, signing=0)
    a = advisor.analyze(offer, "Globex", "Business Analyst", "$130k - $160k")
    assert a.verdict in ("below-market", "low")
    assert a.gap_pct < 0
    # Should flag the lowball base.
    assert any("below" in f.lower() for f in a.flags)


def test_signing_bonus_flag(tmp_db):
    from src.comp.advisor import CompAdvisor, Offer
    _seed_job(tmp_db)
    advisor = CompAdvisor(tmp_db)
    offer = Offer(base=90_000, signing=50_000)
    a = advisor.analyze(offer, "Globex", "Business Analyst", "$130k - $160k")
    assert any("signing" in f.lower() or "one-time" in f.lower()
               for f in a.flags)


def test_script_generated(tmp_db):
    from src.comp.advisor import CompAdvisor, Offer
    _seed_job(tmp_db)
    advisor = CompAdvisor(tmp_db)
    offer = Offer(base=120_000)
    a = advisor.analyze(offer, "Globex", "Business Analyst", "$130k - $160k")
    # Talking points and an email script are always produced (fallback if no key).
    assert len(a.talking_points) >= 3
    assert "Globex" in a.email_script
    assert "base" in a.email_script.lower()


def test_save_and_history(tmp_db):
    from src.comp.advisor import CompAdvisor, Offer
    job_id = _seed_job(tmp_db)
    advisor = CompAdvisor(tmp_db)
    offer = Offer(base=120_000)
    a = advisor.analyze(offer, "Globex", "Business Analyst", "$130k - $160k")
    advisor.save_negotiation(str(job_id), "Globex", "Business Analyst", offer, a)
    hist = advisor.history()
    assert len(hist) == 1
    assert str(hist[0]["job_id"]) == str(job_id)
    assert "$" in hist[0]["offer_amount"]
    # Idempotent — saving again does not duplicate.
    advisor.save_negotiation(str(job_id), "Globex", "Business Analyst", offer, a)
    assert len(advisor.history()) == 1
