"""
Ingestion engine — the fallback chain + dedup + DB writer.

For each target, try tier 1 (Greenhouse API), then tier 2 (HTML fallback),
then tier 3 (direct careers), then tier 4 (manual paste). Each result carries
a confidence label. Dedup by (company + role_title + canonical_url). Write new
postings to the `raw_jobs` table.
"""
from __future__ import annotations

from typing import Optional

from src.db.repository import query_one, execute_sql, now_iso
from src.ingestion.posting import Posting
from src.ingestion.targets.greenhouse_target import GreenhouseTarget
from src.ingestion.targets.html_fallback_target import HtmlFallbackTarget
from src.ingestion.targets.direct_careers_target import DirectCareersTarget
from src.ingestion.targets.manual_paste_target import ManualPasteTarget
from src.ingestion.targets.lever_target import LeverTarget
from src.ingestion.targets.ashby_target import AshbyTarget
from src.ingestion.targets.workable_target import WorkableTarget


class IngestionEngine:
    """Runs the fallback ingestion chain for all configured targets."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings = settings
        from src.config_loader import load_config
        self.config = load_config()

    # ------------------------------------------------------------------ #
    def ingest_all(self, limit: int = 50) -> list[Posting]:
        """
        Run the fallback chain for every target. Returns the list of NEW
        postings written to the DB (deduped).
        """
        new_postings: list[Posting] = []
        for target_cfg in self.config.targets():
            try:
                postings = self._fetch_with_fallback(target_cfg, limit)
            except Exception as exc:
                # Never let one target's failure stop the others.
                postings = []
                _log(f"Target {target_cfg['name']} failed: {exc}")

            for p in postings:
                if self._is_new(p):
                    self._write(p)
                    new_postings.append(p)

        return new_postings

    # ------------------------------------------------------------------ #
    def _fetch_with_fallback(self, target_cfg: dict, limit: int) -> list[Posting]:
        """
        Try the best source first, then fall down the chain.
        Returns the first non-empty tier's results.

        Chain:
          Tier 1  Greenhouse API (confidence=high) — platform == greenhouse
          Tier 2  HTML fallback of the board/careers page (confidence=low)
          Tier 3  Direct careers page scrape (confidence=low)
        """
        name = target_cfg["name"]
        domain = target_cfg["domain"]
        url = target_cfg["url"]
        keywords = target_cfg.get("role_keywords", [])

        # Tier 1: Greenhouse API (only when platform is greenhouse).
        if target_cfg.get("platform") == "greenhouse":
            board = target_cfg.get("board") or _board_from_url(url)
            gh = GreenhouseTarget(name, board, domain, keywords)
            postings = gh.fetch(limit)
            if postings:
                return postings

        # Tier 2: platform-specific JSON APIs (Lever / Ashby / Workable).
        platform = target_cfg.get("platform")
        if platform == "lever":
            lv = LeverTarget(name, domain, url, keywords)
            postings = lv.fetch(limit)
            if postings:
                return postings
        elif platform == "ashby":
            ab = AshbyTarget(name, domain, url, keywords)
            postings = ab.fetch(limit)
            if postings:
                return postings
        elif platform == "workable":
            wk = WorkableTarget(name, domain, url, keywords)
            postings = wk.fetch(limit)
            if postings:
                return postings

        # Tier 3: HTML fallback — parse the board/careers page for job links.
        html = HtmlFallbackTarget(name, domain, url, keywords)
        postings = html.fetch(limit)
        if postings:
            return postings

        # Tier 3: direct careers page scrape.
        direct = DirectCareersTarget(name, domain, url, keywords)
        postings = direct.fetch(limit)
        if postings:
            return postings

        return []

    # ------------------------------------------------------------------ #
    # Dedup + persistence
    # ------------------------------------------------------------------ #
    def _is_new(self, posting: Posting) -> bool:
        """True if this posting's normalized id is not already in raw_jobs."""
        row = query_one(
            "SELECT id FROM raw_jobs WHERE id = ?",
            (posting.normalized_id,),
        )
        return row is None

    def _write(self, posting: Posting) -> None:
        """Insert a posting into raw_jobs (idempotent on id)."""
        execute_sql(
            """
            INSERT OR REPLACE INTO raw_jobs
                (id, source, source_type, url, company, role_title, jd,
                 salary, location, posted_at, canonical_ats_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                posting.normalized_id,
                posting.source,
                posting.source_type,
                posting.url,
                posting.company,
                posting.role_title,
                posting.jd,
                posting.salary,
                posting.location,
                posting.posted_at,
                posting.canonical_ats_url,
                now_iso(),
            ),
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _board_from_url(url: str) -> str:
    """Extract a Greenhouse board slug from a URL, or a safe default."""
    import re
    m = re.search(r"greenhouse\.io/v1/boards/([^/]+)", url)
    if m:
        return m.group(1)
    # Common: apply.workable.com/<board>/ or <board>.greenhouse.io
    m = re.search(r"(?:workable\.com/|boards\.greenhouse\.io/)([a-z0-9-]+)", url, re.I)
    if m:
        return m.group(1)
    return "careers"


def _log(msg: str) -> None:
    from src.utils.logging_setup import get_logger
    get_logger("ingestion").warning(msg)
