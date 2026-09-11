"""
Structured message protocol for the multi-agent orchestrator (Module 13).

Every agent receives a typed ``Message`` and returns a typed ``Message``.
Agents never talk to each other directly — they only exchange messages. This
keeps the pipeline decoupled and makes each agent independently testable.

Message kinds:
  * "ingest"      — raw postings discovered
  * "score"       — geo-compliance + match scoring
  * "fraud"       — fraud / legitimacy scoring
  * "resume"      — build a tailored resume for a job
  * "debate"      — Resume agent vs Validator agent on a bullet
  * "track"       — record a status transition
  * "report"      — final aggregated result

Each message carries a ``kind``, a ``payload`` dict, and a ``correlation_id``
so the orchestrator can route replies back to the right job.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Message:
    kind: str
    payload: dict = field(default_factory=dict)
    correlation_id: Optional[str] = None
    # Set by the receiving agent to indicate handling outcome.
    status: str = "received"      # received|ok|rejected|needs_review|error
    detail: str = ""
    result: Optional[dict] = None

    def with_status(self, status: str, detail: str = "",
                    result: Optional[dict] = None) -> "Message":
        """Return a copy of this message with status fields updated."""
        self.status = status
        self.detail = detail
        self.result = result
        return self
