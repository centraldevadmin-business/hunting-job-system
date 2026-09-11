"""
Module 5 tests — The Dashboard (Streamlit + data access + possibility engine).

Verifies:
  - Brutal-possibility math: multiplicative gates, geo/fraud hard gates.
  - Historical rate learning from outcomes (Bayesian smoothing).
  - Data-access layer surfaces raw jobs when scored_jobs is empty.
  - Queue filters and sorting.
  - Status transitions and authorization.
  - Analytics: funnel, calibration, leaderboard, follow-up, dry-market.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.dashboard.possibility import (
    PossibilityEngine,
    PossibilityResult,
    DEFAULT_HISTORICAL_RATE,
)
from src.dashboard import data_access as da


# --------------------------------------------------------------------------- #
# Possibility engine
# --------------------------------------------------------------------------- #
def test_possibility_full_score():
    """A perfect match, eligible, clean, with a 20% historical rate → 20%."""
    eng = PossibilityEngine(historical_rate=0.20)
    r = eng.compute(match_score=100, geo_eligible=True, fraud_score=100)
    assert r.pct == pytest.approx(20.0)
    assert r.trusted is False


def test_possibility_geo_gate_zeroes():
    """Geo-ineligible → 0% regardless of everything else."""
    eng = PossibilityEngine(historical_rate=0.30)
    r = eng.compute(match_score=100, geo_eligible=False, fraud_score=100)
    assert r.pct == 0.0
    assert r.gated_out == "geo-ineligible"


def test_possibility_fraud_red_zeroes():
    """A red posting (fraud_score 0) → 0%."""
    eng = PossibilityEngine(historical_rate=0.30)
    r = eng.compute(match_score=100, geo_eligible=True, fraud_score=0)
    assert r.pct == 0.0
    assert r.gated_out == "fraud"


def test_possibility_fraud_amber_discounts():
    """Amber fraud (score 50) discounts proportionally: 100*1*0.5*0.3 = 15."""
    eng = PossibilityEngine(historical_rate=0.30)
    r = eng.compute(match_score=100, geo_eligible=True, fraud_score=50)
    assert r.pct == pytest.approx(15.0)


def test_possibility_fraud_none_treated_clean():
    """Unknown fraud score → gate 1.0 (not penalized)."""
    eng = PossibilityEngine(historical_rate=0.30)
    r = eng.compute(match_score=100, geo_eligible=True, fraud_score=None)
    assert r.fraud_gate == 1.0
    assert r.pct == pytest.approx(30.0)


def test_possibility_trusted_after_outcomes():
    """Trusted flips on once outcomes >= outcomes_before_trusted."""
    eng = PossibilityEngine(outcomes=35, outcomes_before_trusted=30)
    r = eng.compute(match_score=50, geo_eligible=True, fraud_score=100)
    assert r.trusted is True


def test_historical_rate_from_outcomes():
    """10 interviews out of 100 applications → ~0.10 (smoothed toward prior)."""
    eng = PossibilityEngine()
    rate = eng.historical_rate_from_outcomes(interview_count=10, applied_count=100)
    assert 0.0 < rate < 0.20


def test_historical_rate_no_data_returns_prior():
    """No data → conservative prior."""
    eng = PossibilityEngine()
    assert eng.historical_rate_from_outcomes(0, 0) == DEFAULT_HISTORICAL_RATE


# --------------------------------------------------------------------------- #
# Data-access layer (graceful degradation when scored_jobs empty)
# --------------------------------------------------------------------------- #
def test_list_job_cards_surfaces_raw_jobs(tmp_db):
    """When scored_jobs is empty, raw jobs still surface as cards."""
    da.execute_sql(
        "INSERT INTO raw_jobs (id, company, role_title, jd) VALUES (?, ?, ?, ?)",
        ("GL-RAW-1", "Acme", "Data Analyst", "remote worldwide"),
        settings=tmp_db,
    )
    cards = da.list_job_cards(settings=tmp_db)
    assert len(cards) >= 1
    c = cards[0]
    assert c.company == "Acme"
    assert c.possibility_pct is not None  # computed even without a match score


def test_filter_by_possibility(tmp_db):
    cards = da.list_job_cards(settings=tmp_db)
    f = da.QueueFilter(min_possibility=100.0)  # nothing reaches 100% unscored
    filtered = da.filter_cards(cards, f)
    assert len(filtered) == 0


def test_filter_by_fraud_status(tmp_db):
    from src.db.repository import query_one
    # Seed one green, one red posting.
    for jid, fs in [("GL-GREEN-1", 90), ("GL-RED-1", 10)]:
        da.execute_sql(
            "INSERT INTO raw_jobs (id, company, role_title) VALUES (?, ?, ?)",
            (jid, "Acme", "Data Analyst"),
            settings=tmp_db,
        )
        da.execute_sql(
            "INSERT INTO scored_jobs (id, fraud_score) VALUES (?, ?)",
            (jid, fs),
            settings=tmp_db,
        )
    cards = da.list_job_cards(settings=tmp_db)
    f = da.QueueFilter(fraud_status="green")
    filtered = da.filter_cards(cards, f)
    for c in filtered:
        assert da._fraud_level(c) == "green"
    # And a red filter returns only the red one.
    f2 = da.QueueFilter(fraud_status="red")
    filtered2 = da.filter_cards(cards, f2)
    for c in filtered2:
        assert da._fraud_level(c) == "red"
    # Sanity: query_one is available from the repository layer.
    row = query_one("SELECT COUNT(*) AS n FROM scored_jobs", settings=tmp_db)
    assert row["n"] == 2


def test_sort_by_match_desc(tmp_db):
    cards = da.list_job_cards(settings=tmp_db)
    f = da.QueueFilter(sort="match_desc")
    filtered = da.filter_cards(cards, f)
    scores = [c.match_score for c in filtered]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------- #
# Status + authorization
# --------------------------------------------------------------------------- #
def test_set_status_and_authorize(tmp_db):
    """One-click status update + authorization persist to the DB."""
    # Seed a raw job + a scored job (scored_jobs has a FK to raw_jobs).
    da.execute_sql(
        "INSERT INTO raw_jobs (id, company, role_title) VALUES (?, ?, ?)",
        ("GL-TEST-1", "Acme", "Data Analyst"),
        settings=tmp_db,
    )
    da.execute_sql(
        "INSERT INTO scored_jobs (id, status) VALUES (?, 'pending')",
        ("GL-TEST-1",),
        settings=tmp_db,
    )
    assert da.is_authorized("GL-TEST-1", settings=tmp_db) is False

    da.set_status("GL-TEST-1", "applied", settings=tmp_db)
    row = da.query_one(
        "SELECT status FROM scored_jobs WHERE id = ?", ("GL-TEST-1",),
        settings=tmp_db,
    )
    assert row["status"] == "applied"

    da.authorize_job("GL-TEST-1", settings=tmp_db)
    assert da.is_authorized("GL-TEST-1", settings=tmp_db) is True
    row = da.query_one(
        "SELECT status FROM scored_jobs WHERE id = ?", ("GL-TEST-1",),
        settings=tmp_db,
    )
    assert row["status"] == "authorized"


def test_set_status_invalid_rejected(tmp_db):
    assert da.set_status("GL-NOPE", "bogus", settings=tmp_db) is False


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
def test_funnel_counts_empty(tmp_db):
    counts = da.funnel_counts(settings=tmp_db)
    assert counts["collected"] == 0
    assert counts["interviews"] == 0


def test_dry_market_signal_when_empty(tmp_db):
    signal = da.dry_market_signal(settings=tmp_db)
    assert signal["dry"] is True


def test_dry_market_signal_with_eligible(tmp_db):
    da.execute_sql(
        "INSERT INTO raw_jobs (id, company, role_title) VALUES (?, ?, ?)",
        ("GL-ELIG-1", "Acme", "Data Analyst"),
        settings=tmp_db,
    )
    da.execute_sql(
        "INSERT INTO scored_jobs (id, geo_eligible) VALUES (?, 1)",
        ("GL-ELIG-1",),
        settings=tmp_db,
    )
    signal = da.dry_market_signal(settings=tmp_db)
    assert signal["dry"] is False
    assert signal["eligible_jobs"] == 1


def test_company_leaderboard_empty(tmp_db):
    assert da.company_leaderboard(settings=tmp_db) == []


def test_followup_list_empty(tmp_db):
    assert da.followup_list(settings=tmp_db) == []


def test_calibration_data_empty(tmp_db):
    assert da.calibration_data(settings=tmp_db) == []


def test_top_levers_empty(tmp_db):
    assert da.top_levers(settings=tmp_db) == []


# --------------------------------------------------------------------------- #
# Application timeline & tracking dates
# --------------------------------------------------------------------------- #
def _seed_application(tmp_db, job_id="GL-APP-1", status="applied",
                      applied_at="2026-09-01T10:00:00+00:00",
                      last_updated="2026-09-05T10:00:00+00:00"):
    da.execute_sql(
        "INSERT INTO raw_jobs (id, company, role_title) VALUES (?, ?, ?)",
        (job_id, "Acme", "Data Analyst"),
        settings=tmp_db,
    )
    da.execute_sql(
        "INSERT INTO scored_jobs (id, status) VALUES (?, 'pending')",
        (job_id,),
        settings=tmp_db,
    )
    da.execute_sql(
        "INSERT INTO applications "
        "(job_id, status, applied_at, last_updated) VALUES (?, ?, ?, ?)",
        (job_id, status, applied_at, last_updated),
        settings=tmp_db,
    )


def test_application_timeline_empty(tmp_db):
    assert da.application_timeline(settings=tmp_db) == []


def test_application_timeline_populated(tmp_db):
    _seed_application(tmp_db, "GL-APP-1", status="interview",
                      applied_at="2026-09-01T10:00:00+00:00",
                      last_updated="2026-09-05T10:00:00+00:00")
    tl = da.application_timeline(settings=tmp_db)
    assert len(tl) == 1
    row = tl[0]
    assert row["company"] == "Acme"
    assert row["status"] == "interview"
    assert row["submitted_at"] == "2026-09-01T10:00:00+00:00"
    assert row["last_updated"] == "2026-09-05T10:00:00+00:00"


def test_status_breakdown(tmp_db):
    _seed_application(tmp_db, "GL-APP-1", status="applied")
    _seed_application(tmp_db, "GL-APP-2", status="interview")
    _seed_application(tmp_db, "GL-APP-3", status="interview")
    breakdown = da.status_breakdown(settings=tmp_db)
    by_status = {b["status"]: b["count"] for b in breakdown}
    assert by_status["interview"] == 2
    assert by_status["applied"] == 1


def test_company_application_summary(tmp_db):
    _seed_application(tmp_db, "GL-APP-1", status="interview")
    _seed_application(tmp_db, "GL-APP-2", status="applied")
    summary = da.company_application_summary(settings=tmp_db)
    assert len(summary) == 1
    assert summary[0]["applied"] == 2
    assert summary[0]["interviews"] == 1


def test_activity_by_date(tmp_db):
    # Seed a date well outside the 30-day window from "now" (2026-09-06).
    _seed_application(tmp_db, "GL-APP-1", applied_at="2026-01-01T10:00:00+00:00")
    activity = da.activity_by_date(settings=tmp_db, window_days=30)
    assert activity == []

    # A recent date falls inside the window.
    _seed_application(tmp_db, "GL-APP-2", applied_at="2026-09-05T10:00:00+00:00")
    activity = da.activity_by_date(settings=tmp_db, window_days=30)
    assert len(activity) == 1
    assert activity[0]["count"] == 1


def test_conversion_by_country(tmp_db):
    _seed_application(tmp_db, "GL-APP-1", status="interview")
    _seed_application(tmp_db, "GL-APP-2", status="applied")
    conv = da.conversion_by_country(settings=tmp_db)
    assert len(conv) == 1
    assert conv[0]["applied"] == 2
    assert conv[0]["interviews"] == 1
    assert conv[0]["rate"] == pytest.approx(50.0)


def test_application_age_report(tmp_db):
    _seed_application(tmp_db, "GL-APP-1", last_updated="2026-09-01T10:00:00+00:00")
    report = da.application_age_report(settings=tmp_db)
    assert len(report) == 1
    assert report[0]["age_days"] is not None
    assert report[0]["age_days"] >= 0


def test_job_card_tracking_dates(tmp_db):
    """JobCard surfaces authorized/applied/last-updated dates."""
    # Seed raw + scored first (authorized_jobs has a FK to scored_jobs).
    da.execute_sql(
        "INSERT INTO raw_jobs (id, company, role_title) VALUES (?, ?, ?)",
        ("GL-APP-1", "Acme", "Data Analyst"),
        settings=tmp_db,
    )
    da.execute_sql(
        "INSERT INTO scored_jobs (id, status) VALUES (?, 'pending')",
        ("GL-APP-1",),
        settings=tmp_db,
    )
    # Authorize before seeding the application: authorize_job() bumps status,
    # which would otherwise overwrite the seeded applications.last_updated.
    da.authorize_job("GL-APP-1", settings=tmp_db)
    da.execute_sql(
        "INSERT INTO applications "
        "(job_id, status, applied_at, last_updated) VALUES (?, ?, ?, ?)",
        ("GL-APP-1", "applied", "2026-09-01T10:00:00+00:00",
         "2026-09-05T10:00:00+00:00"),
        settings=tmp_db,
    )
    cards = da.list_job_cards(settings=tmp_db)
    assert len(cards) == 1
    card = cards[0]
    assert card.applied_at == "2026-09-01T10:00:00+00:00"
    assert card.last_updated == "2026-09-05T10:00:00+00:00"
    assert card.submitted_at == "2026-09-01T10:00:00+00:00"
    assert card.authorized_at is not None
