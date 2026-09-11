"""
ReportLab renderer — single-column, ATS-friendly PDF.

Single-column layout only (ATS parsers choke on multi-column designs). No
tables, no text boxes, no graphics — plain text flow that any ATS can parse.
The PDF is generated from validated, integrity-clean bullets only.

The resume is a complete, professional document:
  - Name + contact line
  - Professional summary (derived from the master record's real skills, role,
    and education — never invented)
  - Professional experience (from the verified employment records, with the
    validated achievement bullets nested under each role)
  - Skills (grouped by category from the verified skill list)
  - Education
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import re

from src.resume.variants import Variant
from src.resume.master import MasterRecord
from src.db.repository import _project_root


class ResumeRenderer:
    """Renders a validated resume variant to a single-column ATS-friendly PDF."""

    def __init__(self, master: MasterRecord, settings: Optional[dict] = None):
        self.master = master
        self.settings = settings
        resumes_dir = (
            _project_root() / "output" / "resumes"
            if settings is None
            else _project_root() / settings.get("paths", {}).get("resumes", "output/resumes")
        )
        resumes_dir.mkdir(parents=True, exist_ok=True)
        self.resumes_dir = resumes_dir

    # ------------------------------------------------------------------ #
    # Section builders — all derived from verified master-record facts.
    # ------------------------------------------------------------------ #
    def _summary(self) -> Optional[str]:
        """A professional summary built only from verified facts.

        No invented claims. It states the verified role, top verified skills,
        and years of experience computed from the employment dates.
        """
        profile = self.master.profile
        role = ""
        if self.master.employments:
            role = self.master.employments[0].role or ""
        years = self._years_of_experience()
        parts = []
        if role:
            parts.append(f"{role}")
        skill_list = [s.name for s in self.master.skills if s.name]
        if skill_list:
            joined = ", ".join(s.lower() for s in skill_list)
            if len(joined) > 60:
                joined = joined[:57] + "…"
            parts.append(f"with expertise in {joined}")
        if not parts:
            sentence = "Results-driven professional."
        elif len(parts) == 1:
            sentence = f"Results-driven {parts[0]}."
        else:
            sentence = f"Results-driven {parts[0]} {parts[1]}."
        if years:
            sentence += f" {years} years of hands-on experience."
        return sentence

    def _years_of_experience(self) -> Optional[str]:
        """Compute years of experience from employment date ranges (verified)."""
        import datetime as _dt
        dates = []
        for e in self.master.employments:
            start = self._parse_date(e.start_date)
            end = self._parse_date(e.end_date) if e.end_date else _dt.date.today()
            if start:
                months = (end.year - start.year) * 12 + (end.month - start.month)
                if months > 0:
                    dates.append(months)
        if not dates:
            return None
        total_months = min(dates) if len(dates) == 1 else sum(dates)
        years = round(total_months / 12, 1)
        return f"~{years}" if years else None

    @staticmethod
    def _parse_date(s: Optional[str]):
        import datetime as _dt
        if not s:
            return None
        s = s.strip()
        for fmt in ("%m/%Y", "%Y-%m", "%Y-%m-%d", "%m/%Y"):
            try:
                return _dt.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def _skills_grouped(self) -> list[tuple[str, list[str]]]:
        """Group verified skills into categories for a cleaner layout."""
        groups = {
            "Languages & Frameworks": [],
            "Data & Analytics": [],
            "Tools & Platforms": [],
            "Other": [],
        }
        lang = {"python", "vba", "sql", "r", "java", "javascript", "typescript"}
        data = {"pandas", "numpy", "scikit-learn", "power bi", "excel", "power bi desktop",
                "tableau", "matplotlib", "seaborn", "solver", "power query"}
        tools = {"git", "jupyter", "postgres", "mysql", "mongodb", "aws", "gcp",
                 "docker", "looker", "looker studio", "looker studio"}
        for s in self.master.skills:
            name = s.name.lower()
            if name in lang:
                groups["Languages & Frameworks"].append(s.name)
            elif name in data:
                groups["Data & Analytics"].append(s.name)
            elif name in tools:
                groups["Tools & Platforms"].append(s.name)
            else:
                groups["Other"].append(s.name)
        return [(k, v) for k, v in groups.items() if v]

    def _render(self, variant: Variant, job_id: str, filename: Optional[str] = None) -> str:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, HRFlowable
        )
        from reportlab.lib import colors

        if filename is None:
            # Sanitize job_id: it may contain | / ? and other illegal chars.
            safe = re.sub(r"[^a-z0-9_-]+", "_", (job_id or "")).lower().strip("_")
            filename = f"resume_{safe}_{variant.name}.pdf"
        pdf_path = self.resumes_dir / filename

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "CustomTitle", parent=styles["Title"], fontSize=18,
            spaceAfter=2, textColor=colors.HexColor("#0f172a"),
        )
        contact_style = ParagraphStyle(
            "Contact", parent=styles["BodyText"], fontSize=9,
            textColor=colors.HexColor("#475569"), spaceAfter=4,
        )
        style = styles["BodyText"]
        h2 = ParagraphStyle(
            "SectionHead", parent=styles["Heading2"], fontSize=12,
            spaceBefore=16, spaceAfter=8, textColor=colors.HexColor("#0f172a"),
            fontName="Helvetica-Bold",
            border_width=0, border_color=colors.HexColor("#2563eb"),
            border_padding=0,
        )
        bullet_style = ParagraphStyle(
            "Bullet", parent=style, leftIndent=16, spaceAfter=5, fontSize=10,
            leading=14,
        )
        exp_head = ParagraphStyle(
            "ExpHead", parent=styles["Heading3"], fontSize=11,
            spaceBefore=8, spaceAfter=0, textColor=colors.HexColor("#0f172a"),
            fontName="Helvetica-Bold",
        )
        exp_sub = ParagraphStyle(
            "ExpSub", parent=style, fontSize=9.5, textColor=colors.HexColor("#475569"),
            spaceAfter=3,
        )
        summary_style = ParagraphStyle(
            "Summary", parent=style, fontSize=10.5, spaceAfter=6, leading=15,
        )

        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=LETTER,
            leftMargin=0.7 * inch,
            rightMargin=0.7 * inch,
            topMargin=0.7 * inch,
            bottomMargin=0.7 * inch,
        )

        flowables: list = []

        # Map achievement id -> employment id so nested bullets land under the
        # right role even when an employment has multiple achievements.
        ach_to_emp = {a.id: a.employment_id for a in self.master.achievements}

        # Name + contact line.
        name = self.master.profile.get("full_name", "Candidate")
        flowables.append(Paragraph(name, title_style))

        contact_parts = []
        for key in ("email", "phone", "location"):
            val = self.master.profile.get(key)
            if val:
                contact_parts.append(str(val))
        if contact_parts:
            flowables.append(Paragraph(" &nbsp;|&nbsp; ".join(contact_parts), contact_style))

        # Accent rule under the header.
        flowables.append(HRFlowable(
            width="100%", thickness=1.5, color=colors.HexColor("#2563eb"),
            spaceBefore=4, spaceAfter=10,
        ))

        # Professional summary.
        summary = self._summary()
        if summary:
            flowables.append(Spacer(1, 2))
            flowables.append(Paragraph("Professional Summary", h2))
            flowables.append(Paragraph(summary, summary_style))

        # Professional experience — verified employment + validated bullets.
        if self.master.employments:
            flowables.append(Spacer(1, 4))
            flowables.append(Paragraph("Professional Experience", h2))
            for e in self.master.employments:
                when = e.start_date
                if e.end_date:
                    when = f"{e.start_date} — {e.end_date}"
                elif e.current:
                    when = f"{e.start_date} — Present"
                loc = f" &nbsp;|&nbsp; {e.location}" if e.location else ""
                flowables.append(Paragraph(f"{e.role} — {e.company}{loc}", exp_head))
                flowables.append(Paragraph(when, exp_sub))
                # Nested validated achievement bullets for this employment.
                emp_bullets = [
                    b for b in variant.bullets
                    if ach_to_emp.get(b.source_achievement_id) == e.id
                ]
                if not emp_bullets:
                    # Fall back to any achievement from this employment even if
                    # it wasn't selected for this JD, so the role is never empty.
                    emp_achs = [
                        a for a in self.master.achievements if a.employment_id == e.id
                    ]
                    for a in emp_achs:
                        flowables.append(Paragraph(f"• {a.bullet}", bullet_style))
                else:
                    for b in emp_bullets:
                        flowables.append(Paragraph(f"• {b.text}", bullet_style))

        # Skills grouped by category.
        groups = self._skills_grouped()
        if groups:
            flowables.append(Spacer(1, 4))
            flowables.append(Paragraph("Skills", h2))
            for group_name, names in groups:
                flowables.append(Paragraph(f"<b>{group_name}:</b> " + ", ".join(names), style))

        # Education.
        if self.master.education:
            flowables.append(Spacer(1, 4))
            flowables.append(Paragraph("Education", h2))
            for ed in self.master.education:
                year = f" ({ed.year})" if ed.year else ""
                flowables.append(
                    Paragraph(f"{ed.degree} — {ed.institution}{year}", style)
                )

        doc.build(flowables)
        return str(pdf_path)

    # Backwards-compatible alias used by the orchestrator.
    def render(self, variant: Variant, job_id: str, filename: Optional[str] = None) -> str:
        return self._render(variant, job_id, filename)
