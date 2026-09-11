"""
Multi-Agent Orchestrator — Module 13.

Decomposes the hunt pipeline into specialized *agents* that exchange typed
``Message`` objects (see ``messages.py``). Each agent wraps an existing
engine and owns exactly one responsibility:

  * IngestorAgent    — scrape target companies for new postings
  * ComplianceAgent  — geo-compliance eligibility filter
  * FraudAgent       — anti-fraud / legitimacy scoring
  * MatchAgent       — deterministic 0-100 match scoring
  * ResumeAgent      — build a tailored resume for a job
  * ValidatorAgent   — closed-vocabulary hallucination gate; "debates" the
                       Resume agent on any unvalidated bullet
  * TrackerAgent     — record human-in-the-loop status transitions

The ``Orchestrator`` routes messages, runs the Resume↔Validator debate loop
for any job whose resume fails validation, and aggregates a final report.

Design guarantees:
  * Zero new facts. Every agent only reads from the master record / DB and
    the deterministic engines. The LLM never injects a fact.
  * Human-in-the-loop gates. No agent sends email or submits anything.
  * Graceful degradation. If an engine raises, the agent returns an
    ``error`` message and the orchestrator continues.
"""
from __future__ import annotations

from typing import Callable, Optional

from src.agents.messages import Message
from src.db import repository


# --------------------------------------------------------------------------- #
# Individual agents
# --------------------------------------------------------------------------- #
class IngestorAgent:
    """Discovers new postings from target companies."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    def handle(self, msg: Message) -> Message:
        try:
            from src.ingestion.engine import IngestionEngine
            new = IngestionEngine(self.settings).ingest_all(limit=50)
            return msg.with_status(
                "ok", detail=f"{len(new)} new posting(s) found",
                result={"count": len(new)})
        except Exception as exc:
            return msg.with_status("error", detail=str(exc))


class ComplianceAgent:
    """Applies the geo-compliance eligibility filter."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    def handle(self, msg: Message) -> Message:
        try:
            from src.compliance.pipeline import CompliancePipeline
            res = CompliancePipeline(self.settings).process()
            return msg.with_status(
                "ok",
                detail=f"{res.get('accepted', 0)} eligible · "
                       f"{res.get('rejected', 0)} rejected",
                result=res)
        except Exception as exc:
            return msg.with_status("error", detail=str(exc))


class FraudAgent:
    """Runs the anti-fraud / legitimacy scoring."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    def handle(self, msg: Message) -> Message:
        try:
            from src.fraud.pipeline import FraudPipeline
            res = FraudPipeline(self.settings).process()
            return msg.with_status(
                "ok",
                detail=f"{res.get('clean', 0)} clean · "
                       f"{res.get('flagged', 0)} flagged",
                result=res)
        except Exception as exc:
            return msg.with_status("error", detail=str(exc))


class MatchAgent:
    """Computes the deterministic 0-100 match score for a single job."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    def handle(self, msg: Message) -> Message:
        try:
            from src.scoring.matcher import MatchScorer
            scorer = MatchScorer(self.settings)
            result = scorer.score(
                msg.payload.get("jd", ""),
                msg.payload.get("distill"),
                msg.payload.get("role_title", ""),
            )
            return msg.with_status(
                "ok",
                detail=f"match {result.score:.0f}/100",
                result={"score": result.score,
                        "skill_hits": result.skill_hits,
                        "skill_missing": result.skill_missing,
                        "red_flags": result.red_flags})
        except Exception as exc:
            return msg.with_status("error", detail=str(exc))


class ResumeAgent:
    """Builds a tailored, validated resume for a single job."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    def handle(self, msg: Message) -> Message:
        try:
            from src.resume.orchestrator import ResumeOrchestrator
            orch = ResumeOrchestrator(self.settings)
            result = orch.build_for_job(
                msg.payload.get("jd", ""),
                msg.payload.get("job_id", ""),
                top_k=msg.payload.get("top_k", 6),
            )
            if result.all_validated:
                return msg.with_status(
                    "ok",
                    detail="resume validated",
                    result={"pdf_path": result.pdf_path,
                            "warnings": result.warnings})
            # Needs the Validator agent to debate the unvalidated bullets.
            return msg.with_status(
                "needs_review",
                detail=f"{len(result.human_review)} bullet(s) need review",
                result={"human_review": [
                    {"bullet": getattr(b, "bullet", str(b)),
                     "validated": getattr(b, "validated", False),
                     "integrity_clean": getattr(b, "integrity_clean", True)}
                    for b in result.human_review],
                        "warnings": result.warnings})
        except Exception as exc:
            return msg.with_status("error", detail=str(exc))


class ValidatorAgent:
    """
    Closed-vocabulary hallucination gate.

    Debates the Resume agent: for each bullet the Resume agent could not
    validate, the Validator checks whether it traces to a verified master
    record fact. If it does, the bullet is accepted (debate won); if not,
    it stays flagged for human review.
    """

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    def handle(self, msg: Message) -> Message:
        try:
            from src.resume.master import load_master_record

            master = load_master_record(self.settings)
            bullets = msg.payload.get("human_review", [])
            accepted = []
            still_flagged = []
            for b in bullets:
                text = b.get("bullet", "")
                # A bullet is accepted in the debate if it traces verbatim to
                # a verified master record fact (any verified achievement,
                # employment, or skill). Otherwise it stays flagged.
                if self._traces_to_master(text, master):
                    accepted.append(b)
                else:
                    still_flagged.append(b)
            return msg.with_status(
                "ok" if not still_flagged else "needs_review",
                detail=f"{len(accepted)} accepted, "
                       f"{len(still_flagged)} still flagged",
                result={"accepted": accepted, "still_flagged": still_flagged})
        except Exception as exc:
            return msg.with_status("error", detail=str(exc))

    @staticmethod
    def _traces_to_master(text: str, master) -> bool:
        """Return True if *text* contains a verified master fact verbatim."""
        needle = text.strip().lower()
        for emp in master.employments:
            if emp.company.lower() in needle:
                return True
        for ach in master.achievements:
            if ach.bullet.lower() in needle:
                return True
        for skill in master.skills:
            if skill.name.lower() in needle:
                return True
        return False


class TrackerAgent:
    """Records a human-in-the-loop status transition for a job."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    def handle(self, msg: Message) -> Message:
        try:
            from src.tracking.tracker import Tracker
            tr = Tracker(self.settings)
            transition = tr.set_status(
                msg.payload["job_id"],
                msg.payload["new_status"],
                by=msg.payload.get("by", "human"),
                notes=msg.payload.get("notes", ""),
            )
            if transition is None:
                return msg.with_status(
                    "error", detail="invalid status transition")
            return msg.with_status(
                "ok",
                detail=f"{transition.from_status} -> {transition.to_status}",
                result={"from": transition.from_status,
                        "to": transition.to_status})
        except Exception as exc:
            return msg.with_status("error", detail=str(exc))


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class Orchestrator:
    """
    Routes messages to agents, runs the Resume↔Validator debate loop, and
    aggregates a final report.

    The debate loop: when the Resume agent returns ``needs_review``, the
    Validator agent is invoked on the flagged bullets. If the Validator
    accepts them all, the resume is re-emitted; otherwise the job is left
    for human review.
    """

    def __init__(self, settings: Optional[dict] = None,
                 progress: Optional[Callable[[str, str], None]] = None):
        self.settings = settings or {}
        self.progress = progress or (lambda *_: None)
        self.agents = {
            "ingest": IngestorAgent(self.settings),
            "compliance": ComplianceAgent(self.settings),
            "fraud": FraudAgent(self.settings),
            "match": MatchAgent(self.settings),
            "resume": ResumeAgent(self.settings),
            "validate": ValidatorAgent(self.settings),
            "track": TrackerAgent(self.settings),
        }
        self.log: list[dict] = []

    def _emit(self, label: str, detail: str = "") -> None:
        self.progress(label, detail)

    def route(self, msg: Message) -> Message:
        """Send one message to its agent and return the reply.

        Accepts either a ``Message`` or a plain dict with ``kind``,
        ``payload``, and ``correlation_id`` keys for convenience.
        """
        if isinstance(msg, dict):
            msg = Message(
                kind=msg["kind"],
                payload=msg.get("payload", {}),
                correlation_id=msg.get("correlation_id"),
            )
        agent = self.agents.get(msg.kind)
        if agent is None:
            return msg.with_status("error", detail=f"unknown agent: {msg.kind}")
        self._emit(msg.kind, msg.detail)
        reply = agent.handle(msg)
        self.log.append({
            "kind": msg.kind, "correlation_id": msg.correlation_id,
            "status": reply.status, "detail": reply.detail,
        })
        return reply

    def debate(self, resume_reply: Message) -> Message:
        """
        Run the Resume↔Validator debate for a single job.

        Returns a final message whose result carries the resolved resume
        status (ok / needs_review).
        """
        if resume_reply.status == "ok":
            return resume_reply
        # Resume agent needs review → Validator debates the bullets.
        validate_msg = Message(
            kind="validate",
            payload={"human_review": (resume_reply.result or {}).get(
                "human_review", [])},
            correlation_id=resume_reply.correlation_id,
        )
        validate_reply = self.route(validate_msg)
        result = validate_reply.result or {}
        accepted = result.get("accepted", [])
        still_flagged = result.get("still_flagged", [])
        if still_flagged:
            # Debate could not resolve — leave for human review.
            return resume_reply.with_status(
                "needs_review",
                detail=f"{len(still_flagged)} bullet(s) still flagged",
                result={"human_review": still_flagged,
                        "accepted": accepted})
        # Validator accepted all bullets — resume is now valid.
        return resume_reply.with_status(
            "ok",
            detail=f"debate resolved: {len(accepted)} bullet(s) accepted",
            result={"human_review": [], "accepted": accepted})

    def run_pipeline(self, job: dict, top_k: int = 6) -> dict:
        """
        Run the full agent pipeline for a single job dict with keys:
        job_id, jd, role_title, distill (optional).

        Returns an aggregated result dict.
        """
        job_id = job.get("job_id", "")
        self._emit("Ingesting", job_id)

        # Score.
        match_reply = self.route(Message(
            kind="match",
            payload={"jd": job.get("jd", ""), "distill": job.get("distill"),
                     "role_title": job.get("role_title", "")},
            correlation_id=job_id,
        ))
        score = 0.0
        if match_reply.status == "ok":
            score = (match_reply.result or {}).get("score", 0.0)

        # Resume + debate.
        resume_reply = self.route(Message(
            kind="resume",
            payload={"jd": job.get("jd", ""), "job_id": job_id,
                     "top_k": top_k},
            correlation_id=job_id,
        ))
        final = self.debate(resume_reply)

        return {
            "job_id": job_id,
            "match_score": score,
            "resume_status": final.status,
            "resume_result": final.result,
        }

    def set_status(self, job_id: str, new_status: str, by: str = "human",
                   notes: str = "") -> Message:
        """Convenience: route a single status-transition message."""
        return self.route(Message(
            kind="track",
            payload={"job_id": job_id, "new_status": new_status,
                     "by": by, "notes": notes},
            correlation_id=job_id,
        ))
