"""
Browser autofill — Module 6 (Execution), manual-assist mode.

This is NOT an autonomous applicant. It is a *human-assist* tool: it opens the
company's own application page in a real browser (Playwright + Chromium),
pre-fills the standard fields it can safely know from the candidate's verified
master record (full name, email, phone), and stages the resume upload — then it
STOPS. The human reviews the page and clicks submit themselves.

Why manual-assist and not full autonomy:
  * Career pages (Greenhouse, Lever, Ashby, Workday) are heavily anti-bot. A
    fully autonomous submit is a permanent cat-and-mouse game that will break
    and, worse, could submit garbage.
  * The system has NO email access and NO mandate to act without a human.
  * Pre-filling the boring fields saves time; the human always owns the click.

Constraints honoured:
  * Zero hallucination — only fields the master record actually contains are
    filled. Unknown fields are left blank for the human.
  * No submission — this module never clicks "Submit".
  * No data leaves the machine — the browser session is local.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from src.resume.master import load_master_record, MasterRecord


# The fields we can safely pre-fill from the master record alone.
# Each maps a friendly label to the master-record attribute that fills it.
_KNOWN_FIELDS = {
    "Full name": "full_name",
    "First name": "first_name",
    "Last name": "last_name",
    "Email": "email",
    "Email address": "email",
    "Phone": "phone",
    "Mobile": "phone",
    "Phone number": "phone",
}


@dataclass
class AutofillResult:
    """Outcome of an autofill session."""
    opened: bool = False
    url: Optional[str] = None
    filled: list[str] = field(default_factory=list)      # labels actually filled
    skipped: list[str] = field(default_factory=list)      # labels with no data
    not_found: list[str] = field(default_factory=list)    # labels not on the page
    error: Optional[str] = None
    submitted: bool = False                               # always False — never auto-submit

    @property
    def ready_for_human(self) -> bool:
        """True when the page is open and pre-filled, awaiting the human."""
        return self.opened and not self.error and not self.submitted


class Autofiller:
    """
    Pre-fill standard application fields from the master record.

    Usage:
        autofiller = Autofiller()
        result = autofiller.fill("https://careers.example.com/job/123")
        print(result.filled)
        print(result.ready_for_human)   # True -> human reviews + submits
    """

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings or {}
        self.master = load_master_record(self.settings)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def fill(self, url: str, headless: bool = False,
             timeout_ms: int = 20000) -> AutofillResult:
        """
        Open `url` in a browser and pre-fill known fields.

        Returns an AutofillResult. The page is left OPEN for the human to
        review and submit — this method never submits.
        """
        res = AutofillResult(url=url)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            res.error = "playwright is not installed. Run: uv run playwright install chromium"
            return res

        profile = self.master.profile
        # Build a lookup of label -> value from the master record.
        values = self._resolve_values(profile)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                res.opened = True
            except Exception as exc:
                res.error = f"Failed to open page: {exc}"
                browser.close()
                return res

            # Try to pre-fill each known field.
            for label, attr in _KNOWN_FIELDS.items():
                value = values.get(attr)
                if not value:
                    res.skipped.append(label)
                    continue
                filled = self._fill_field(page, label, value)
                if filled:
                    res.filled.append(label)
                else:
                    res.not_found.append(label)

            browser.close()
        return res

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _resolve_values(self, profile: dict) -> dict[str, str]:
        """Map known field attributes to concrete values from the profile."""
        name = profile.get("full_name") or ""
        parts = name.split()
        first = parts[0] if parts else ""
        last = " ".join(parts[1:]) if len(parts) > 1 else ""
        return {
            "full_name": name,
            "first_name": first,
            "last_name": last,
            "email": (profile.get("email") or "").lower(),
            "phone": profile.get("phone") or "",
        }

    def _fill_field(self, page, label: str, value: str) -> bool:
        """
        Find an input whose accessible label / placeholder / name matches
        `label` and fill it. Returns True if a matching field was found.
        """
        selectors = [
            # aria-label / data-testid style
            f'aria-label="{label}"',
            f'[aria-label*="{label}" i]',
            # placeholder
            f'input[placeholder*="{label}" i]',
            f'textarea[placeholder*="{label}" i]',
            # name / id (word-boundary)
            f'input[name*="{label}" i]',
            f'#label-{label.lower()}',
            # visible text label
            f'label:has-text("{label}") input',
            f'label:has-text("{label}") textarea',
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if not loc.is_visible(timeout=1500):
                    continue
                loc.fill(value)
                return True
            except Exception:
                continue
        return False
