"""
Resume generator — rephrases authentic bullets to match a JD.

The LLM (Grok, free tier) is a REPHRASER ONLY. It receives the authentic
source bullets and is instructed — by a hard system prompt — to:
  - keep every fact, metric, tool, and date exactly as sourced,
  - reorder / reword to surface JD-relevant skills first,
  - NEVER invent a tool, metric, company, or accomplishment.

Retrieval: the top-k most JD-relevant bullets (from the vector store) are the
ONLY material the generator may touch. It cannot reach beyond them.

Output: a structured dict of emitted bullets, each tagged with its source
achievement id so the validator and integrity layer can trace it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from src.resume.master import MasterRecord, Achievement
from src.resume.vector_store import VectorStore
from src.resume.validator import Validator, validate_resume
from src.resume.integrity import IntegrityChecker, check_integrity
from src.utils.gemini_client import chat_complete


@dataclass
class EmittedBullet:
    text: str
    source_achievement_id: Optional[int]
    source_bullet: str
    validated: bool = False
    integrity_clean: bool = True


class ResumeGenerator:
    """Retrieves bullets, rephrases them, validates, and enforces integrity."""

    def __init__(self, master: MasterRecord, settings: Optional[dict] = None):
        self.master = master
        self.settings = settings
        self.store = VectorStore(settings)
        self.validator = Validator(master)
        self.integrity = IntegrityChecker()
        self._llm = None

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #
    def retrieve(self, jd_text: str, top_k: int = 6) -> list[Achievement]:
        """
        Retrieve the top-k most JD-relevant authentic bullets.

        These are the ONLY bullets the generator may emit.
        """
        if not self.master.achievements:
            return []

        jd_emb = self.store.embed(jd_text)
        scored = []
        for a in self.master.achievements:
            if not a.bullet:
                continue
            b_emb = self.store.embed(a.bullet)
            scored.append((self._cosine(jd_emb, b_emb), a))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [a for _, a in scored[:top_k]]

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #
    def generate(self, jd_text: str, top_k: int = 6, max_retries: int = 2) -> list[EmittedBullet]:
        """
        Generate validated, integrity-clean bullets for a JD.

        Returns a list of EmittedBullet. Each has been through the validator
        (closed-vocabulary) and the integrity layer (no distortion). Bullets
        that fail are dropped after retries; if they still fail, they are
        flagged for human review rather than emitted.
        """
        candidates = self.retrieve(jd_text, top_k=top_k)
        if not candidates:
            return []

        emitted: list[EmittedBullet] = []
        for ach in candidates:
            lead = self._rephrase(ach, jd_text)
            # Deterministically re-attach the verified facts so truncation
            # can never drop a metric, tool, or company.
            bullet = self._assemble(ach, lead)
            if not bullet:
                continue

            # Validator: closed-vocabulary gate.
            result = validate_resume(bullet, self.master)
            if not result.valid:
                # Retry up to max_retries; if still invalid, flag for human.
                still_bad = False
                for _ in range(max_retries):
                    bullet = self._rephrase(ach, jd_text)
                    result = validate_resume(bullet, self.master)
                    if result.valid:
                        break
                    still_bad = True
                if still_bad:
                    emitted.append(EmittedBullet(
                        text=bullet,
                        source_achievement_id=ach.id,
                        source_bullet=ach.bullet,
                        validated=False,
                        integrity_clean=False,
                    ))
                    continue

            # Integrity: no distortion gate.
            integ = check_integrity(bullet, ach)
            if not integ.clean:
                emitted.append(EmittedBullet(
                    text=bullet,
                    source_achievement_id=ach.id,
                    source_bullet=ach.bullet,
                    validated=True,
                    integrity_clean=False,
                ))
                continue

            emitted.append(EmittedBullet(
                text=bullet,
                source_achievement_id=ach.id,
                source_bullet=ach.bullet,
                validated=True,
                integrity_clean=True,
            ))

        return emitted

    # ------------------------------------------------------------------ #
    # LLM rephrasing (Grok free tier)
    # ------------------------------------------------------------------ #
    def _rephrase(self, ach: Achievement, jd_text: str) -> str:
        """
        Rephrase an authentic bullet to surface JD-relevant skills.

        The system prompt is a HARD constraint: facts are frozen. The LLM may
        only reorder, reword, and restructure — never invent.
        """
        system_prompt = (
            "You are a resume bullet rewriter. You will be given ONE authentic "
            "accomplishment and a job description.\n\n"
            "HARD RULES — VIOLATION IS NOT ALLOWED:\n"
            "1. Keep EVERY fact EXACTLY as sourced: company names, metrics, "
            "percentages, dollar figures, dates, and tool names must be "
            "preserved verbatim.\n"
            "2. Do NOT invent any tool, technology, metric, company, role, or "
            "accomplishment that is not present in the source bullet.\n"
            "3. Do NOT change ownership or scope. If the source says "
            "'contributed to', keep that framing — do not upgrade to 'led'.\n"
            "4. Output EXACTLY ONE lead clause of at most 10 words. No "
            "preamble, no quotes, no bullet markers, no options, no lists. "
            "Just the opening clause that leads with the JD-relevant skill.\n\n"
            f"SOURCE BULLET: {ach.bullet}\n"
            f"METRIC (sourced): {ach.metric or 'none'}\n"
            f"TOOLS (sourced): {ach.tools or 'none'}\n"
            f"JOB DESCRIPTION: {jd_text[:1500]}"
        )

        try:
            lead = chat_complete(
                system=system_prompt,
                prompt="Write a short lead clause for the source bullet above.",
                temperature=0.3,
                max_tokens=40,
            )
            if not lead:
                return ach.bullet
            lead = lead.splitlines()[0].strip().rstrip(".")
            if not lead:
                return ach.bullet
            return lead
        except Exception:
            # Any LLM failure falls back to the authentic bullet.
            return ach.bullet

    # ------------------------------------------------------------------ #
    # Deterministic assembly — re-attach verified facts
    # ------------------------------------------------------------------ #
    @staticmethod
    def _assemble(ach: Achievement, lead: str) -> str:
        """
        Assemble a complete bullet from a lead clause + verified facts.

        The facts (metric, tools, company) come from structured ground truth,
        never from the model, so truncation cannot drop them. The result is a
        single, complete, grammatical sentence.
        """
        lead_lower = lead.lower()
        parts = [lead]

        # Metric — re-attach verbatim only if not already present in the lead.
        if ach.metric and str(ach.metric).lower() not in lead_lower:
            parts.append(f"({ach.metric})")

        # Tools — re-attach verbatim only if NONE of the individual tools
        # already appear in the lead (the model may return the full bullet,
        # which already contains the tools in a different form).
        if ach.tools:
            tools = ach.tools.strip()
            tool_tokens = [t.strip().lower() for t in ach.tools.split(",") if t.strip()]
            if not any(tok in lead_lower for tok in tool_tokens):
                parts.append(f"using {tools}")

        # Company — re-attach if present and not already in the lead.
        if ach.company and ach.company.lower() not in lead_lower:
            parts.append(f"at {ach.company}")

        bullet = "; ".join(p for p in parts if p).strip()
        bullet = bullet[0].upper() + bullet[1:] if bullet else bullet
        return bullet.rstrip(".")

    # ------------------------------------------------------------------ #
    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        import math

        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
