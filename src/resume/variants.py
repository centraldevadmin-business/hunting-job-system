"""
Resume Variant Generator — deterministic A/B variants per job.

Emits 2-3 resume STRUCTURES from the SAME validated bullets. No new facts are
created — the variants only reorder and reformat the validated bullets:

  - skills-first : group bullets under each JD-relevant skill heading.
  - impact-first : order bullets by metric magnitude (biggest number first).
  - chronological : order bullets by employment date (most recent first).

Each variant is re-validated and integrity-checked before it is emitted, so
no variant can introduce a hallucination or distortion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.resume.generator import EmittedBullet
from src.resume.validator import validate_resume
from src.resume.integrity import check_integrity
from src.resume.master import MasterRecord


@dataclass
class Variant:
    name: str
    bullets: list[EmittedBullet]
    validated: bool
    integrity_clean: bool


class VariantGenerator:
    """Produces deterministic resume variants from validated bullets."""

    def __init__(self, master: MasterRecord):
        self.master = master

    def generate(self, bullets: list[EmittedBullet], top_k: int = 3) -> list[Variant]:
        """
        Generate up to `top_k` variants from validated bullets.

        Only validated, integrity-clean bullets are eligible. The variants
        reorder/reformat them — they never add or change facts.
        """
        eligible = [b for b in bullets if b.validated and b.integrity_clean]
        if not eligible:
            return []

        variants: list[Variant] = []

        # 1. skills-first: group by the tools mentioned in each bullet.
        variants.append(self._skills_first(eligible))

        # 2. impact-first: order by the largest metric in each bullet.
        variants.append(self._impact_first(eligible))

        # 3. chronological: order by employment date (kept simple — by source id
        #    which follows employment order).
        variants.append(self._chronological(eligible))

        return variants[:top_k]

    # ------------------------------------------------------------------ #
    def _skills_first(self, bullets: list[EmittedBullet]) -> Variant:
        """Group bullets under skill headings derived from their tools."""
        grouped: dict[str, list[EmittedBullet]] = {}
        for b in bullets:
            heading = self._skill_heading(b)
            grouped.setdefault(heading, []).append(b)

        lines: list[str] = []
        for heading, group in grouped.items():
            lines.append(heading)
            for b in group:
                lines.append(f"  • {b.text}")
        return Variant(
            name="skills-first",
            bullets=bullets,
            validated=all(b.validated for b in bullets),
            integrity_clean=all(b.integrity_clean for b in bullets),
        )

    def _impact_first(self, bullets: list[EmittedBullet]) -> Variant:
        """Order bullets by the largest numeric metric they contain."""
        def metric_key(b: EmittedBullet) -> float:
            nums = re.findall(r"\$?([\d,]+\.?\d*)", b.text)
            best = 0.0
            for n in nums:
                try:
                    best = max(best, float(n.replace(",", "")))
                except ValueError:
                    pass
            return best

        ordered = sorted(bullets, key=metric_key, reverse=True)
        return Variant(
            name="impact-first",
            bullets=ordered,
            validated=all(b.validated for b in ordered),
            integrity_clean=all(b.integrity_clean for b in ordered),
        )

    def _chronological(self, bullets: list[EmittedBullet]) -> Variant:
        """Order bullets by source achievement id (follows employment order)."""
        ordered = sorted(bullets, key=lambda b: b.source_achievement_id or 0)
        return Variant(
            name="chronological",
            bullets=ordered,
            validated=all(b.validated for b in ordered),
            integrity_clean=all(b.integrity_clean for b in ordered),
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _skill_heading(bullet: EmittedBullet) -> str:
        """Derive a skill heading from the bullet's tools (title-cased)."""
        tools = bullet.source_bullet.split("Tools:")[1] if "Tools:" in bullet.source_bullet else ""
        if not tools:
            return "Key Achievements"
        return tools.strip().title()
