"""
Module 2b — JD Distiller.

Grok (free tier) reads each JD and outputs a STRUCTURED "what they really want"
list. This is the one LLM task that genuinely earns its keep: it turns a wall
of JD prose into structured requirements the validator and match engine can
consume deterministically.

The distiller NEVER invents facts — it only summarizes what is in the JD.
If no LLM key is available, it falls back to a deterministic regex-based
distiller that extracts the same fields from the raw text.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from src.utils.gemini_client import chat_complete


class _FakeResponse:
    """Minimal stand-in so the existing parse path stays unchanged."""
    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)
    class _Message:
        def __init__(self, content):
            self.content = content
    choices = None
    def __init__(self, content):
        self.choices = [self._Choice(content)]


@dataclass
class JDRequirements:
    top_requirements: list[str] = field(default_factory=list)
    must_have: list[str] = field(default_factory=list)
    nice_to_have: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    actual_seniority: str = ""
    source: str = "llm"          # llm | regex | empty

    def to_row(self) -> dict:
        """Serialize for the jd_distill table (comma-separated lists)."""
        return {
            "top_requirements": ", ".join(self.top_requirements),
            "must_have": ", ".join(self.must_have),
            "nice_to_have": ", ".join(self.nice_to_have),
            "red_flags": ", ".join(self.red_flags),
            "actual_seniority": self.actual_seniority,
        }


class JDDistiller:
    """Distills a JD into structured requirements (LLM with regex fallback)."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}
        self._llm = None

    def distill(self, jd_text: str) -> JDRequirements:
        """
        Distill a JD into structured requirements.

        Tries Grok first; falls back to a deterministic regex distiller if no
        key is available or the LLM call fails.
        """
        if not jd_text or not jd_text.strip():
            return JDRequirements()

        try:
            result = self._distill_llm(jd_text)
            if result.source == "llm":
                return result
        except Exception:
            pass

        return self._distill_regex(jd_text)

    # ------------------------------------------------------------------ #
    # LLM path (Grok free tier)
    # ------------------------------------------------------------------ #
    def _distill_llm(self, jd_text: str) -> JDRequirements:
        system_prompt = (
            "You are a precise job-description analyst. You will be given a job "
            "description. Extract structured requirements. You MUST ONLY use "
            "facts present in the JD — never invent requirements, tools, or "
            "seniority.\n\n"
            "Return a JSON object with these keys:\n"
            "  {\"top_requirements\": [...], \"must_have\": [...], "
            "\"nice_to_have\": [...], \"red_flags\": [...], "
            "\"actual_seniority\": \"string\"}\n\n"
            "Rules:\n"
            "- top_requirements: the 3-5 most important hard requirements.\n"
            "- must_have: non-negotiable qualifications/experience.\n"
            "- nice_to_have: bonus qualifications.\n"
            "- red_flags: anything concerning (visa-only, equity-only pay, "
            "unpaid, vague company, etc.). Empty list if none.\n"
            "- actual_seniority: junior / mid / senior / lead / staff / unknown.\n"
            "- Output ONLY the JSON object. No preamble."
        )

        try:
            content = chat_complete(
                system=system_prompt,
                prompt=jd_text[:3000],
                temperature=0.0,
                max_tokens=600,
            )
            if content is None:
                return self._distill_regex(jd_text)
            return self._parse_llm_json(content.strip(), jd_text)
        except Exception:
            return self._distill_regex(jd_text)

    def _parse_llm_json(self, content: str, jd_text: str) -> JDRequirements:
        """Parse the LLM's JSON output; fall back to regex on any failure."""
        try:
            import json
            data = json.loads(content)
            req = JDRequirements(
                top_requirements=_as_list(data.get("top_requirements")),
                must_have=_as_list(data.get("must_have")),
                nice_to_have=_as_list(data.get("nice_to_have")),
                red_flags=_as_list(data.get("red_flags")),
                actual_seniority=str(data.get("actual_seniority", "")).strip(),
                source="llm",
            )
            if not req.top_requirements:
                return self._distill_regex(jd_text)
            return req
        except Exception:
            return self._distill_regex(jd_text)

    # ------------------------------------------------------------------ #
    # Deterministic regex fallback
    # ------------------------------------------------------------------ #
    def _distill_regex(self, jd_text: str) -> JDRequirements:
        """
        Extract structured requirements without an LLM.

        Heuristics only — never invents. Extracts what is literally in the text.
        """
        text = jd_text.strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        # Seniority detection.
        seniority = self._detect_seniority(text)

        # Must-have vs nice-to-have: bullet points under requirement-like headers.
        must_have: list[str] = []
        nice_to_have: list[str] = []
        top_requirements: list[str] = []

        for ln in lines:
            # Skip pure headers.
            if ln.lower().endswith(":") and len(ln) < 60:
                continue
            # Nice-to-have signals.
            if re.match(r"^(nice to have|bonus|preferred|plus|familiar with|good to have)", ln, re.I):
                nice_to_have.append(_clean_bullet(ln))
            elif re.match(r"^(must|required|minimum|at least|require|need|bachelor|master|certified|certification)", ln, re.I):
                must_have.append(_clean_bullet(ln))
            else:
                # Generic bullet — treat as a top requirement if short.
                if 15 < len(ln) < 200:
                    top_requirements.append(_clean_bullet(ln))

        # Red flags.
        red_flags = self._detect_red_flags(text)

        # Keep top_requirements bounded and de-duplicated.
        top_requirements = _dedupe(top_requirements)[:5]
        must_have = _dedupe(must_have)[:8]
        nice_to_have = _dedupe(nice_to_have)[:8]

        return JDRequirements(
            top_requirements=top_requirements,
            must_have=must_have,
            nice_to_have=nice_to_have,
            red_flags=red_flags,
            actual_seniority=seniority,
            source="regex",
        )

    def _detect_seniority(self, text: str) -> str:
        t = text.lower()
        if re.search(r"\b(staff|principal|architect|director|vp)\b", t):
            return "staff/principal"
        if re.search(r"\b(lead|senior|staff)\b", t):
            return "senior"
        if re.search(r"\b(mid|intermediate)\b", t):
            return "mid"
        if re.search(r"\b(junior|entry|entry-level|fresh|graduate|intern)\b", t):
            return "junior"
        if re.search(r"\b(lead|staff)\b", t):
            return "lead"
        return "unknown"

    def _detect_red_flags(self, text: str) -> list[str]:
        flags = []
        if re.search(r"\bunpaid\b", text, re.I):
            flags.append("unpaid role")
        if re.search(r"\b(equity only|equity-only|no base|no base salary)", text, re.I):
            flags.append("equity-only compensation")
        if re.search(r"\b(need to buy|buy equipment|pay for equipment|gear card)", text, re.I):
            flags.append("candidate must buy equipment")
        if re.search(r"\b(gift card|paypal reimbursement)", text, re.I):
            flags.append("gift-card / reimbursement scheme")
        if re.search(r"\b(telegram|whatsapp)\s*(only|exclusive|chat)", text, re.I):
            flags.append("Telegram/WhatsApp-only interview")
        if re.search(r"\bmust reside\b|must be a [a-z]+ citizen|permanent residency", text, re.I):
            flags.append("residency / citizenship required")
        return flags


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v).strip() for v in value if str(v).strip()]


def _clean_bullet(line: str) -> str:
    """Strip leading bullet markers and requirement headers from a line."""
    ln = re.sub(r"^[•\-\u2022*]\s*", "", line).strip()
    ln = re.sub(r"^\(.*?\)\s*", "", ln)
    return ln


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
