"""
Module 7 — Tracking.

Human-in-the-loop application tracking. Records status transitions, detects
stale applications, and DRAFTS follow-up messages for the human to copy and
send. It NEVER sends email or touches any inbox.
"""
from src.tracking.tracker import Tracker, TrackingReport, StatusTransition

__all__ = ["Tracker", "TrackingReport", "StatusTransition"]
