# Resume Generator Fix (Session 2026-09-07)

## Problem
Free-tier Gemini truncated resume bullets on 503 load — the model would return only a partial bullet, losing verified facts.

## Fix in `src/resume/generator.py`
- `_rephrase(self, ach, jd_text)`: Now produces only a SHORT lead clause (max_tokens=40, temperature=0.3, prompt="Write a short lead clause for the source bullet above."). System prompt: "Output EXACTLY ONE lead clause of at most 10 words." Falls back to `ach.bullet` on any failure.
- `_assemble(ach, lead)` (static): Deterministically re-attaches verified facts from structured data so truncation can never drop them. Dedup: metric appended as `({ach.metric})` only if not already in lead; tools appended as `using {tools}` only if NONE of the individual tool tokens (split on ",") appear in lead; company appended as `at {ach.company}` only if not in lead. Joins with "; ", capitalizes first letter, strips trailing period.
- `generate()`: loop now does `lead = self._rephrase(...)` then `bullet = self._assemble(ach, lead)`.

## Note
The model ignores the 10-word limit and returns the full source bullet as the "lead" — but assembly dedup makes this harmless (no duplication). Result: complete, clean, integrity-clean bullets.

## DB schema
`resumes` table (schema.sql): id, job_id, pdf_path, resume_text, validated, generated_at. Only hunt_engine.py writes to it (from dashboard).
