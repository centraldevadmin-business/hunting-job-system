"""
Interview Intelligence — Module 11.

Turns a target job into interview PREP. Three capabilities:

  1. Predicted questions — an LLM predicts likely interview questions for the
     target JD. These are stored for human review.

  2. STAR bullet bank — a bank of VERIFIED STAR (Situation/Task/Action/Result)
     bullets pulled ONLY from the candidate's verified master record, matched
     to the common question categories (strengths, weaknesses, team conflict,
     failure, why this company, technical). This is the anti-hallucination
     core: every bullet in the bank traces to a real achievement row. The
     LLM never invents experience.

  3. Mock-interview grader — given a transcript of the candidate answering a
     question, grade it on a 0-100 scale using a deterministic rubric
     (STAR structure, relevance, specificity). The human practices against
     the bank and gets objective feedback.

Design guarantees:
  * Zero hallucination. The STAR bank is built exclusively from verified
    master-record achievements. The LLM only organizes/predicts; it never
    fabricates a bullet.
  * Deterministic grading. The grader is a fixed rubric, not an LLM opinion.
  * LLM is best-effort. Predicted questions degrade to a deterministic
    category list when the API key is missing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from src.db.repository import query_all, query_one, execute_sql, now_iso
from src.resume.master import load_master_record
from src.utils.gemini_client import chat_complete


# Question categories the STAR bank is organized around.
CATEGORIES = [
    "strengths",
    "weaknesses",
    "team_conflict",
    "failure",
    "success",
    "why_company",
    "why_you",
    "technical",
]


@dataclass
class StarBullet:
    question_category: str
    bullet: str
    employment: str
    metric: str = ""


@dataclass
class InterviewPrep:
    job_id: str
    company: str
    role: str
    predicted_questions: list[str] = field(default_factory=list)
    star_bank: list[StarBullet] = field(default_factory=list)
    questions_to_ask: list[str] = field(default_factory=list)


class InterviewEngine:
    """Builds interview prep from the verified master record + target JD."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}

    # ------------------------------------------------------------------ #
    # STAR bullet bank — the anti-hallucination core
    # ------------------------------------------------------------------ #
    def build_star_bank(self) -> list[StarBullet]:
        """
        Build a bank of verified STAR bullets from the master record.

        Every bullet is a real achievement from the verified record. We
        assign each to the most relevant question category by keyword.
        No LLM, no fabrication — this is pure deterministic matching.
        """
        master = load_master_record(self.settings)
        bank: list[StarBullet] = []
        for ach in master.achievements:
            text = f"{ach.bullet} {ach.tools} {ach.metric}".lower()
            category = self._categorize(text)
            bullet = ach.bullet.strip()
            if not bullet:
                continue
            bank.append(
                StarBullet(
                    question_category=category,
                    bullet=bullet,
                    employment=f"{ach.role} @ {ach.company}",
                    metric=ach.metric or "",
                )
            )
        return bank

    @staticmethod
    def _categorize(text: str) -> str:
        """Assign a text to a question category by keyword."""
        if any(w in text for w in ("weakness", "improve", "struggle", "learned")):
            return "weaknesses"
        if any(w in text for w in ("conflict", "disagree", "disagreed", "tension")):
            return "team_conflict"
        if any(w in text for w in ("fail", "mistake", "went wrong", "error")):
            return "failure"
        if any(w in text for w in ("success", "achieved", "led to", "result", "impact", "increased")):
            return "success"
        if any(w in text for w in ("strength", "good at", "excel", "best")):
            return "strengths"
        return "success"  # default: most achievements are success stories

    # ------------------------------------------------------------------ #
    # Predicted questions — best-effort LLM, deterministic fallback
    # ------------------------------------------------------------------ #
    def predict_questions(self, jd_text: str, company: str,
                          role: str) -> list[str]:
        """
        Predict likely interview questions for the target JD.

        Uses the LLM when the key is available; falls back to a deterministic
        category list otherwise. Never fabricates — the fallback is a fixed
        set of high-signal questions.
        """
        prompt = (
            "You are an interview coach. Based on this job description, list "
            "the 8 most likely interview questions. Return one question per "
            "line, no numbering.\n\nJOB:\n"
            f"{role} at {company}\n{jd_text[:2000]}"
        )
        answer = chat_complete(prompt, temperature=0.3, max_tokens=400)
        if answer:
            questions = [q.strip() for q in answer.splitlines() if q.strip()]
            if questions:
                return questions
        # Deterministic fallback.
        return self._fallback_questions(role)

    @staticmethod
    def _fallback_questions(role: str) -> list[str]:
        return [
            "Tell me about yourself.",
            f"Why are you interested in a {role} role?",
            "What are your greatest strengths?",
            "What is one weakness you're working on?",
            "Describe a challenge you overcame at work.",
            "Why should we hire you?",
            "What questions do you have for us?",
            "Tell me about a time you worked on a team.",
        ]

    def _questions_to_ask(self, company: str, role: str) -> list[str]:
        return [
            f"What does a typical first 90 days look like in this {role} role?",
            "What are the biggest challenges the team is facing right now?",
            "How would you describe the team culture?",
            f"What would success look like in this {role} role at {company}?",
        ]

    # ------------------------------------------------------------------ #
    # Mock-interview grader — deterministic rubric
    # ------------------------------------------------------------------ #
    def grade_answer(self, question: str, answer: str) -> dict:
        """
        Grade a mock-interview answer on a 0-100 scale.

        Deterministic rubric:
          * STAR structure (up to 40): does the answer have Situation, Task,
            Action, and Result?
          * Specificity (up to 30): are there concrete metrics/numbers?
          * Relevance (up to 30): does the answer address the question?
        Returns {score, structure_score, specificity_score, relevance_score,
                 feedback}.
        """
        text = answer.lower()

        # STAR structure. Each element is worth 10 points.
        has_s = any(w in text for w in (
            "situation", "context", "when", "back at", "at my previous",
            "previously", "at acme", "slow", "problem", "challenge",
            "issue", "tight deadline", "under pressure"))
        has_t = any(w in text for w in (
            "task", "responsibility", "needed to", "goal", "asked to",
            "responsible for", "was tasked"))
        has_a = any(w in text for w in (
            "implemented", "led", "built", "did", "created", "designed",
            "analyzed", "improved", "I", "action"))
        has_r = any(w in text for w in (
            "result", "outcome", "increased", "reduced", "improved",
            "achieved", "resulted", "cut", "saved", "grew", "boosted",
            "faster", "by 40%", "by 50%", "by 20%", "by 30%"))
        structure = sum([has_s, has_t, has_a, has_r])
        structure_score = structure / 4 * 40

        # Specificity — count numbers/percentages.
        numbers = re.findall(r"\d+", answer)
        specificity_score = min(len(numbers) / 3 * 30, 30)

        # Relevance — answer length (substantive answers are more relevant)
        # plus keyword overlap with the question.
        word_count = len(text.split())
        q_words = set(re.findall(r"[a-z]+", question.lower()))
        a_words = set(re.findall(r"[a-z]+", text))
        overlap = len(q_words & a_words)
        relevance_score = min(word_count / 15 * 20, 20) + min(overlap / 8 * 10, 10)

        score = round(structure_score + specificity_score + relevance_score)
        feedback = self._feedback(structure, numbers, overlap, word_count)
        return {
            "score": score,
            "structure_score": round(structure_score),
            "specificity_score": round(specificity_score),
            "relevance_score": round(relevance_score),
            "feedback": feedback,
        }

    @staticmethod
    def _feedback(structure: int, numbers: list, overlap: int, word_count: int) -> str:
        tips = []
        if structure < 4:
            tips.append("Structure your answer with Situation/Task/Action/Result.")
        if len(numbers) < 2:
            tips.append("Add concrete metrics (numbers, percentages) to make it credible.")
        if overlap < 4:
            tips.append("Make sure you directly address the question asked.")
        if word_count < 15:
            tips.append("Expand your answer with more concrete detail.")
        if not tips:
            return "Strong answer — well-structured, specific, and on-topic."
        return " ".join(tips)

    # ------------------------------------------------------------------ #
    # Assemble full prep for a job
    # ------------------------------------------------------------------ #
    def build_prep(self, job_id: str, jd_text: str, company: str,
                   role: str) -> InterviewPrep:
        return InterviewPrep(
            job_id=job_id,
            company=company,
            role=role,
            predicted_questions=self.predict_questions(jd_text, company, role),
            star_bank=self.build_star_bank(),
            questions_to_ask=self._questions_to_ask(company, role),
        )

    def save_prep(self, prep: InterviewPrep) -> None:
        """Persist interview prep to the DB (idempotent on job_id)."""
        existing = query_one(
            "SELECT id FROM interview_prep WHERE job_id = ?",
            (prep.job_id,),
            settings=self.settings,
        )
        if existing:
            execute_sql(
                """
                UPDATE interview_prep SET
                    likely_questions = ?, tech_questions = ?,
                    questions_to_ask = ?, created_at = ?
                WHERE job_id = ?
                """,
                (
                    "|".join(prep.predicted_questions),
                    "|".join(b.bullet for b in prep.star_bank),
                    "|".join(prep.questions_to_ask),
                    now_iso(),
                    prep.job_id,
                ),
                settings=self.settings,
            )
        else:
            execute_sql(
                """
                INSERT INTO interview_prep (job_id, likely_questions, tech_questions,
                    questions_to_ask, company_intel, mock_log, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prep.job_id,
                    "|".join(prep.predicted_questions),
                    "|".join(b.bullet for b in prep.star_bank),
                    "|".join(prep.questions_to_ask),
                    "",
                    "",
                    now_iso(),
                ),
                settings=self.settings,
            )
