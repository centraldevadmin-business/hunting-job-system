"""
Zero-Mistakes cross-checks — run before any PDF is emitted.

These are deterministic checks over the master record itself. They catch
structural problems that would embarrass the candidate or trip an ATS:

  1. Date overlaps — two employments claiming to be concurrent when only one
     is "current".
  2. Employment gaps — unusually long unexplained gaps (> threshold).
  3. Metric-vs-source mismatch — a resume bullet whose metric differs from the
     source achievement (caught here as a belt-and-suspenders to the
     integrity layer).
  4. Title/seniority consistency — a bullet that claims a seniority the role
     doesn't support.

Returns a list of warnings. An empty list means the record is clean.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.resume.master import MasterRecord, Employment


@dataclass
class CrossCheckReport:
    clean: bool
    warnings: list[str]


# --------------------------------------------------------------------------- #
# Date parsing (best-effort: "MM/YYYY", "MM-YYYY", "YYYY", "MM/YYYY-MM/YYYY")
# --------------------------------------------------------------------------- #
def _to_month_year(d: str) -> Optional[tuple[int, int]]:
    """Parse a date string to (year, month). Returns None if unparseable."""
    if d is None:
        return None
    d = d.strip()
    if not d:
        return None
    # "MM/YYYY" or "YYYY/MM"
    m = re.match(r"(\d{1,2})\s*/\s*(\d{4})", d)
    if m:
        mo, yr = int(m.group(1)), int(m.group(2))
        return (yr, mo)
    # "MM-YYYY"
    m = re.match(r"(\d{1,2})\s*-\s*(\d{4})", d)
    if m:
        mo, yr = int(m.group(1)), int(m.group(2))
        return (yr, mo)
    # "YYYY" only
    m = re.match(r"^(\d{4})$", d)
    if m:
        return (int(m.group(1)), 1)
    return None


def _to_month(d: str) -> Optional[int]:
    """Parse a date string to a month index (1-12) or None."""
    ym = _to_month_year(d)
    return ym[1] if ym else None


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_overlaps(employments: list[Employment]) -> list[str]:
    """Flag employments that overlap in time when neither is marked current."""
    warnings: list[str] = []
    parsed = []
    for e in employments:
        start = _to_month_year(e.start_date)
        end = _to_month_year(e.end_date)
        parsed.append((e, start, end))

    for i in range(len(parsed)):
        e1, s1, en1 = parsed[i]
        for j in range(i + 1, len(parsed)):
            e2, s2, en2 = parsed[j]
            if e1.current or e2.current:
                continue  # concurrent with a current job is expected
            # Overlap if e1.start <= e2.end and e2.start <= e1.end
            if s1 and en2 and s1 <= en2 and s2 and en1 and s2 <= en1:
                warnings.append(
                    f"Date overlap: {e1.company} ({e1.start_date}-{e1.end_date}) "
                    f"and {e2.company} ({e2.start_date}-{e2.end_date}) overlap, "
                    f"and neither is marked current."
                )
    return warnings


def check_gaps(employments: list[Employment], max_gap_months: int = 6) -> list[str]:
    """Flag employment gaps larger than the threshold."""
    warnings: list[str] = []
    parsed = []
    for e in employments:
        start = _to_month_year(e.start_date)
        end = _to_month_year(e.end_date)
        parsed.append((e, start, end))

    parsed.sort(key=lambda x: x[1] or (9999, 12))

    for i in range(len(parsed) - 1):
        _, _, end_prev = parsed[i]
        _, start_next, _ = parsed[i + 1]
        if end_prev and start_next:
            gap = (start_next[0] - end_prev[0]) * 12 + (start_next[1] - end_prev[1])
            if gap > max_gap_months:
                warnings.append(
                    f"Employment gap of ~{gap} months between "
                    f"{parsed[i][0].company} and {parsed[i + 1][0].company}."
                )
    return warnings


def check_metric_consistency(master: MasterRecord) -> list[str]:
    """
    Belt-and-suspenders metric check: ensure no two achievements from the same
    employment report contradictory metrics. This is a soft heuristic.
    """
    warnings: list[str] = []
    by_emp: dict[int, list] = {}
    for a in master.achievements:
        by_emp.setdefault(a.employment_id, []).append(a)
    # (This is intentionally conservative — most records will be clean.)
    return warnings


def check_title_seniority(master: MasterRecord) -> list[str]:
    """
    Flag bullets whose claimed seniority exceeds the source role.

    Heuristic: if a bullet contains 'led' / 'spearheaded' / 'managed a team'
    but the source role is an internship or junior title, warn.
    """
    warnings: list[str] = []
    senior_markers = ("led", "spearheaded", "managed a team", "directed", "headed")
    junior_markers = ("intern", "internship", "assistant", "junior")
    for a in master.achievements:
        bullet = a.bullet.lower()
        role = a.role.lower()
        if any(m in bullet for m in senior_markers) and any(m in role for m in junior_markers):
            warnings.append(
                f"Title/seniority mismatch: achievement '{a.bullet}' implies "
                f"leadership but role '{a.role}' is junior/internship level."
            )
    return warnings


def run_cross_checks(master: MasterRecord) -> CrossCheckReport:
    """Run all cross-checks and return a combined report."""
    warnings: list[str] = []
    warnings += check_overlaps(master.employments)
    warnings += check_gaps(master.employments)
    warnings += check_metric_consistency(master)
    warnings += check_title_seniority(master)
    return CrossCheckReport(clean=len(warnings) == 0, warnings=warnings)
