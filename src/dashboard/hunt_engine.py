"""
Hunt engine — the one-button pipeline.

Press "Run the hunt" and this runs the full autonomous chain, in order:

    1. Ingest      — scrape every target company for new postings
    2. Score       — geo-compliance filter + JD distillation
    3. Fraud       — anti-fraud scoring + company review
    4. Tailor      — build a tailored CV for each top match
    5. Rank        — return best-fit jobs, brutal-possibility first

Everything is local. No email, no job boards beyond the curated target list,
no data leaves the machine. The human still clicks submit.

`progress(label, detail)` is called after each major step so the UI can show a
live progress indicator while the engine runs.
"""
from __future__ import annotations

from typing import Callable, Optional

from src.db import repository
from src.dashboard import data_access as da
from src.ingestion.engine import IngestionEngine
from src.compliance.pipeline import CompliancePipeline
from src.fraud.pipeline import FraudPipeline
from src.resume.orchestrator import ResumeOrchestrator
from src.ingestion.deadline import sync_deadlines
from src.ingestion.enrichment import enrich_all


def run_hunt(progress: Optional[Callable[[str, str], None]] = None,
             resume_top_k: int = 5) -> list[da.JobCard]:
    """
    Run the full hunt pipeline and return the ranked list of JobCards
    (best fit first), each with a tailored resume where one was built.

    `progress(label, detail)` is invoked after each step for the UI.
    """

    def log(label: str, detail: str = "") -> None:
        if progress:
            try:
                progress(label, detail)
            except Exception:
                pass

    # 1. Ingest new postings from all target companies.
    log("Ingesting", "scraping target companies…")
    new = IngestionEngine().ingest_all(limit=50)
    log("Ingesting", f"{len(new)} new posting(s) found")

    # 1b. JD enrichment — fill empty descriptions from job pages.
    try:
        enr = enrich_all()
        log("Enriching", f"{enr['enriched']}/{enr['total']} JDs filled")
    except Exception as exc:
        log("Enriching", f"skipped: {exc}")

    # 1c. Deadline + freshness extraction (deterministic, no LLM).
    try:
        dl_ids = [r["id"] for r in repository.query_all("SELECT id FROM raw_jobs")]
        sync_deadlines(dl_ids)
        log("Deadlines", f"parsed {len(dl_ids)} posting(s)")
    except Exception as exc:
        log("Deadlines", f"skipped: {exc}")

    # 2. Score: geo-compliance + JD distillation.
    log("Scoring", "filtering + matching…")
    scored = CompliancePipeline().process()
    log("Scoring",
        f"{scored['accepted']} eligible · {scored['rejected']} rejected · "
        f"{scored['review']} need review")

    # 2b. Match scoring — compute the 0-100 match_score for every scored job.
    try:
        from src.scoring.matcher import MatchScorer
        scorer = MatchScorer()
        scored_rows = repository.query_all(
            "SELECT id, match_score FROM scored_jobs WHERE match_score IS NULL"
        )
        matched = 0
        for sr in scored_rows:
            job_id = sr["id"]
            jr = repository.query_one(
                "SELECT rj.role_title, rj.jd FROM raw_jobs rj WHERE rj.id = ?",
                (job_id,),
            )
            if not jr or not jr.get("jd"):
                continue
            distill = repository.query_one(
                "SELECT * FROM jd_distill WHERE job_id = ?", (job_id,)
            )
            m = scorer.score(jr.get("jd") or "", distill, jr.get("role_title") or "")
            repository.execute_sql(
                "UPDATE scored_jobs SET match_score = ? WHERE id = ?",
                (m.score, job_id),
            )
            matched += 1
        log("Matching", f"scored {matched} job(s)")
    except Exception as exc:
        log("Matching", f"skipped: {exc}")

    # 3. Fraud scoring + company review.
    log("Verifying", "checking companies for fraud…")
    fraud = FraudPipeline().process()
    log("Verifying",
        f"{fraud['green']} clean · {fraud['amber']} amber · {fraud['red']} red")

    # 4. Rank the eligible, fraud-clean jobs by brutal possibility.
    cards = da.list_job_cards()
    eligible = [
        c for c in cards
        if c.geo_eligible and (c.fraud_score is None or c.fraud_score >= 40)
    ]
    eligible.sort(key=lambda c: c.possibility_pct or 0.0, reverse=True)
    top = eligible[:max(resume_top_k, 1)]

    if not top:
        log("Done", "no eligible jobs this run")
        return sorted(cards, key=lambda c: c.possibility_pct or 0.0, reverse=True)

    # 5. Build tailored CVs for the top matches.
    log("Tailoring", "building CVs…")
    orch = ResumeOrchestrator()
    built = 0
    for card in top:
        if not card.jd:
            continue
        try:
            result = orch.build_for_job(card.jd, str(card.job_id))
            if result.pdf_path:
                repository.execute_sql(
                    "DELETE FROM resumes WHERE job_id = ?",
                    (str(card.job_id),),
                )
                repository.execute_sql(
                    "INSERT INTO resumes (job_id, pdf_path, resume_text, validated, generated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(card.job_id), result.pdf_path, None,
                     1 if result.all_validated else 0, repository.now_iso()),
                )
                built += 1
        except Exception as exc:
            log("Resume", f"failed for {card.company}: {exc}")
    log("Tailoring", f"{built} tailored CV(s) built")

    # Re-read so the freshly built resumes are attached to the cards.
    return da.list_job_cards()
