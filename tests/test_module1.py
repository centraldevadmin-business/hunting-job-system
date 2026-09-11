"""
Module 1 tests — Precision Targeting & Ingestion.

Verifies:
  - Posting normalization (id, canonical URL, host).
  - The fallback chain returns postings with confidence labels.
  - Re-running the chain does not create duplicates (dedup).
  - Postings are written to the raw_jobs table.
  - Manual-paste tier normalizes a pasted JD.

Network calls are mocked so the suite runs fast and deterministically.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingestion.posting import Posting, host_of
from src.ingestion.engine import IngestionEngine, _board_from_url


# --------------------------------------------------------------------------- #
# Posting normalization
# --------------------------------------------------------------------------- #
def test_posting_normalized_id_is_stable():
    p1 = Posting("Stripe", "Data Analyst", "https://stripe.com/jobs/search?gh_jid=1")
    p2 = Posting("Stripe", "Data Analyst", "https://stripe.com/jobs/search?gh_jid=1")
    assert p1.normalized_id == p2.normalized_id


def test_posting_different_jid_is_different_posting():
    p1 = Posting("Stripe", "Data Analyst", "https://stripe.com/jobs/search?gh_jid=1")
    p2 = Posting("Stripe", "Data Analyst", "https://stripe.com/jobs/search?gh_jid=99")
    assert p1.normalized_id != p2.normalized_id


def test_posting_url_normalization_ignores_query_and_trailing_slash():
    # Same gh_jid, different query order / trailing slash -> same posting.
    p1 = Posting("Stripe", "Data Analyst", "https://stripe.com/jobs/search?gh_jid=1")
    p2 = Posting("Stripe", "Data Analyst", "https://stripe.com/jobs/search/?gh_jid=1")
    assert p1.normalized_id == p2.normalized_id


def test_posting_host_of():
    assert host_of("https://stripe.com/jobs/search") == "stripe.com"


def test_posting_to_dict_roundtrip():
    p = Posting("Stripe", "Data Analyst", "https://stripe.com/jobs/search?gh_jid=1",
                jd="JD text", salary="$200k", location="Remote", confidence="high")
    d = p.to_dict()
    assert d["company"] == "Stripe"
    assert d["role_title"] == "Data Analyst"
    assert d["confidence"] == "high"


# --------------------------------------------------------------------------- #
# Board extraction
# --------------------------------------------------------------------------- #
def test_board_from_url_greenhouse():
    assert _board_from_url("https://boards-api.greenhouse.io/v1/boards/stripe/jobs") == "stripe"


def test_board_from_url_workable():
    assert _board_from_url("https://apply.workable.com/deel/") == "deel"


def test_board_from_url_default():
    assert _board_from_url("https://stripe.com/jobs/search") == "careers"


# --------------------------------------------------------------------------- #
# Fallback chain + dedup + DB write (network mocked)
# --------------------------------------------------------------------------- #
@pytest.fixture
def mock_db(tmp_path, monkeypatch):
    """Point the DB at a temp file and mock the Greenhouse target + config.

    We restrict the config to a single target (Stripe) so the suite does not
    make real network calls to the other 6 targets.
    """
    from src.db.repository import db_path
    monkeypatch.setattr("src.db.repository.db_path", lambda settings=None: tmp_path / "t.db")

    # Mock the Greenhouse target to return a controlled posting.
    from src.ingestion.targets import greenhouse_target as gh_mod

    def fake_fetch(self, limit=50):
        return [Posting(
            company="Stripe",
            role_title="Data Analyst",
            url="https://stripe.com/jobs/search?gh_jid=1",
            jd="JD text",
            salary="$200k",
            location="Remote",
            source="greenhouse",
            source_type="api",
            confidence="high",
        )]

    monkeypatch.setattr(gh_mod.GreenhouseTarget, "fetch", fake_fetch)

    # Restrict config to a single Stripe target.
    from src import config_loader
    fake_config = type("FakeConfig", (), {
        "targets": lambda self: [{
            "name": "Stripe",
            "domain": "stripe.com",
            "platform": "greenhouse",
            "url": "https://stripe.com/jobs/search",
            "board": "stripe",
            "role_keywords": ["Business Analyst", "Data Analyst", "Operations Analyst"],
        }],
    })()
    monkeypatch.setattr(config_loader, "load_config", lambda: fake_config)

    return tmp_path


def test_chain_returns_postings_with_confidence(mock_db):
    """For a working target, the chain returns postings with confidence labels."""
    eng = IngestionEngine()
    new = eng.ingest_all(limit=50)

    assert len(new) == 1
    p = new[0]
    assert p.confidence == "high"
    assert p.source == "greenhouse"
    assert p.source_type == "api"
    assert p.company == "Stripe"


def test_dedup_prevents_duplicates(mock_db):
    """Re-running the chain does not create duplicates."""
    from src.db.repository import query_all

    eng = IngestionEngine()
    first = eng.ingest_all(limit=50)
    second = eng.ingest_all(limit=50)

    # Second run writes nothing new.
    assert len(second) == 0

    # raw_jobs has exactly the count from the first run.
    rows = query_all("SELECT id FROM raw_jobs")
    assert len(rows) == len(first)

    # No duplicate ids.
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))


def test_postings_written_to_raw_jobs(mock_db):
    """Postings are persisted to the raw_jobs table."""
    from src.db.repository import query_one

    eng = IngestionEngine()
    eng.ingest_all(limit=50)

    row = query_one("SELECT company, role_title, source, source_type FROM raw_jobs")
    assert row is not None
    assert row["company"] == "Stripe"
    assert row["role_title"] == "Data Analyst"
    assert row["source"] == "greenhouse"
    assert row["source_type"] == "api"


def test_failed_target_does_not_stop_chain(mock_db, monkeypatch):
    """A failing target logs a warning but the chain continues."""
    from src.ingestion.targets import greenhouse_target as gh_mod

    def failing_fetch(self, limit=50):
        raise RuntimeError("boom")

    monkeypatch.setattr(gh_mod.GreenhouseTarget, "fetch", failing_fetch)

    eng = IngestionEngine()
    # Should not raise even though the target fails.
    new = eng.ingest_all(limit=50)
    assert len(new) == 0


# --------------------------------------------------------------------------- #
# Tier 4: manual paste
# --------------------------------------------------------------------------- #
def test_manual_paste_target():
    """Tier 4: manual paste normalizes a pasted JD."""
    from src.ingestion.targets.manual_paste_target import ManualPasteTarget
    t = ManualPasteTarget("Stripe", "stripe.com", ["Business Analyst", "Data Analyst"])
    postings = t.fetch(
        "Business Analyst, Payments\n"
        "Remote - Worldwide\n"
        "We need someone who can analyze data. Salary $180,000/year.\n"
    )
    assert len(postings) == 1
    p = postings[0]
    assert p.role_title == "Business Analyst, Payments"
    assert p.source == "manual"
    assert p.source_type == "paste"
    assert p.confidence == "low"
    assert "180,000" in (p.salary or "")
    assert "Remote" in (p.location or "")


def test_manual_paste_filters_non_matching_title():
    """Tier 4: a non-analyst title is filtered out."""
    from src.ingestion.targets.manual_paste_target import ManualPasteTarget
    t = ManualPasteTarget("Stripe", "stripe.com", ["Business Analyst", "Data Analyst"])
    postings = t.fetch("Account Executive, AI Sales\nRemote\n")
    assert len(postings) == 0
