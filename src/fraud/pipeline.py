"""
Module 3 pipeline — Anti-Fraud + Company Review end-to-end.

Reads postings (joining raw_jobs with scored_jobs for geo data), runs the
deterministic fraud engine and company review, and writes fraud_score /
fraud_flags to scored_jobs.

Red postings are auto-rejected — they never reach the dashboard.
"""
from __future__ import annotations

from typing import Optional

from src.db import repository
from src.fraud.fraud_engine import FraudEngine
from src.fraud.company_review import CompanyReviewEngine


class FraudPipeline:
    """Runs fraud scoring + company review over postings."""

    def __init__(self, settings: Optional[dict] = None, min_salary: int = 30000):
        self.settings = settings or repository.load_settings()
        self.fraud = FraudEngine(min_salary=min_salary)
        self.company_review = CompanyReviewEngine()

    # ------------------------------------------------------------------ #
    def process(self, limit: Optional[int] = None) -> dict:
        """
        Score every posting for fraud and aggregate company reviews.

        Returns a summary dict: {processed, green, amber, red, rejected}.
        """
        sql = """
            SELECT r.*, s.geo_eligible, s.geo_confidence
            FROM raw_jobs r
            LEFT JOIN scored_jobs s ON s.id = r.id
            ORDER BY r.fetched_at DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = repository.query_all(sql, settings=self.settings)

        summary = {"processed": 0, "green": 0, "amber": 0, "red": 0, "rejected": 0}

        for row in rows:
            company = row.get("company") or ""
            domain = self._company_domain(company)
            result = self.fraud.evaluate(
                url=row.get("url") or "",
                jd=row.get("jd") or "",
                salary=row.get("salary"),
                company_domain=domain,
            )
            # Ensure a scored_jobs row exists before updating fraud fields.
            self._ensure_scored(row["id"])
            self._write_fraud(row["id"], result)

            # Accumulate company review signals.
            self.company_review.add_posting(
                company=company,
                domain=domain,
                has_contact=False,
                has_address=False,
                has_company_page=bool(domain),
                is_live=True,
            )

            summary["processed"] += 1
            if result.level == "green":
                summary["green"] += 1
            elif result.level == "amber":
                summary["amber"] += 1
            else:
                summary["red"] += 1
                # Auto-reject red postings.
                self._auto_reject(row["id"])

        # Aggregate company reviews.
        self._write_company_reviews()

        return summary

    # ------------------------------------------------------------------ #
    def _ensure_scored(self, job_id: str) -> None:
        """Insert a scored_jobs row if one does not already exist."""
        existing = repository.query_one(
            "SELECT 1 FROM scored_jobs WHERE id = ?", (job_id,),
            settings=self.settings,
        )
        if not existing:
            repository.execute_sql(
                """
                INSERT INTO scored_jobs (id, status) VALUES (?, 'pending')
                """,
                (job_id,),
                settings=self.settings,
            )

    # ------------------------------------------------------------------ #
    def _write_fraud(self, job_id: str, result) -> None:
        repository.execute_sql(
            """
            UPDATE scored_jobs
            SET fraud_score = ?, fraud_flags = ?
            WHERE id = ?
            """,
            (result.score, "; ".join(result.flags), job_id),
            settings=self.settings,
        )

    def _auto_reject(self, job_id: str) -> None:
        repository.execute_sql(
            "UPDATE scored_jobs SET status = 'rejected' WHERE id = ?",
            (job_id,),
            settings=self.settings,
        )

    def _write_company_reviews(self) -> None:
        reviews = self.company_review.all_reviews()
        for company, review in reviews.items():
            if not company:
                continue
            repository.execute_sql(
                """
                INSERT INTO company_reviews (company, legitimacy, activity, composite, level)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(company) DO UPDATE SET
                    legitimacy = excluded.legitimacy,
                    activity = excluded.activity,
                    composite = excluded.composite,
                    level = excluded.level
                """,
                (company, review.legitimacy_score, review.activity_score,
                 review.composite_score, review.level),
                settings=self.settings,
            )

    def _company_domain(self, company: str) -> str:
        """Best-effort known domain for a company name."""
        if not company:
            return ""
        # Strip common suffixes.
        name = company.lower()
        for suffix in (" inc", " inc.", " llc", " llc.", " corp", " corp.",
                       " limited", " gmbh", " ag", " ltd", " co", " com"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        return f"{name}.com"
