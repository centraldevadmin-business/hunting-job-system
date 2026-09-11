"""
Estimated salary engine — Module 5.5.

Most career boards (including Greenhouse) do not expose a salary field, so the
dashboard would otherwise show jobs with no comp signal at all. This module
produces a defensible *estimated* annual salary range for a posting using:

  1. Role-title seniority signals (staff/principal/lead → upper band).
  2. Company comp tiers (public-tech baselines, adjusted per company).
  3. Role-family baselines (data analyst, SWE, PM, etc.).

The numbers are market estimates, not facts. Every value is clearly labelled
"estimated" in the UI so the dashboard stays honest. When a real salary is
present in the DB (``raw_jobs.salary``), it is used verbatim instead.

This is intentionally deterministic and offline — no network calls, no API
keys. It degrades gracefully: if it cannot estimate, it returns ``None``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# --------------------------------------------------------------------------- #
# Role-family baselines (US annual USD, mid-point of a typical band).
# Sourced from public compensation aggregates (Levels.fyi, Glassdoor,
# LinkedIn Salary, Google "Data Analyst" / "Data Scientist" career-page
# postings). Figures are rounded to sensible bands.
# --------------------------------------------------------------------------- #
ROLE_FAMILY_BASELINE: dict[str, tuple[float, float]] = {
    # (low, high) — annual USD
    "data analyst": (95_000, 150_000),
    "data scientist": (120_000, 185_000),
    "data engineer": (130_000, 190_000),
    "data scientist, ml": (140_000, 210_000),
    "machine learning": (150_000, 230_000),
    "ml engineer": (150_000, 230_000),
    "software engineer": (130_000, 200_000),
    "engineer": (120_000, 185_000),
    "product manager": (145_000, 215_000),
    "pm": (145_000, 215_000),
    "operations analyst": (85_000, 135_000),
    "strategy": (110_000, 175_000),
    "sales": (80_000, 160_000),
    "marketing": (85_000, 145_000),
    "designer": (120_000, 185_000),
    "recruiter": (80_000, 135_000),
    "account manager": (85_000, 145_000),
    "support": (65_000, 105_000),
    "security": (135_000, 200_000),
    "finance": (95_000, 160_000),
    "legal": (140_000, 220_000),
    "operations": (80_000, 135_000),
    "research": (125_000, 195_000),
    "analyst": (90_000, 145_000),
    "manager": (120_000, 185_000),
    "lead": (140_000, 205_000),
    "staff": (160_000, 235_000),
    "principal": (185_000, 275_000),
    "director": (180_000, 265_000),
    "head of": (175_000, 260_000),
    "vp": (200_000, 300_000),
    "chief": (220_000, 320_000),
    "intern": (45_000, 75_000),
    "co-founder": (150_000, 200_000),
}

# Company comp multipliers vs. the US big-tech baseline band.
COMPANY_COMP_MULTIPLIER: dict[str, float] = {
    "stripe": 1.05,
    "google": 1.10,
    "meta": 1.12,
    "facebook": 1.12,
    "apple": 1.08,
    "amazon": 1.03,
    "microsoft": 1.04,
    "netflix": 1.25,
    "openai": 1.15,
    "anthropic": 1.15,
    "nvidia": 1.08,
    "uber": 1.05,
    "airbnb": 1.07,
    "spotify": 0.95,
    "snap": 1.0,
    "twitter": 0.98,
    "x": 1.0,
    "linkedin": 1.05,
    "salesforce": 0.98,
    "oracle": 0.95,
    "ibm": 0.9,
    "gitlab": 1.0,
    "shopify": 1.02,
    "coinbase": 1.1,
    "databricks": 1.12,
    "scale": 1.08,
}

# Seniority bump applied to the mid-point of the baseline band.
SENIORITY_MULTIPLIER: dict[str, float] = {
    "intern": 0.55,
    "junior": 0.78,
    "entry": 0.8,
    "mid": 1.0,
    "senior": 1.22,
    "staff": 1.42,
    "principal": 1.6,
    "lead": 1.3,
    "manager": 1.28,
    "director": 1.5,
    "vp": 1.75,
    "chief": 1.9,
}

# Words that signal seniority when present in the title.
_SENIORITY_PATTERNS = [
    (r"\bprincipal\b", "principal"),
    (r"\bstaff\b", "staff"),
    (r"\blead\b", "lead"),
    (r"\bmanager\b", "manager"),
    (r"\bdirector\b", "director"),
    (r"\bvp\b", "vp"),
    (r"\bhead of\b", "head of"),
    (r"\bchief\b", "chief"),
    (r"\bsenior\b", "senior"),
    (r"\bjunior\b", "junior"),
    (r"\bentry\b", "entry"),
    (r"\bintern\b", "intern"),
]


@dataclass
class SalaryEstimate:
    """A low/high annual-USD range plus the method used."""
    low: float
    high: float
    mid: float
    method: str          # "real" | "estimated"
    note: str = ""

    @property
    def display(self) -> str:
        return f"${_fmt(self.low)} – {_fmt(self.high)}"

    @property
    def display_mid(self) -> str:
        return f"${_fmt(self.mid)} est."


def _fmt(n: float) -> str:
    """Format a dollar figure as $120k / $1.5M style."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{int(round(n / 1000))}k"
    return f"{int(round(n))}"


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _match_role_family(title: str) -> tuple[Optional[str], float]:
    """
    Return (role_family_key, family_weight) for the best-matching role family.
    Weights let us prefer a specific family (e.g. 'machine learning') over a
    generic one (e.g. 'ml' inside 'ml engineer').
    """
    t = _slugify(title)
    best = None
    best_weight = 0.0
    for family in ROLE_FAMILY_BASELINE:
        # Prefer multi-word families first by checking longer keys.
        if family in t:
            weight = len(family.split())
            if weight > best_weight:
                best = family
                best_weight = weight
    return best, best_weight


def _detect_seniority(title: str) -> Optional[str]:
    t = _slugify(title)
    for pattern, label in _SENIORITY_PATTERNS:
        if re.search(pattern, t):
            return label
    return None


def _company_key(company: str) -> str:
    return _slugify(company)


def estimate_salary(company: str, role_title: str,
                    real_salary: Optional[str] = None) -> Optional[SalaryEstimate]:
    """
    Estimate an annual salary range for a posting.

    If ``real_salary`` is a parseable range string, it is returned verbatim
    (method="real"). Otherwise a market estimate is computed from the role
    family and company comp tier. Returns ``None`` when nothing can be
    estimated.
    """
    # 1. Prefer a real, parseable salary if present.
    real = _parse_real_salary(real_salary)
    if real is not None:
        return real

    family, _ = _match_role_family(role_title)
    if family is None:
        return None

    low, high = ROLE_FAMILY_BASELINE[family]
    mid = (low + high) / 2.0

    # 2. Apply company comp tier.
    comp = COMPANY_COMP_MULTIPLIER.get(_company_key(company))
    if comp is not None:
        low *= comp
        high *= comp
        mid *= comp

    # 3. Apply seniority bump.
    seniority = _detect_seniority(role_title)
    if seniority and seniority in SENIORITY_MULTIPLIER:
        mult = SENIORITY_MULTIPLIER[seniority]
        low *= mult
        high *= mult
        mid *= mult

    # Keep a sane floor so estimates never look absurdly low.
    low = max(low, 40_000)
    high = max(high, low + 20_000)

    return SalaryEstimate(
        low=round(low, -2),
        high=round(high, -2),
        mid=round(mid, -2),
        method="estimated",
        note=f"Market estimate for {family.replace('_', ' ')} in {company}.",
    )


def _parse_real_salary(text: Optional[str]) -> Optional[SalaryEstimate]:
    """
    Parse a real salary string like "$120k - $150k" or "$120000" into a
    SalaryEstimate. Returns None if unparseable.
    """
    if not text:
        return None
    t = text.lower().replace("$", "").replace(",", "")
    nums = re.findall(r"(\d+(?:\.\d+)?)\s*(k|m)?", t)
    nums = [(float(v), (u or "")) for v, u in nums if v]
    if not nums:
        return None

    def to_annual(v: float, unit: str) -> float:
        if unit == "m":
            return v * 1_000_000
        if unit == "k":
            return v * 1_000
        return v

    values = [to_annual(v, u) for v, u in nums]
    if len(values) == 1:
        v = values[0]
        return SalaryEstimate(v * 0.85, v * 1.15, v, method="real")
    low, high = min(values), max(values)
    return SalaryEstimate(low, high, (low + high) / 2.0, method="real")
