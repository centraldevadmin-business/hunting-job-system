"""
Claim-Integrity Layer — catches DISTORTION, not just invention.

The validator rejects tokens that don't exist in the master record. The
integrity layer catches a different failure mode: a bullet that is built from
real facts but DISTORTS them — e.g. upgrading "contributed to" to "led", or
claiming sole ownership of a team achievement.

It checks, for every emitted bullet:
  1. Verb tense / ownership — did the rewrite escalate ownership?
  2. Metric preservation — is the source metric still present and unchanged?
  3. Tool preservation — are the source tools still present?
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.resume.master import Achievement


# Ownership-escalating verbs: if the emitted bullet uses one of these but the
# source bullet used a weaker verb, it's a distortion.
STRONG_VERBS = {
    "led", "spearheaded", "orchestrated", "directed", "managed", "owned",
    "initiated", "created", "built", "designed", "established", "launched",
    "headed", "chaired", "drove",
}
WEAK_VERBS = {
    "contributed to", "helped", "assisted", "supported", "participated in",
    "involved in", "exposed to", "familiar with", "worked on", "touched",
}

# Metric-preserving: a number followed by its unit/suffix must survive.
import re
_METRIC_RE = re.compile(r"\$?\d[\d,]*\.?\d*%?")


@dataclass
class IntegrityResult:
    clean: bool
    flags: list[str]


class IntegrityChecker:
    """Checks emitted bullets against their source achievement."""

    def check(self, emitted: str, source: Optional[Achievement]) -> IntegrityResult:
        flags: list[str] = []

        if source is None:
            # No source — the validator should have caught this, but double-check.
            flags.append("Bullet has no source achievement (cannot verify)")
            return IntegrityResult(clean=False, flags=flags)

        emitted_lower = emitted.lower()
        source_lower = source.bullet.lower()

        # 1. Ownership escalation check.
        if self._has_strong_verb(emitted_lower) and self._has_weak_verb(source_lower):
            flags.append(
                f"Ownership escalation: source says '{source.bullet}' "
                f"but emitted bullet claims leadership/ownership"
            )

        # 2. Metric preservation check.
        source_metrics = _METRIC_RE.findall(source.bullet)
        emitted_metrics = _METRIC_RE.findall(emitted)
        for m in source_metrics:
            if m not in emitted_metrics:
                flags.append(
                    f"Metric distortion: source metric '{m}' is missing or altered "
                    f"in emitted bullet"
                )

        # 3. Tool preservation check.
        #
        # Tools may be stored as separate metadata (ach.tools) and the
        # generator is allowed to relocate them. We only flag a tool as
        # "dropped" if it appears BOTH in the source bullet text AND in the
        # ach.tools metadata but is missing from the emitted bullet. This
        # avoids false positives where a tool lives only in metadata.
        if source.tools:
            for tool in (t.strip() for t in source.tools.split(",")):
                if not tool or tool.lower() in emitted_lower:
                    continue
                # Only flag if the tool was actually present in the source
                # bullet text (i.e. the generator dropped it from prose).
                if tool.lower() not in source_lower:
                    continue
                flags.append(
                    f"Tool dropped: source tool '{tool}' is missing from emitted bullet"
                )

        return IntegrityResult(clean=len(flags) == 0, flags=flags)

    @staticmethod
    def _has_strong_verb(text: str) -> bool:
        for verb in STRONG_VERBS:
            if verb in text:
                return True
        return False

    @staticmethod
    def _has_weak_verb(text: str) -> bool:
        for verb in WEAK_VERBS:
            if verb in text:
                return True
        return False


def check_integrity(emitted: str, source: Optional[Achievement]) -> IntegrityResult:
    """Convenience wrapper."""
    return IntegrityChecker().check(emitted, source)
