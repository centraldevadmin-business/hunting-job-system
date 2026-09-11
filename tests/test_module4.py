"""
Module 4 tests — Zero-Hallucination Resume Synthesizer.

DoD: Given a master record and a test JD, the module emits a validated PDF
where every bullet traces to a source achievement id, and a deliberately
fabricated metric is rejected 100% of the time on test cases.
"""
import os

import pytest

from src.resume.master import (
    MasterRecord, Employment, Achievement, Skill, Education,
)
from src.resume.validator import Validator, validate_resume
from src.resume.integrity import IntegrityChecker, check_integrity
from src.resume.crosscheck import run_cross_checks, check_overlaps, check_gaps
from src.resume.variants import VariantGenerator
from src.resume.generator import ResumeGenerator, EmittedBullet
from src.resume.orchestrator import ResumeOrchestrator
from src.resume.master import load_master_record


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seed_test_master():
    """Seed a small master record into the (temp) database."""
    from src.db.repository import execute_sql, query_one

    if query_one("SELECT id FROM career_profile LIMIT 1"):
        return

    execute_sql(
        "INSERT INTO career_profile (full_name, email, phone, location) VALUES (?, ?, ?, ?)",
        ("Nafiz Mahfuz", "nafiz@example.com", "+8801877014405", "Dhaka, Bangladesh"),
    )
    execute_sql(
        "INSERT INTO employment (company, role, start_date, end_date, current, location) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Betopia Limited", "Business Analyst", "02/2024", None, 1, "Dhaka, Bangladesh"),
    )
    emp_id = query_one("SELECT id FROM employment")["id"]
    execute_sql(
        "INSERT INTO achievement (employment_id, bullet, tools, metric) VALUES (?, ?, ?, ?)",
        (emp_id, "Reduced cost per unit by 12% using SQL and Power BI", "SQL, Power BI", "12%"),
    )
    execute_sql(
        "INSERT INTO achievement (employment_id, bullet, tools, metric) VALUES (?, ?, ?, ?)",
        (emp_id, "Built a Random Forest no-show model achieving 82% accuracy", "Python, Scikit-learn", "82%"),
    )
    for name, level in [("SQL", "Advanced"), ("Power BI", "Intermediate"), ("Python", "Advanced")]:
        execute_sql("INSERT OR IGNORE INTO skill (name, level) VALUES (?, ?)", (name, level))
    execute_sql(
        "INSERT INTO education (institution, degree, year, verified) VALUES (?, ?, ?, ?)",
        ("BRAC University", "B.Sc. CSE", 2024, 1),
    )


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def master() -> MasterRecord:
    """A small but realistic master record with known facts."""
    emp = Employment(
        id=1, company="Betopia Limited", role="Business Analyst",
        start_date="02/2024", end_date=None, current=True, location="Dhaka, Bangladesh",
    )
    ach = Achievement(
        id=1, employment_id=1, company="Betopia Limited", role="Business Analyst",
        bullet="Reduced cost per unit by 12% using SQL and Power BI dashboards",
        tools="SQL, Power BI", metric="12%",
    )
    return MasterRecord(
        profile={"full_name": "Nafiz Mahfuz", "email": "nafiz@example.com",
                 "location": "Dhaka, Bangladesh"},
        employments=[emp],
        achievements=[ach],
        skills=[Skill(name="SQL", level="Advanced"), Skill(name="Power BI", level="Intermediate")],
        education=[Education(id=1, institution="BRAC University", degree="B.Sc. CSE", year=2024, verified=1)],
    )


# --------------------------------------------------------------------------- #
# Validator — the hard gate
# --------------------------------------------------------------------------- #
def test_validator_accepts_authentic_bullet(master):
    """An authentic bullet (from the record) passes."""
    result = validate_resume(
        "Reduced cost per unit by 12% using SQL and Power BI",
        master,
    )
    assert result.valid, f"Authentic bullet wrongly rejected: {result.violations}"


def test_validator_rejects_fabricated_metric(master):
    """A fabricated metric is rejected 100% of the time."""
    fabricated = "Reduced cost per unit by 87% using Python and TensorFlow"
    result = validate_resume(fabricated, master)
    assert not result.valid
    assert "87%" in result.rejected_tokens, "Fabricated metric '87%' must be flagged"


def test_validator_rejects_fabricated_tool(master):
    """A fabricated tool is rejected."""
    fabricated = "Reduced cost per unit by 12% using SQL and Kubernetes"
    result = validate_resume(fabricated, master)
    assert not result.valid
    assert "kubernetes" in result.rejected_tokens


def test_validator_rejects_fabricated_company(master):
    """A fabricated company is rejected."""
    fabricated = "Led a team at Google to reduce cost by 12%"
    result = validate_resume(fabricated, master)
    assert not result.valid
    assert "google" in result.rejected_tokens


# --------------------------------------------------------------------------- #
# Integrity — the distortion gate
# --------------------------------------------------------------------------- #
def test_integrity_flags_ownership_escalation():
    """'Contributed to' upgraded to 'led' is flagged."""
    ach = Achievement(1, 1, "Walton", "Intern",
                       "Contributed to a slow-moving stock analysis", "Excel", "50,000")
    result = check_integrity("Led a slow-moving stock analysis", ach)
    assert not result.clean
    assert any("ownership" in f.lower() for f in result.flags)


def test_integrity_flags_metric_distortion():
    """A changed metric is flagged."""
    ach = Achievement(1, 1, "Betopia", "BA",
                       "Reduced cost per unit by 12%", "SQL", "12%")
    result = check_integrity("Reduced cost per unit by 25%", ach)
    assert not result.clean
    assert any("metric" in f.lower() for f in result.flags)


def test_integrity_clean_bullet_passes():
    """A faithful rewrite passes."""
    ach = Achievement(1, 1, "Betopia", "BA",
                       "Reduced cost per unit by 12% using SQL", "SQL", "12%")
    result = check_integrity("Cut cost per unit 12% with SQL", ach)
    assert result.clean


# --------------------------------------------------------------------------- #
# Cross-checks
# --------------------------------------------------------------------------- #
def test_crosscheck_detects_overlap():
    """Two overlapping non-current employments are flagged."""
    emps = [
        Employment(1, "A", "BA", "01/2023", "06/2023", False, "Dhaka"),
        Employment(2, "B", "BA", "03/2023", "12/2023", False, "Dhaka"),
    ]
    warnings = check_overlaps(emps)
    assert len(warnings) >= 1


def test_crosscheck_detects_gap():
    """A large gap between employments is flagged."""
    emps = [
        Employment(1, "A", "BA", "01/2020", "01/2021", False, "Dhaka"),
        Employment(2, "B", "BA", "01/2023", "12/2023", False, "Dhaka"),
    ]
    warnings = check_gaps(emps, max_gap_months=6)
    assert len(warnings) >= 1


def test_crosscheck_clean_record():
    """A clean record produces no warnings."""
    emp = Employment(1, "Betopia", "BA", "02/2024", None, True, "Dhaka")
    ach = Achievement(1, 1, "Betopia", "BA",
                      "Reduced cost per unit by 12%", "SQL", "12%")
    master = MasterRecord(
        profile={"full_name": "Nafiz"}, employments=[emp],
        achievements=[ach], skills=[], education=[],
    )
    report = run_cross_checks(master)
    assert report.clean


# --------------------------------------------------------------------------- #
# Variants
# --------------------------------------------------------------------------- #
def test_variant_generator_emits_three():
    """Three deterministic variants are produced from validated bullets."""
    master = MasterRecord(
        profile={"full_name": "Nafiz"},
        employments=[Employment(1, "Betopia", "BA", "02/2024", None, True, "Dhaka")],
        achievements=[Achievement(1, 1, "Betopia", "BA",
                                  "Cut cost per unit 12% with SQL", "SQL", "12%")],
        skills=[Skill("SQL", "Advanced")], education=[],
    )
    gen = ResumeGenerator(master)
    bullets = [
        EmittedBullet("Cut cost per unit 12% with SQL", 1,
                      "Cut cost per unit 12% with SQL", validated=True, integrity_clean=True),
    ]
    variants = VariantGenerator(master).generate(bullets, top_k=3)
    names = {v.name for v in variants}
    assert {"skills-first", "impact-first", "chronological"}.issubset(names)


# --------------------------------------------------------------------------- #
# End-to-end orchestrator — the Module 4 DoD
# --------------------------------------------------------------------------- #
def test_orchestrator_emits_validated_pdf(tmp_path, monkeypatch):
    """
    Given the master record and a test JD, the orchestrator emits a validated
    PDF where every bullet traces to a source achievement id.
    """
    monkeypatch.setattr("src.db.repository.db_path", lambda settings=None: tmp_path / "career.db")
    load_master_record()  # ensure schema exists

    # Seed a master record so the orchestrator has facts to work with.
    _seed_test_master()

    orch = ResumeOrchestrator()
    jd = "Business Analyst needed in SQL, Power BI, Python to reduce operational costs."
    result = orch.build_for_job(jd, "TEST-1")

    assert result.all_validated, f"Expected all validated, got warnings: {result.warnings}"
    assert result.pdf_path is not None
    assert os.path.exists(result.pdf_path)
    # Every emitted bullet traces to a source achievement id.
    for b in result.variants[0].bullets:
        assert b.source_achievement_id is not None
