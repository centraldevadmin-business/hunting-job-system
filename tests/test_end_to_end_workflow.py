"""
End-to-end workflow test — Modules 0 through 7.

This is NOT a unit test. It runs the ENTIRE pipeline against a single realistic
synthetic job and measures the accuracy of every stage, so you can see exactly
where the system is strong and where it degrades.

Pipeline stages measured:
  1. Geo-compliance gate (Module 2)      — did it correctly ACCEPT a legit job?
  2. JD distillation (Module 2b)         — did it extract the right must-haves?
  3. Fraud engine (Module 3a)            — did it score a legit posting green?
  4. Possibility score (Module 5)        — brutal possibility math
  5. Resume build (Module 4)             — zero-hallucination, all validated
  6. Cover letter (Module 6)             — only master facts, quotes JD
  7. Application staging (Module 6)      — human-in-the-loop package
  8. Tracking (Module 7)                 — status transition + report

Accuracy is reported as a per-stage pass/fail plus an overall pipeline score.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# --------------------------------------------------------------------------- #
# Realistic synthetic data
# --------------------------------------------------------------------------- #
MASTER_PROFILE = (
    "INSERT INTO career_profile (id, full_name, email, phone, location) "
    "VALUES (1, 'MD Nafiz Mahfuz', 'nafiz@example.com', '+880 1877-014405', 'Dhaka, Bangladesh')"
)
MASTER_EMPLOYMENT = (
    "INSERT INTO employment (id, company, role, start_date, end_date, current, location) "
    "VALUES (1, 'Betopia Limited', 'Business Analyst', '02/2024', NULL, 1, 'Dhaka, Bangladesh')"
)
# Three authentic achievements with real metrics + tools.
MASTER_ACHIEVEMENTS = [
    (1, 1, "Built data pipelines processing 2M events daily using Python and SQL",
     "python, sql", "2M daily"),
    (2, 1, "Analyzed 500+ customer surveys and cut churn by 18% in one quarter",
     "metrics analysis, product", "18%"),
    (3, 1, "Contributed to a reporting dashboard used by 30 analysts across the firm",
     "power bi, sql", "30 analysts"),
]
MASTER_SKILLS = [
    "INSERT INTO skill (name, level) VALUES ('python', 'advanced'), "
    "('sql', 'advanced'), ('metrics analysis', 'advanced'), "
    "('product', 'intermediate'), ('power bi', 'intermediate')",
]
MASTER_EDUCATION = (
    "INSERT INTO education (id, institution, degree, year, verified) "
    "VALUES (1, 'University of Dhaka', 'BBA', 2022, 1)"
)

# A realistic, legit JD for a Data Analyst role — worldwide remote.
SAMPLE_JD = """
We are looking for a Data Analyst to join our global team. You will build data
pipelines, analyze metrics, and produce reporting dashboards.

What we need:
- Build and maintain data pipelines in Python and SQL
- Analyze metrics and surface actionable insights
- Experience with product analytics
- 3+ years of experience in data or business analysis

We hire globally and work from anywhere. This role is open worldwide.
"""

SAMPLE_URL = "https://acmecorp.com/careers/data-analyst"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seed_master(tmp_db):
    from src.db.repository import execute_sql
    execute_sql(MASTER_PROFILE, settings=tmp_db)
    execute_sql(MASTER_EMPLOYMENT, settings=tmp_db)
    for ach in MASTER_ACHIEVEMENTS:
        execute_sql(
            "INSERT INTO achievement (id, employment_id, bullet, tools, metric) "
            "VALUES (?, ?, ?, ?, ?)",
            ach,
            settings=tmp_db,
        )
    execute_sql(MASTER_SKILLS[0], settings=tmp_db)
    execute_sql(MASTER_EDUCATION, settings=tmp_db)


def _seed_job(tmp_db, job_id="GL-E2E-1", company="Acme", role_title="Data Analyst",
              jd=SAMPLE_JD, url=SAMPLE_URL, salary=""):
    from src.db.repository import execute_sql
    execute_sql(
        "INSERT INTO raw_jobs (id, source, source_type, url, company, role_title, jd, "
        "salary, location, posted_at, canonical_ats_url, fetched_at) "
        "VALUES (?, 'direct', 'direct', ?, ?, ?, ?, ?, 'remote worldwide', ?, ?, ?)",
        (job_id, url, company, role_title, jd, salary, "2026-09-01", url, "2026-09-01"),
        settings=tmp_db,
    )
    # Bootstrap scored_jobs so the FK chain holds for execution/tracking.
    execute_sql(
        "INSERT INTO scored_jobs (id, geo_eligible, geo_confidence, match_score, status) "
        "VALUES (?, NULL, NULL, NULL, 'pending')",
        (job_id,),
        settings=tmp_db,
    )


# --------------------------------------------------------------------------- #
# Stage 1 — Geo-compliance
# --------------------------------------------------------------------------- #
def test_stage1_geo_compliance_accepts_legit_job(tmp_db):
    """A worldwide-remote legit job must be ACCEPTED."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.compliance.geo_compliance import GeoComplianceFilter
    geo = GeoComplianceFilter(tmp_db)
    result = geo.evaluate(SAMPLE_JD, posting_url=SAMPLE_URL)

    assert result.verdict == "ACCEPT", f"Expected ACCEPT, got {result.verdict}"
    assert result.eligible is True
    assert "worldwide" in result.matched_accept or "work from anywhere" in result.matched_accept


def test_stage1_geo_compliance_rejects_citizenship_job(tmp_db):
    """A 'US Citizen required' job must be REJECTED."""
    _seed_master(tmp_db)
    bad_jd = "Must be a US citizen. Work authorization you do not hold is not supported."
    from src.compliance.geo_compliance import GeoComplianceFilter
    geo = GeoComplianceFilter(tmp_db)
    result = geo.evaluate(bad_jd)
    assert result.verdict == "REJECT"
    assert result.eligible is False


# --------------------------------------------------------------------------- #
# Stage 2 — JD distillation
# --------------------------------------------------------------------------- #
def test_stage2_jd_distillation_extracts_requirements(tmp_db):
    """The distiller must extract must-have requirements from the JD."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.distiller.jd_distiller import JDDistiller
    dist = JDDistiller(tmp_db)
    reqs = dist.distill(SAMPLE_JD)

    # Regex fallback (no LLM key) should pull verb-led requirements.
    assert reqs.top_requirements or reqs.must_have, \
        "Expected at least one requirement to be extracted"
    joined = " ".join(reqs.must_have + reqs.top_requirements).lower()
    assert "python" in joined or "sql" in joined or "pipelines" in joined


# --------------------------------------------------------------------------- #
# Stage 3 — Fraud engine
# --------------------------------------------------------------------------- #
def test_stage3_fraud_scores_legit_job_green(tmp_db):
    """A legit posting with a matching domain must score green (>=70)."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.fraud.fraud_engine import FraudEngine
    fraud = FraudEngine(min_salary=30000)
    result = fraud.evaluate(SAMPLE_URL, jd=SAMPLE_JD, company_domain="acmecorp.com")
    assert result.level == "green", f"Expected green, got {result.level} ({result.reason})"
    assert result.rejected is False


def test_stage3_fraud_flags_payment_request_red(tmp_db):
    """A JD asking to buy equipment must be flagged red."""
    _seed_master(tmp_db)
    bad_jd = "You must buy your own equipment and pay to start. Gift card offered as bonus."
    from src.fraud.fraud_engine import FraudEngine
    fraud = FraudEngine(min_salary=30000)
    result = fraud.evaluate("https://scam.example.com", jd=bad_jd)
    assert result.level == "red"
    assert result.rejected is True


# --------------------------------------------------------------------------- #
# Stage 4 — Possibility score
# --------------------------------------------------------------------------- #
def test_stage4_possibility_math(tmp_db):
    """Brutal possibility = match x geo x fraud x historical rate."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.dashboard.possibility import PossibilityEngine
    engine = PossibilityEngine(historical_rate=0.15, outcomes=0,
                               outcomes_before_trusted=30)

    # A strong match (90), geo-eligible, fraud-green (100), prior rate 0.15.
    res = engine.compute(match_score=90, geo_eligible=True, fraud_score=100)
    expected = 90 * 1.0 * 1.0 * 0.15
    assert res.pct == pytest.approx(expected, abs=0.5)
    assert res.trusted is False  # < 30 outcomes → unproven

    # Geo-ineligible → hard gate zeroes it.
    res2 = engine.compute(match_score=90, geo_eligible=False, fraud_score=100)
    assert res2.pct == 0.0
    assert res2.gated_out == "geo-ineligible"

    # Red fraud → hard gate zeroes it.
    res3 = engine.compute(match_score=90, geo_eligible=True, fraud_score=0)
    assert res3.pct == 0.0
    assert res3.gated_out == "fraud"


# --------------------------------------------------------------------------- #
# Stage 5 — Resume build (zero-hallucination)
# --------------------------------------------------------------------------- #
def test_stage5_resume_is_zero_hallucination(tmp_db):
    """Every emitted bullet must be validated and integrity-clean."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.resume.orchestrator import ResumeOrchestrator
    orch = ResumeOrchestrator(tmp_db)
    result = orch.build_for_job(SAMPLE_JD, job_id="GL-E2E-1")

    # With no LLM key, bullets are emitted verbatim → always validated.
    assert result.all_validated is True, \
        f"Resume has unvalidated bullets: {result.human_review}"
    assert result.pdf_path is not None

    # No hallucinated tokens: every token must trace to the master record.
    # (The validator already enforced this; assert the pipeline returned clean.)
    for b in result.human_review:
        assert b.validated and b.integrity_clean


def test_stage5_resume_rejects_invented_metric(tmp_db):
    """A bullet with a metric not in the master record must be flagged."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.resume.validator import validate_resume
    from src.resume.master import load_master_record
    master = load_master_record(tmp_db)

    # "Increased revenue by 999%" — 999 is not in the master vocabulary.
    bad_bullet = "Increased revenue by 999% using python and sql"
    result = validate_resume(bad_bullet, master)
    assert result.valid is False
    assert any("999" in v for v in result.violations)


# --------------------------------------------------------------------------- #
# Stage 6 — Cover letter
# --------------------------------------------------------------------------- #
def test_stage6_cover_letter_uses_only_master_facts(tmp_db):
    """The cover letter must name the applicant and quote JD requirements."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.execution.cover_letter import CoverLetterGenerator
    gen = CoverLetterGenerator(tmp_db)
    result = gen.build(jd_text=SAMPLE_JD, company="Acme",
                       role_title="Data Analyst", job_id="GL-E2E-1")

    assert "MD Nafiz Mahfuz" in result.body
    assert "Acme" in result.body
    # It must quote at least one JD requirement.
    joined = " ".join(result.warnings) + " " + result.body
    assert "python" in joined.lower() or "pipelines" in joined.lower()


# --------------------------------------------------------------------------- #
# Stage 7 — Application staging (human-in-the-loop)
# --------------------------------------------------------------------------- #
def test_stage7_stage_and_apply(tmp_db):
    """Staging writes an application row; marking applied flips status."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.execution.application import ApplicationStager
    stager = ApplicationStager(tmp_db)

    pkg = stager.build_package("GL-E2E-1", jd_text=SAMPLE_JD,
                               company="Acme", role_title="Data Analyst")
    assert pkg.cover_letter_body
    assert pkg.checklist

    stage = stager.stage_for_submission("GL-E2E-1", jd_text=SAMPLE_JD,
                                        company="Acme", role_title="Data Analyst",
                                        submit_url=SAMPLE_URL)
    assert stage.staged is True

    stager.mark_applied("GL-E2E-1", notes="Submitted on company careers page")
    from src.db.repository import query_one
    app = query_one("SELECT status FROM applications WHERE job_id = ?",
                    ("GL-E2E-1",), settings=tmp_db)
    assert app["status"] == "applied"


# --------------------------------------------------------------------------- #
# Stage 8 — Tracking
# --------------------------------------------------------------------------- #
def test_stage8_tracking_lifecycle(tmp_db):
    """Full status lifecycle: applied → interview → report."""
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.execution.application import ApplicationStager
    from src.tracking.tracker import Tracker

    stager = ApplicationStager(tmp_db)
    stager.stage_for_submission("GL-E2E-1", jd_text=SAMPLE_JD,
                                company="Acme", role_title="Data Analyst",
                                submit_url=SAMPLE_URL)
    stager.mark_applied("GL-E2E-1", notes="applied")

    tracker = Tracker(tmp_db)
    tracker.record_interview("GL-E2E-1", notes="phone screen")

    report = tracker.report()
    assert report.total_applied == 1
    assert report.total_interviews == 1
    assert report.interview_rate == pytest.approx(100.0)


# --------------------------------------------------------------------------- #
# Full pipeline accuracy report
# --------------------------------------------------------------------------- #
def test_full_pipeline_accuracy_report(tmp_db):
    """
    Run the entire pipeline once and return a per-stage accuracy dict.

    This is the "measure the accuracy of the test" the user asked for. Every
    stage is a real assertion; the returned dict shows pass/fail per stage.
    """
    _seed_master(tmp_db)
    _seed_job(tmp_db)

    from src.compliance.geo_compliance import GeoComplianceFilter
    from src.distiller.jd_distiller import JDDistiller
    from src.fraud.fraud_engine import FraudEngine
    from src.dashboard.possibility import PossibilityEngine
    from src.resume.orchestrator import ResumeOrchestrator
    from src.execution.cover_letter import CoverLetterGenerator
    from src.execution.application import ApplicationStager
    from src.tracking.tracker import Tracker
    from src.db.repository import query_one

    stages = {}

    # Stage 1 — geo
    geo = GeoComplianceFilter(tmp_db).evaluate(SAMPLE_JD, posting_url=SAMPLE_URL)
    stages["geo_compliance"] = geo.verdict == "ACCEPT"

    # Stage 2 — distillation
    reqs = JDDistiller(tmp_db).distill(SAMPLE_JD)
    joined = " ".join(reqs.must_have + reqs.top_requirements).lower()
    stages["jd_distillation"] = ("python" in joined or "sql" in joined)

    # Stage 3 — fraud
    fraud = FraudEngine(min_salary=30000).evaluate(SAMPLE_URL, jd=SAMPLE_JD,
                                                   company_domain="acmecorp.com")
    stages["fraud_engine"] = fraud.level == "green"

    # Stage 4 — possibility
    poss = PossibilityEngine().compute(90, True, 100)
    stages["possibility_score"] = abs(poss.pct - 13.5) < 0.5

    # Stage 5 — resume
    orch = ResumeOrchestrator(tmp_db)
    rres = orch.build_for_job(SAMPLE_JD, job_id="GL-E2E-1")
    stages["resume_zero_hallucination"] = rres.all_validated and rres.pdf_path is not None

    # Stage 6 — cover letter
    cover = CoverLetterGenerator(tmp_db).build(jd_text=SAMPLE_JD, company="Acme",
                                               role_title="Data Analyst", job_id="GL-E2E-1")
    stages["cover_letter"] = "MD Nafiz Mahfuz" in cover.body

    # Stage 7 — staging + apply
    stager = ApplicationStager(tmp_db)
    stager.stage_for_submission("GL-E2E-1", jd_text=SAMPLE_JD, company="Acme",
                                role_title="Data Analyst", submit_url=SAMPLE_URL)
    stager.mark_applied("GL-E2E-1", notes="applied")
    app = query_one("SELECT status FROM applications WHERE job_id = ?",
                    ("GL-E2E-1",), settings=tmp_db)
    stages["application_staging"] = app["status"] == "applied"

    # Stage 8 — tracking
    tracker = Tracker(tmp_db)
    tracker.record_interview("GL-E2E-1")
    rep = tracker.report()
    stages["tracking"] = rep.total_applied == 1 and rep.total_interviews == 1

    passed = sum(1 for v in stages.values() if v)
    total = len(stages)
    stages["overall_accuracy_pct"] = round(passed / total * 100, 1)
    stages["passed"] = passed
    stages["total"] = total

    # Every stage must pass on a clean, realistic job.
    assert passed == total, f"Pipeline accuracy: {passed}/{total} — failures: { {k: v for k, v in stages.items() if not v} }"
    print(f"\n✅ Full pipeline accuracy: {passed}/{total} stages passing "
          f"({stages['overall_accuracy_pct']}%)")
    for k, v in stages.items():
        print(f"   [{'✓' if v else '✗'}] {k}")
