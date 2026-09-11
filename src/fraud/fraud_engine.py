"""
Module 3a — Posting-level Anti-Fraud Engine.

Deterministic binary gate. No LLM. Rules beat an LLM here.

Composite score 0–100 → green / amber / red. Anything red is auto-rejected
and never reaches the dashboard.

Rules:
  1. Domain match (strongest): posting URL host must equal the target
     company's known domain. Mismatch → red.
  2. Payment / equipment requests: "buy equipment", "pay to start",
     "gift card", "Telegram/WhatsApp-only interview" → red.
  3. Salary absurdity: posted salary > 3× min-salary with no other signal
     → amber (anomaly), not red.
  4. Generic contact: contact email on a free domain (Gmail/Yahoo/Hotmail)
     for a "company" → amber.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Free email domains — a "company" posting a job through one is suspicious.
FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "mail.com", "protonmail.com", "aol.com", "zoho.com", "gmx.com",
}

# Instant-red signals.
RED_PATTERNS = [
    (re.compile(r"buy\s+(?:\w+\s+)*equipment", re.I), "must buy equipment"),
    (re.compile(r"pay\s+to\s+start", re.I), "pay to start"),
    (re.compile(r"pay\s+for\s+(your\s+)?equipment|equipment\s+fee", re.I), "equipment fee"),
    (re.compile(r"gift\s+card", re.I), "gift card requirement"),
    (re.compile(r"(telegram|whatsapp)\s*(only|exclusive|chat|interview)", re.I),
     "Telegram/WhatsApp-only interview"),
    (re.compile(r"pay\s+a\s+refundable\s+deposit", re.I), "refundable deposit"),
    (re.compile(r"your\s+paycheck\s+must\s+be\s+deposited\s+into\s+(their|a)\s+account", re.I),
     "check-cashing scheme"),
]


@dataclass
class FraudResult:
    score: int                      # 0–100 (higher = safer)
    level: str                      # green | amber | red
    flags: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def rejected(self) -> bool:
        return self.level == "red"


class FraudEngine:
    """Deterministic posting-level fraud scoring."""

    def __init__(self, min_salary: int = 30000):
        self.min_salary = int(min_salary)

    def evaluate(self, url: str, jd: str = "", salary: str = "",
                 contact_email: str = "", company_domain: str = "") -> FraudResult:
        """
        Score a posting.

        url: the posting URL.
        jd: the job description text.
        salary: the posted salary string (may be None).
        contact_email: any contact email found in the JD.
        company_domain: the target company's known domain (for the domain
            match rule). Empty means "unknown" → not penalized.
        """
        flags: list[str] = []
        score = 100

        # Rule 1 — domain match (strongest).
        if company_domain:
            posting_host = host_of(url)
            known = company_domain.strip().lower().lstrip("www.")
            if posting_host and posting_host != known:
                score = 0
                flags.append(f"domain mismatch: {posting_host} != {known}")

        # Rule 2 — payment / equipment requests.
        for pattern, label in RED_PATTERNS:
            if pattern.search(jd or ""):
                score = 0
                flags.append(label)

        # Rule 3 — salary absurdity (amber, not red).
        posted = parse_salary(salary)
        if posted and posted > 3 * self.min_salary:
            score -= 35
            flags.append(f"salary anomaly: ${posted:,} > 3× min (${self.min_salary:,})")

        # Rule 4 — generic contact on a free domain.
        if contact_email:
            domain = email_domain(contact_email)
            if domain in FREE_EMAIL_DOMAINS:
                score -= 35
                flags.append(f"free-domain contact email: {domain}")

        score = max(0, min(100, score))
        level = _score_to_level(score)
        reason = "; ".join(flags) if flags else "no fraud signals detected"
        return FraudResult(score=score, level=level, flags=flags, reason=reason)


def _score_to_level(score: int) -> str:
    if score >= 70:
        return "green"
    if score >= 40:
        return "amber"
    return "red"


def host_of(url: str) -> str:
    """Return the host (lowercased, no www) of a URL."""
    if not url:
        return ""
    u = re.sub(r"^https?://", "", url)
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0].lower()


def email_domain(email: str) -> str:
    """Return the lowercased domain of an email address."""
    if "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


def parse_salary(salary: Optional[str]) -> Optional[int]:
    """
    Extract a representative annual salary (USD) from a salary string.

    Handles "$120k", "$120,000", "$120k–$150k" (returns the lower bound),
    "120000 USD", etc. Returns None if no number is found.
    """
    if not salary:
        return None
    text = salary.lower()
    # Strip currency symbols and commas.
    text = re.sub(r"[,$]", "", text)
    # Find the first number, optionally with a k/m suffix.
    m = re.search(r"(\d+(?:\.\d+)?)\s*([km]?)\b", text)
    if not m:
        return None
    value = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k":
        value *= 1000
    elif suffix == "m":
        value *= 1_000_000
    return int(value)
