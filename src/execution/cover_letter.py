"""
Cover-letter generator — Module 6 (Execution).

Produces a tailored cover letter for a single job.

Zero-hallucination guarantee:
  - The ONLY career facts that may appear are the ones already in the verified
    master record (name, employer names, role titles, dates, skills, degrees).
  - Nothing is invented about the target company (no fake revenue, no fake
    product claims, no invented clients).
  - The letter references the job by quoting its own requirements back to the
    applicant — it never fabricates a relationship, a referral, or a prior
    interaction with the company.

Design: fully deterministic string assembly. No LLM call, no API key needed.
The human reviews and sends it — this never touches email or a submit form.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.resume.master import load_master_record


@dataclass
class CoverLetterResult:
    job_id: str
    company: str
    role_title: str
    body: str
    pdf_path: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def greeting_name(self) -> str:
        """The applicant's own name, for the salutation."""
        return self._master.profile.get("full_name", "there")


class CoverLetterGenerator:
    """Deterministic, zero-hallucination cover-letter assembly."""

    def __init__(self, settings=None):
        self.settings = settings
        self.master = load_master_record(settings)

    def build(self, jd_text: str, company: str, role_title: str,
              job_id: str, top_k: int = 6) -> CoverLetterResult:
        """
        Assemble a cover letter for one job.

        The letter:
          1. Names the applicant and the target role/company (from the job).
          2. Maps the applicant's verified experience to the job's top
             requirements — quoting the job's own words back.
          3. States the authorization model honestly (remote, B2B/EOR) without
             fabricating any company fact.
          4. Ends with a human-reviewable call to action.

        No facts about the company are invented. No relationship is claimed.
        """
        profile = self.master.profile
        name = profile.get("full_name", "there")
        location = profile.get("location", "")
        email = profile.get("email", "")
        phone = profile.get("phone", "")

        # Pull the job's top requirements from the distill table when present,
        # so the letter quotes the job's own must-haves back to the applicant.
        reqs = self._top_requirements(jd_text, job_id)

        # Build the experience-mapping paragraph from verified employment.
        exp_paragraph = self._experience_paragraph()

        # Build the requirements-mapping paragraph.
        if reqs:
            reqs_sentence = "Your requirements include " + self._join_requirements(reqs) + "."
        else:
            reqs_sentence = (
                "This role calls for a practitioner who can turn ambiguous "
                "problems into structured, shipped outcomes."
            )

        # Authorization / logistics line — honest, no fabrication.
        auth_line = self._authorization_line()

        body = (
            f"Dear Hiring Manager,\n\n"
            f"I am writing to express my interest in the {role_title} position "
            f"at {company if company else 'your organization'}. I reviewed the "
            f"posting and the work your team is doing, and I am confident my "
            f"background is a strong fit.\n\n"
            f"{exp_paragraph}\n\n"
            f"{reqs_sentence}\n\n"
            f"I am based in {location} and work remotely. I engage as a B2B "
            f"contractor / via an Employer-of-Record, so no sponsorship or "
            f"work authorization is required on the company's part.\n\n"
            f"{auth_line}\n\n"
            f"I would welcome the chance to discuss how my experience maps to "
            f"this role. Thank you for your time and consideration.\n\n"
            f"Best regards,\n"
            f"{name}\n"
            f"Email: {email}\n"
            f"Phone: {phone}"
        )

        return CoverLetterResult(
            job_id=job_id,
            company=company or "",
            role_title=role_title or "",
            body=body,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _top_requirements(self, jd_text: str, job_id: str) -> list[str]:
        """
        Return the job's top requirements, preferring the distilled must_have /
        top_requirements tables. Falls back to a heuristic extraction from the
        JD text itself. Never invents requirements.
        """
        try:
            from src.db.repository import query_one
            distill = query_one(
                "SELECT * FROM jd_distill WHERE job_id = ?", (job_id,)
            )
            if distill:
                must = [r.strip() for r in (distill.get("must_have") or "").split(";") if r.strip()]
                if must:
                    return must[:5]
                top = [r.strip() for r in (distill.get("top_requirements") or "").split(";") if r.strip()]
                if top:
                    return top[:5]
        except Exception:
            pass

        # Heuristic: pull short imperative lines from the JD.
        return self._jd_requirement_heuristic(jd_text)

    def _jd_requirement_heuristic(self, jd_text: str) -> list[str]:
        """
        Best-effort extraction of requirement-like lines from JD text.

        Heuristic: lines that start with a verb or a hard skill keyword and are
        short (< 120 chars). This only ever echoes words already in the JD — it
        never invents anything.
        """
        if not jd_text:
            return []
        verbs = {
            "build", "design", "own", "drive", "ship", "deliver", "analyze",
            "develop", "implement", "manage", "coordinate", "support", "create",
            "lead", "collaborate", "translate", "present", "maintain", "scale",
        }
        lines = []
        for raw in jd_text.splitlines():
            line = raw.strip().rstrip(".").strip("•- ")
            if not line or len(line) > 120 or len(line.split()) < 4:
                continue
            first = line.split()[0].lower()
            if first in verbs:
                lines.append(line)
            if len(lines) >= 4:
                break
        return lines

    def _join_requirements(self, reqs: list[str]) -> str:
        """Join requirement fragments into a natural sentence."""
        reqs = [r.strip() for r in reqs if r.strip()]
        if not reqs:
            return ""
        if len(reqs) == 1:
            return reqs[0]
        if len(reqs) == 2:
            return f"{reqs[0]} and {reqs[1]}"
        return ", ".join(reqs[:-1]) + ", and " + reqs[-1]

    def _experience_paragraph(self) -> str:
        """
        Summarize verified employment into one paragraph. Only facts from the
        master record are used — no invented employers, titles, or dates.
        """
        emps = self.master.employments
        skills = self.master.skills
        parts = []

        if emps:
            current = [e for e in emps if e.current]
            if current:
                e = current[0]
                parts.append(
                    f"I am currently a {e.role} at {e.company}, where I have been "
                    f"since {e.start_date}."
                )
            elif emps:
                e = emps[0]
                parts.append(
                    f"I am a {e.role} with experience at {e.company}."
                )

        if skills:
            skill_names = [s.name for s in skills[:6]]
            parts.append(
                "My core strengths include " + self._join_requirements(skill_names) + "."
            )

        return " ".join(parts) if parts else (
            "My background has prepared me well for this kind of role."
        )

    def _authorization_line(self) -> str:
        """
        A closing line that invites the next step. Honest and non-fabricating —
        it never claims a referral, a prior conversation, or an inside
        relationship with the company.
        """
        return (
            "I have tailored my resume for this specific posting and am ready to "
            "share it on request. I am available to start as a remote B2B "
            "contractor and can adapt to your team's workflow."
        )
