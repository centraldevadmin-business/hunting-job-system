"""
Deterministic validator — the HARD GATE against hallucination.

Rule: tokenize the generated resume text. Any proper noun, metric, date, or
skill that is NOT in the master record's closed vocabulary is a hallucination.
The resume is REJECTED and must be re-generated (max 2 retries). If it still
fails, it is flagged for human review and never emitted.

This is deterministic — no LLM involved. It is the core anti-hallucination
mechanism of the whole system.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.resume.master import MasterRecord, _tokenize_text, _tokenize_date, _NUMBER_RE


@dataclass
class ValidationResult:
    valid: bool
    violations: list[str]
    rejected_tokens: list[str]


class Validator:
    """
    Closed-vocabulary validator.

    A token is legal only if it appears in the master record's vocabulary.
    We compare against a normalized vocabulary so that "SQL" and "sql" match,
    and "$50,000" matches "$50,000".
    """

    def __init__(self, master: MasterRecord, allow_fuzzy: bool = False):
        self.master = master
        self.allow_fuzzy = allow_fuzzy
        self.vocab = master.vocabulary()

    def validate(self, text: str) -> ValidationResult:
        """
        Validate a generated resume against the closed vocabulary.

        Returns a ValidationResult. `valid` is True only when there are zero
        violations.
        """
        violations: list[str] = []
        rejected: list[str] = []

        lowered = text.lower()

        # 1. Check every vocabulary token in the text.
        tokens = _tokenize_text(text)
        # also grab raw numbers and dates
        tokens.update(_NUMBER_RE.findall(text))
        tokens.update(_tokenize_date(text))

        for tok in tokens:
            if not tok:
                continue
            if tok.lower() not in self.vocab and not self._fuzzy_match(tok):
                rejected.append(tok)
                violations.append(f"Token '{tok}' not found in master record (possible hallucination)")

        # 2. Check that every source bullet is represented (no omission of a
        #    verified fact when it's relevant). This is a soft check — we only
        #    flag if a whole verified metric disappears, which the integrity
        #    layer handles. Here we focus on invention.

        return ValidationResult(
            valid=len(violations) == 0,
            violations=violations,
            rejected_tokens=rejected,
        )

    def _fuzzy_match(self, tok: str) -> bool:
        """
        Optional fuzzy match: allow minor spelling variants of known skills.

        Disabled by default. When enabled, a token that shares a 4-char stem
        with a known skill is accepted. This is a safety valve for typos in
        the master record, not a license to invent facts.
        """
        if not self.allow_fuzzy:
            return False
        stem = tok[:4]
        for v in self.vocab:
            if stem in v or v[:4] == stem:
                return True
        return False


def validate_resume(text: str, master: MasterRecord) -> ValidationResult:
    """Convenience wrapper."""
    return Validator(master).validate(text)
