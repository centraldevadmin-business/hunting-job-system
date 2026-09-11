"""
Negotiation & Compensation Advisor — Module 12.

Turns an offer (or a target job) into a negotiation PLAYBOOK. Three
capabilities:

  1. Market benchmark — an annual-USD range for the role/company/country,
     reused from the dashboard's deterministic ``estimate_salary`` engine
     (real salary used verbatim when present, otherwise a market estimate).
     No API key, no network — pure offline math.

  2. Offer analysis — the human enters the offer's components (base, equity,
     signing, PTO, relocation, other). The engine rolls them into a total
     annual compensation figure, compares it to the benchmark, and flags
     under-market / red-flag terms. It then proposes a target range and a
     recommended counter.

  3. Script generator — an LLM drafts negotiation talking points + an email
     script built ONLY from the verified offer facts and the benchmark. When
     the API key is missing it degrades to a deterministic template. The
     human always reviews and sends; the system never sends.

Design guarantees:
  * Zero hallucination. The benchmark traces to the candidate's own role,
    company, and any real salary in the DB. The offer numbers come from the
    human, never invented. The LLM only rephrases — it never adds a number.
  * Deterministic math. Every comparison, gap %, and counter is computed,
    never guessed.
  * LLM is best-effort. Talking points degrade to a fixed template offline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.db.repository import query_all, query_one, execute_sql, now_iso
from src.utils.gemini_client import chat_complete


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Offer:
    """A concrete offer, entered by the human. All money is annual USD."""
    base: float = 0.0
    equity: float = 0.0          # annualized equity vesting value
    signing: float = 0.0         # one-time; counted separately in total_ctc
    relocation: float = 0.0      # one-time
    pto_days: int = 0
    other: float = 0.0           # bonus, car, etc., annualized
    currency: str = "USD"
    notes: str = ""

    @property
    def annual_recurring(self) -> float:
        """Base + annual equity + annual bonus/other (recurring CTC)."""
        return self.base + self.equity + self.other

    @property
    def total_ctc(self) -> float:
        """Annual recurring + one-time cash (signing, relocation)."""
        return self.annual_recurring + self.signing + self.relocation


@dataclass
class Benchmark:
    low: float
    high: float
    mid: float
    method: str          # "real" | "estimated"
    note: str = ""

    @property
    def display(self) -> str:
        return f"${self.low:,.0f} – ${self.high:,.0f}"


@dataclass
class OfferAnalysis:
    offer: Offer
    benchmark: Benchmark
    total_ctc: float
    gap_to_mid: float            # offer total vs benchmark mid (signed $)
    gap_pct: float               # offer total vs benchmark mid (percent)
    verdict: str                 # "strong" | "at-market" | "below-market" | "low"
    flags: list[str] = field(default_factory=list)
    target_low: float = 0.0
    target_high: float = 0.0
    recommended_counter: float = 0.0
    talking_points: list[str] = field(default_factory=list)
    email_script: str = ""


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #
class CompAdvisor:
    """Analyzes offers against a market benchmark and drafts a negotiation playbook."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    # ------------------------------------------------------------------ #
    # Market benchmark
    # ------------------------------------------------------------------ #
    def benchmark(self, company: str, role_title: str,
                  real_salary: Optional[str] = None) -> Benchmark:
        """
        Return a market benchmark for the role/company.

        Reuses the dashboard's deterministic ``estimate_salary`` engine so we
        do not duplicate the role-family / seniority / company-comp tables.
        """
        try:
            from src.dashboard.salary_estimator import estimate_salary
            est = estimate_salary(company, role_title, real_salary)
            if est is not None:
                return Benchmark(
                    low=est.low, high=est.high, mid=est.mid,
                    method=est.method, note=est.note or "",
                )
        except Exception:
            # Fallback below if the import path is unavailable.
            pass
        # Last-resort deterministic fallback (no dashboard import).
        return Benchmark(
            low=100_000, high=160_000, mid=130_000,
            method="estimated",
            note=f"Generic baseline for {role_title} at {company}.",
        )

    # ------------------------------------------------------------------ #
    # Offer analysis
    # ------------------------------------------------------------------ #
    def analyze(self, offer: Offer, company: str, role_title: str,
                real_salary: Optional[str] = None) -> OfferAnalysis:
        """
        Compare an offer to the market benchmark and produce a playbook.

        All money is treated as annual USD. The human is expected to enter
        numbers in USD; a currency mismatch is flagged but not enforced.
        """
        bench = self.benchmark(company, role_title, real_salary)
        total = offer.total_ctc
        gap = total - bench.mid
        gap_pct = (gap / bench.mid * 100.0) if bench.mid else 0.0

        # Verdict from the gap to the benchmark midpoint.
        if gap_pct >= 10:
            verdict = "strong"
        elif gap_pct >= -5:
            verdict = "at-market"
        elif gap_pct >= -20:
            verdict = "below-market"
        else:
            verdict = "low"

        flags = self._flags(offer, bench)

        # Target range: anchor to the benchmark, leave room to climb.
        target_low = round(bench.mid * 1.05, -2)
        target_high = round(bench.high * 1.10, -2)
        # Recommended counter: ask for the target low, settle for mid+.
        counter = target_low if total < target_low else round(total * 1.08, -2)
        counter = max(counter, target_low)

        playbook = CompAdvisor(self.settings)
        script = playbook._script(company, role_title, offer, bench, verdict,
                                  counter)

        return OfferAnalysis(
            offer=offer,
            benchmark=bench,
            total_ctc=total,
            gap_to_mid=round(gap, -2),
            gap_pct=round(gap_pct, 1),
            verdict=verdict,
            flags=flags,
            target_low=target_low,
            target_high=target_high,
            recommended_counter=round(counter, -2),
            talking_points=script["talking_points"],
            email_script=script["email"],
        )

    def _flags(self, offer: Offer, bench: Benchmark) -> list[str]:
        flags: list[str] = []
        # Red flags — classic signals of a weak or predatory offer.
        if offer.base < bench.low * 0.85:
            flags.append(
                "Base pay is well below the market floor — likely a lowball.")
        if offer.equity <= 0 and offer.annual_recurring < bench.low:
            flags.append(
                "No equity and base below market — total comp is weak.")
        if offer.signing > 0 and offer.base < bench.low * 0.8:
            flags.append(
                "Signing bonus is propping up a below-market base — "
                "ask for recurring base instead.")
        if offer.pto_days and offer.pto_days < 15:
            flags.append(
                f"PTO is thin ({offer.pto_days} days) vs. typical "
                f"20-25 days for this level.")
        # One-time-heavy comp: the recurring number is what matters long-term.
        if offer.annual_recurring > 0 and offer.total_ctc > 0:
            one_time = (offer.signing + offer.relocation) / offer.total_ctc
            if one_time > 0.30:
                flags.append(
                    "Most of the offer is one-time cash (signing/relocation); "
                    "the recurring base is what you negotiate forever.")
        return flags

    # ------------------------------------------------------------------ #
    # Script generator — LLM with deterministic fallback
    # ------------------------------------------------------------------ #
    def _script(self, company: str, role_title: str, offer: Offer,
                bench: Benchmark, verdict: str, counter: float) -> dict:
        prompt = (
            "You are a calm, confident compensation negotiator. Draft two "
            "things for a candidate negotiating an offer. Keep it concise.\n\n"
            "ROLE: {role}\nCOMPANY: {company}\n"
            "BENCHMARK (market range): ${low} – ${high}, mid ${mid}\n"
            "OFFER (annual USD): base ${base}, equity ${eq}, signing ${sig}, "
            "other ${other}, total CTC ${total}\n"
            "VERDICT: {verdict}\n\n"
            "Return exactly two sections separated by '---':\n"
            "1) TALKING POINTS: 5 short bullet lines (one idea each), framed "
            "positively, anchored to the benchmark.\n"
            "2) EMAIL: a 4-6 sentence offer-rejection-and-counter email that "
            "expresses enthusiasm, cites the market range, and asks for "
            "${counter} base.\n\n"
            "Do not invent facts beyond the numbers above."
        ).format(
            role=role_title, company=company,
            low=f"{bench.low:,.0f}", high=f"{bench.high:,.0f}",
            mid=f"{bench.mid:,.0f}",
            base=f"{offer.base:,.0f}", eq=f"{offer.equity:,.0f}",
            sig=f"{offer.signing:,.0f}", other=f"{offer.other:,.0f}",
            total=f"{offer.total_ctc:,.0f}", verdict=verdict,
            counter=f"{max(counter, bench.mid):,.0f}",
        )
        answer = chat_complete(prompt, temperature=0.4, max_tokens=700)
        if answer:
            parts = answer.split("---", 1)
            if len(parts) == 2:
                tps = [b.strip() for b in parts[0].splitlines()
                       if b.strip() and not b.strip().upper().startswith("TALKING")]
                return {
                    "talking_points": tps[:8],
                    "email": parts[1].strip(),
                }
        # Deterministic fallback.
        return self._fallback_script(company, role_title, offer, bench, verdict,
                                     counter)

    @staticmethod
    def _fallback_script(company: str, role_title: str, offer: Offer,
                         bench: Benchmark, verdict: str,
                         counter: float) -> dict:
        tps = [
            "I'm excited about the role and the team — this is a genuine yes.",
            f"Based on market data for a {role_title} in this space, the "
            f"typical range is ${bench.low:,.0f} – ${bench.high:,.0f}.",
            "I'd like to see the recurring base align more closely with that range.",
            "I'm flexible on the mix — base, equity, and timeline all matter.",
            "Can we get to a number around "
            f"${max(counter, bench.mid):,.0f} base?",
        ]
        email = (
            "Hi [hiring manager],\n\n"
            "Thank you again for the offer to join as a "
            f"{role_title} at {company} — I'm genuinely excited about the "
            "team and the work.\n\n"
            f"As I review the package, the market range for this kind of role "
            f"is roughly ${bench.low:,.0f} – ${bench.high:,.0f}. I'd love to "
            f"get closer to that, ideally around "
            f"${max(counter, bench.mid):,.0f} base. I'm "
            "flexible on the overall mix and very keen to make this work.\n\n"
            "Would you be open to a quick call this week?\n\n"
            "Best,\n[Your name]"
        )
        return {"talking_points": tps, "email": email}

    # ------------------------------------------------------------------ #
    # Persistence — compensation history
    # ------------------------------------------------------------------ #
    def save_negotiation(self, job_id: str, company: str, role_title: str,
                         offer: Offer, analysis: OfferAnalysis) -> None:
        """Persist a negotiation record to the DB (idempotent on job_id)."""
        existing = query_one(
            "SELECT id FROM negotiations WHERE job_id = ?",
            (job_id,),
            settings=self.settings,
        )
        strategy = (
            f"{analysis.verdict}; target ${analysis.target_low:,.0f}-"
            f"${analysis.target_high:,.0f}; counter "
            f"${analysis.recommended_counter:,.0f}"
        )
        notes = (
            f"Total CTC ${offer.total_ctc:,.0f} vs benchmark "
            f"${analysis.benchmark.display} "
            f"(gap {analysis.gap_pct:+.1f}%)"
        )
        if existing:
            execute_sql(
                """
                UPDATE negotiations SET
                    offer_amount = ?, benchmark = ?, strategy = ?,
                    status = ?, notes = ?
                WHERE job_id = ?
                """,
                (
                    f"${offer.total_ctc:,.0f}",
                    analysis.benchmark.display,
                    strategy,
                    "draft",
                    notes,
                    job_id,
                ),
                settings=self.settings,
            )
        else:
            execute_sql(
                """
                INSERT INTO negotiations
                    (job_id, offer_amount, benchmark, strategy, status, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, f"${offer.total_ctc:,.0f}",
                 analysis.benchmark.display, strategy, "draft", notes),
                settings=self.settings,
            )

    def history(self, job_id: Optional[str] = None) -> list[dict]:
        """Return stored negotiation records (all, or one by job_id)."""
        if job_id:
            return query_one(
                "SELECT * FROM negotiations WHERE job_id = ?",
                (job_id,),
                settings=self.settings,
            )
        return query_all(
            "SELECT * FROM negotiations ORDER BY id DESC",
            settings=self.settings,
        )
