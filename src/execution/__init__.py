"""
Module 6 — Execution.

Human-in-the-loop application staging. The system assembles a complete
submission package (tailored resume + cover letter + submit URL + checklist)
and records the human's one-click "I applied" — it never sends email or fills
external forms.
"""
from src.execution.application import (
    ApplicationStager,
    SubmissionPackage,
    StageResult,
)
from src.execution.cover_letter import CoverLetterGenerator, CoverLetterResult

__all__ = [
    "ApplicationStager",
    "SubmissionPackage",
    "StageResult",
    "CoverLetterGenerator",
    "CoverLetterResult",
]
