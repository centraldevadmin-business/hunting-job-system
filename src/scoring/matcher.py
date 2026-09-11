"""
Match scoring — Module 3.

Computes a deterministic 0-100 `match_score` for a job against the
candidate's verified profile. This is the number the brutal-possibility
engine multiplies by. It is NOT an LLM opinion — it is computed from:

  * skill overlap      : how many of the JD's hard requirements match the
                         candidate's verified skills.
  * role alignment     : does the job title fall inside the candidate's
                         target roles?
  * seniority fit      : does the JD's seniority match the candidate's
                         experience level?
  * red flags          : penalties for JD red flags (unpaid, equity-only,
                         residency required, etc.).

Every number is traceable. No hallucination.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.db.repository import query_all, query_one


# Candidate's verified skills (from the `skill` table).
def _candidate_skills() -> dict[str, str]:
    rows = query_all("SELECT name, level FROM skill")
    return {r["name"].lower(): r["level"] for r in rows}


# Candidate's target roles (from the `constraints` table).
def _target_role_tokens() -> list[str]:
    row = query_one("SELECT target_roles FROM constraints WHERE id = 1")
    if not row or not row.get("target_roles"):
        return []
    out = []
    for part in row["target_roles"].split(","):
        out.extend(part.strip().split())
    return [t.lower() for t in out if t.strip()]


# Candidate's skill vocabulary (lowercased skill names).
def _skill_vocab() -> set[str]:
    return set(_candidate_skills().keys())


# Common BA/analyst skill aliases so "BI" matches "Power BI", etc.
_SKILL_ALIASES = {
    "bi": "power bi",
    "tableau": "power bi",
    "looker": "power bi",
    "excel": "excel",
    "spreadsheets": "excel",
    "sheets": "excel",
    "sql": "sql",
    "python": "python",
    "pandas": "pandas",
    "numpy": "numpy",
    "scikit": "scikit-learn",
    "sklearn": "scikit-learn",
    "machine learning": "scikit-learn",
    "ml": "scikit-learn",
    "solver": "solver",
    "vba": "vba",
    "analytics": "data analyst",
    "analysis": "business analyst",
}


def _normalize_skill(token: str) -> str:
    t = token.strip().lower()
    return _SKILL_ALIASES.get(t, t)


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9\-]+", (text or "").lower()))


# Seniority levels, junior -> senior.
_SENIORITY_RANK = {
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
    "staff/principal": 5,
    "unknown": 3,
}


@dataclass
class MatchResult:
    score: float                       # 0-100
    skill_hits: list[str]
    skill_missing: list[str]
    role_aligned: bool
    seniority: str
    red_flags: list[str]
    breakdown: dict


class MatchScorer:
    """Deterministic 0-100 match score for a job vs. the candidate profile."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}
        self.skills = _candidate_skills()
        self.skill_vocab = _skill_vocab()
        self.target_tokens = set(_target_role_tokens())

    def score(self, jd_text: str, distill: Optional[dict] = None,
              role_title: str = "") -> MatchResult:
        """
        Compute the match score for one job.

        `distill` is the jd_distill row (dict) if present; otherwise the JD
        text is distilled on the fly with the regex distiller.
        """
        # Gather requirement lists.
        if distill:
            top = _split(distill.get("top_requirements"))
            must = _split(distill.get("must_have"))
            red_flags = _split(distill.get("red_flags"))
            seniority = (distill.get("actual_seniority") or "unknown").strip()
        else:
            from src.distiller.jd_distiller import JDDistiller
            reqs = JDDistiller(self.settings).distill(jd_text)
            top = reqs.top_requirements
            must = reqs.must_have
            red_flags = reqs.red_flags
            seniority = reqs.actual_seniority or "unknown"

        # --- Skill overlap ---
        # Collect skill-like tokens from the JD's requirement text.
        jd_text_lower = (jd_text or "").lower()
        jd_tokens = _tokenize(jd_text_lower)
        skill_hits: list[str] = []
        skill_missing: list[str] = []
        for name in self.skills:
            norm = _normalize_skill(name)
            # Direct token match.
            if norm in jd_tokens or name.lower() in jd_text_lower:
                skill_hits.append(name)
            # Sub-token match (e.g. "python" in "python/pandas").
            elif any(tok in jd_tokens for tok in norm.split()) or any(
                name.lower() in tok for tok in jd_tokens if name.lower() in tok
            ):
                skill_hits.append(name)
            else:
                skill_missing.append(name)

        # --- Role alignment ---
        role_aligned = bool(self.target_tokens) and any(
            tok in _tokenize(role_title.lower()) for tok in self.target_tokens
        )

        # --- Seniority fit ---
        seniority_rank = _SENIORITY_RANK.get(seniority, 3)
        # Candidate is a mid-level BA (current role). Target ~ mid/senior.
        seniority_fit = 1.0 if seniority_rank in (2, 3) else 0.6

        # --- Compute weighted score ---
        skill_ratio = (len(skill_hits) / len(self.skills)) if self.skills else 0.0
        # Weighted blend: skills 55%, role 25%, seniority 20%.
        raw = 0.55 * min(1.0, skill_ratio * 1.6) + \
              0.25 * (1.0 if role_aligned else 0.3) + \
              0.20 * seniority_fit
        score = raw * 100.0

        # --- Red-flag penalties ---
        for flag in red_flags:
            score -= 8
        score = max(0.0, min(100.0, score))

        return MatchResult(
            score=round(score, 1),
            skill_hits=skill_hits,
            skill_missing=skill_missing,
            role_aligned=role_aligned,
            seniority=seniority or "unknown",
            red_flags=red_flags,
            breakdown={
                "skill_ratio": round(skill_ratio, 3),
                "role_aligned": role_aligned,
                "seniority_fit": seniority_fit,
                "seniority": seniority or "unknown",
                "red_flags": len(red_flags),
            },
        )


def _split(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in str(value).split(",") if p.strip()]
