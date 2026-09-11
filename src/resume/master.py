"""
Master record access — the ONLY allowed source of career facts.

Every resume bullet that leaves this system must trace back to a row in the
verified career master record. Nothing else is permitted.

This module reads:
    career_profile, employment, achievement, skill, education

It also builds the *closed vocabulary* — the exact set of proper nouns,
metrics, dates, and skills that the validator uses to reject hallucinations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from src.db.repository import query_all, query_one, get_connection


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class Employment:
    id: int
    company: str
    role: str
    start_date: str
    end_date: Optional[str]
    current: bool
    location: Optional[str]


@dataclass
class Achievement:
    id: int
    employment_id: int
    company: str
    role: str
    bullet: str
    tools: str
    metric: str


@dataclass
class Skill:
    name: str
    level: str


@dataclass
class Education:
    id: int
    institution: str
    degree: str
    year: Optional[int]
    verified: int


@dataclass
class MasterRecord:
    """The full verified career master record."""
    profile: dict = field(default_factory=dict)
    employments: list[Employment] = field(default_factory=list)
    achievements: list[Achievement] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    education: list[Education] = field(default_factory=list)

    # ----- closed vocabulary -----
    def vocabulary(self) -> set[str]:
        """
        The closed vocabulary of every token that may legally appear in a
        resume generated from this record. Anything outside this set is a
        hallucination and must be rejected by the validator.

        Contains: proper nouns (companies, institutions, tools), metrics
        (numbers, percentages, currency), dates, and skill names.
        """
        vocab: set[str] = set()

        # profile
        for key in ("full_name", "email", "phone", "location", "linked_url", "portfolio_url"):
            val = self.profile.get(key)
            if val:
                vocab.add(str(val).lower())

        # employments: company names, roles, dates, locations
        for e in self.employments:
            if e.company:
                vocab.add(e.company.lower())
            if e.role:
                vocab.add(e.role.lower())
            for d in (e.start_date, e.end_date):
                if d:
                    vocab.update(_tokenize_date(d))
            if e.location:
                vocab.add(e.location.lower())

        # achievements: bullets, tools, metrics
        for a in self.achievements:
            vocab.update(_tokenize_text(a.bullet))
            if a.tools:
                vocab.update(_tokenize_text(a.tools))
            if a.metric:
                vocab.update(_tokenize_text(a.metric))

        # skills
        for s in self.skills:
            vocab.add(s.name.lower())

        # education
        for ed in self.education:
            if ed.institution:
                vocab.add(ed.institution.lower())
            if ed.degree:
                vocab.add(ed.degree.lower())
            if ed.year:
                vocab.add(str(ed.year))

        return vocab


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_master_record(settings: Optional[dict] = None) -> MasterRecord:
    """Load the full verified master record from SQLite."""
    with get_connection(settings) as conn:
        conn.row_factory = None  # we'll use our own helpers below

        profile = query_one("SELECT * FROM career_profile LIMIT 1", settings=settings)
        employments = query_all("SELECT * FROM employment ORDER BY start_date DESC", settings=settings)
        achievements = query_all(
            """
            SELECT a.*, e.company, e.role
            FROM achievement a
            JOIN employment e ON e.id = a.employment_id
            ORDER BY a.id
            """,
            settings=settings,
        )
        skills = query_all("SELECT * FROM skill ORDER BY name", settings=settings)
        education = query_all("SELECT * FROM education ORDER BY year DESC", settings=settings)

    return MasterRecord(
        profile=profile or {},
        employments=[
            Employment(
                id=e["id"],
                company=e.get("company") or "",
                role=e.get("role") or "",
                start_date=e.get("start_date") or "",
                end_date=e.get("end_date"),
                current=bool(e.get("current", 0)),
                location=e.get("location"),
            )
            for e in employments
        ],
        achievements=[
            Achievement(
                id=a["id"],
                employment_id=a["employment_id"],
                company=a.get("company") or "",
                role=a.get("role") or "",
                bullet=a.get("bullet") or "",
                tools=a.get("tools") or "",
                metric=a.get("metric") or "",
            )
            for a in achievements
        ],
        skills=[Skill(name=s["name"], level=s.get("level") or "") for s in skills],
        education=[
            Education(
                id=ed["id"],
                institution=ed.get("institution") or "",
                degree=ed.get("degree") or "",
                year=ed.get("year"),
                verified=ed.get("verified", 0),
            )
            for ed in education
        ],
    )


# --------------------------------------------------------------------------- #
# Tokenization helpers (used by both vocabulary and validator)
# --------------------------------------------------------------------------- #
_DATE_TOKEN_RE = re.compile(r"\d{4}|\d{1,2}/\d{2}|\d{1,2}/\d{4}|\d{1,2}-\d{2}")
_NUMBER_RE = re.compile(r"\$?\d[\d,]*\.?\d*%?")


def _tokenize_date(d: str) -> set[str]:
    """Extract date-like tokens from a date string."""
    return set(_DATE_TOKEN_RE.findall(d))


def _tokenize_text(text: str) -> set[str]:
    """Extract meaningful tokens from free text (words, numbers, dates)."""
    tokens: set[str] = set()
    text = text.lower()
    for m in _DATE_TOKEN_RE.findall(text):
        tokens.add(m)
    for m in _NUMBER_RE.findall(text):
        tokens.add(m.strip())
    # words: sequences of letters, digits, and common tool chars
    for m in re.findall(r"[a-z][a-z0-9+#.\-]{2,}", text):
        tokens.add(m)
    return tokens
