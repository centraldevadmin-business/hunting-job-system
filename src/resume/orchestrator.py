"""
Resume orchestrator — the public entry point for Module 4.

Ties together: master record → retrieval → rephrase → validate → integrity
→ variants → cross-checks → render.

Usage:
    from src.resume.orchestrator import ResumeOrchestrator

    orch = ResumeOrchestrator()
    result = orch.build_for_job(jd_text, job_id="GL-1234")
    print(result.pdf_path)          # validated PDF
    print(result.warnings)          # cross-check warnings
    print(result.human_review)      # bullets flagged for a human
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.resume.master import load_master_record, MasterRecord
from src.resume.generator import ResumeGenerator
from src.resume.variants import VariantGenerator
from src.resume.renderer import ResumeRenderer
from src.resume.crosscheck import run_cross_checks


@dataclass
class ResumeBuildResult:
    job_id: str
    variants: list
    pdf_path: Optional[str]
    warnings: list[str] = field(default_factory=list)
    human_review: list = field(default_factory=list)
    all_validated: bool = True


class ResumeOrchestrator:
    """End-to-end resume builder with validation at every stage."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings
        self.master = load_master_record(settings)
        self.generator = ResumeGenerator(self.master, settings)
        self.variants_gen = VariantGenerator(self.master)
        self.renderer = ResumeRenderer(self.master, settings)

    def build_for_job(self, jd_text: str, job_id: str, top_k: int = 6,
                      variant_name: str = "skills-first") -> ResumeBuildResult:
        """
        Build a validated, tailored resume for a single job.

        Returns a ResumeBuildResult. If every emitted bullet is validated and
        integrity-clean, `all_validated` is True and the PDF is emitted.
        Otherwise the PDF is NOT emitted and the result carries the bullets
        flagged for human review.
        """
        # 0. Cross-checks on the master record itself.
        report = run_cross_checks(self.master)

        # 1. Generate validated bullets.
        bullets = self.generator.generate(jd_text, top_k=top_k)

        # 2. Build variants from validated bullets.
        variants = self.variants_gen.generate(bullets, top_k=3)

        # 3. Determine which bullets need human review.
        human_review = [b for b in bullets if not (b.validated and b.integrity_clean)]

        # 4. If any bullet is unvalidated, do NOT emit a PDF.
        if not all(b.validated and b.integrity_clean for b in bullets):
            return ResumeBuildResult(
                job_id=job_id,
                variants=variants,
                pdf_path=None,
                warnings=report.warnings,
                human_review=human_review,
                all_validated=False,
            )

        # 5. Render the primary variant to PDF.
        primary = next((v for v in variants if v.name == variant_name), variants[0])
        pdf_path = self.renderer.render(primary, job_id)

        return ResumeBuildResult(
            job_id=job_id,
            variants=variants,
            pdf_path=pdf_path,
            warnings=report.warnings,
            human_review=human_review,
            all_validated=True,
        )
