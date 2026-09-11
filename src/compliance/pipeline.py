"""
Module 2 pipeline — Geo-Compliance + JD Distiller end-to-end.

Reads unfiltered postings from `raw_jobs`, runs the deterministic
geo-compliance filter and the JD distiller, and writes results to
`scored_jobs` and `jd_distill`.

This is the deterministic core of Module 2. The LLM (Grok) is used only
for the distiller, and only as an accelerator — the regex fallback keeps
the pipeline working with zero cost and no API key.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.db import repository
from src.compliance.geo_compliance import GeoComplianceFilter
from src.distiller.jd_distiller import JDDistiller


class CompliancePipeline:
    """Runs geo-compliance + distillation over raw postings."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or repository.load_settings()
        self.geo = GeoComplianceFilter(self.settings)
        self.distiller = JDDistiller(self.settings)

    # ------------------------------------------------------------------ #
    def process(self, limit: Optional[int] = None) -> dict:
        """
        Process all raw postings (or up to `limit`).

        Returns a summary dict: {processed, accepted, rejected, review,
        llm_distilled, regex_distilled}.
        """
        sql = "SELECT * FROM raw_jobs ORDER BY fetched_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = repository.query_all(sql, settings=self.settings)

        summary = {
            "processed": 0,
            "accepted": 0,
            "rejected": 0,
            "review": 0,
            "llm_distilled": 0,
            "regex_distilled": 0,
        }

        for row in rows:
            self._process_one(row, summary)

        return summary

    def _process_one(self, row: dict, summary: dict) -> None:
        job_id = row["id"]
        jd = row.get("jd") or ""
        url = row.get("url") or ""

        # 1. Geo-compliance.
        geo = self.geo.evaluate(jd, company_domain="", posting_url=url)

        # 2. JD distillation.
        reqs = self.distiller.distill(jd)

        # 3. Write scored_jobs.
        self._write_scored(job_id, geo)

        # 4. Write jd_distill.
        self._write_distill(job_id, reqs)

        summary["processed"] += 1
        if geo.verdict == "ACCEPT":
            summary["accepted"] += 1
        elif geo.verdict == "REJECT":
            summary["rejected"] += 1
        else:
            summary["review"] += 1
        if reqs.source == "llm":
            summary["llm_distilled"] += 1
        else:
            summary["regex_distilled"] += 1

    # ------------------------------------------------------------------ #
    def _write_scored(self, job_id: str, geo) -> None:
        repository.execute_sql(
            """
            INSERT INTO scored_jobs
                (id, geo_eligible, geo_confidence, match_score, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                geo_eligible = excluded.geo_eligible,
                geo_confidence = excluded.geo_confidence,
                status = excluded.status
            """,
            (
                job_id,
                1 if geo.verdict == "ACCEPT" else 0,
                geo.confidence,
                None,                       # match_score filled by Module 3+
                "accepted" if geo.verdict == "ACCEPT"
                else "rejected" if geo.verdict == "REJECT"
                else "review",
            ),
            settings=self.settings,
        )

    def _write_distill(self, job_id: str, reqs) -> None:
        row = reqs.to_row()
        repository.execute_sql(
            """
            INSERT INTO jd_distill
                (job_id, top_requirements, must_have, nice_to_have,
                 red_flags, actual_seniority, distilled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                top_requirements = excluded.top_requirements,
                must_have = excluded.must_have,
                nice_to_have = excluded.nice_to_have,
                red_flags = excluded.red_flags,
                actual_seniority = excluded.actual_seniority,
                distilled_at = excluded.distilled_at
            """,
            (
                job_id,
                row["top_requirements"],
                row["must_have"],
                row["nice_to_have"],
                row["red_flags"],
                row["actual_seniority"],
                datetime.now(timezone.utc).isoformat(),
            ),
            settings=self.settings,
        )
