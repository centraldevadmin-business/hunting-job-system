"""
Daily orchestration scheduler.

Wakes ONCE per day (at dawn) to run the full hunt:
    collect -> score -> stage

Then sleeps. No continuous polling — this keeps API usage minimal and
stays well within free-tier limits.

Run via cron / APscheduler. Example cron (7am daily):
    0 7 * * * cd /path/to/hunting-job-system && /path/to/python scheduler.py --daily

--once   : run a single hunt cycle and exit (used for manual runs / tests)
--daily  : run once and exit (same as --once; kept for cron clarity)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from src.utils.logging_setup import get_logger


def run_hunt_cycle() -> dict:
    """
    Run one full hunt cycle: collect -> score -> stage -> tailor.

    Returns a summary dict. This is the REAL pipeline (Modules 1–6), wired
    to the same engine the dashboard uses so behaviour is identical.
    """
    logger = get_logger("scheduler")
    logger.info("Starting hunt cycle")

    # No UI progress callback in a cron context — pass a no-op.
    def _noop(*a, **k):
        pass

    from src.dashboard.hunt_engine import run_hunt
    cards = run_hunt(progress=_noop, resume_top_k=5)

    scored = sum(1 for c in cards if c.match_score is not None)
    staged = sum(1 for c in cards if c.status in ("applied", "screen",
                                                   "interview", "final", "offer"))
    summary = {
        "collected": len(cards),
        "scored": scored,
        "staged": staged,
        "status": "ok",
        "at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("Hunt cycle complete: %s", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Hunting job system scheduler")
    parser.add_argument("--daily", action="store_true", help="Run once and exit (cron)")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    summary = run_hunt_cycle()
    print(f"Hunt cycle summary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
