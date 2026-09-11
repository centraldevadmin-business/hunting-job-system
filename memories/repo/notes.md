# Hunting Job System — repo notes

## Runtime
- `uv run python ...` (Python 3.12.13). Streamlit dashboard on :8599, password `hunting2026`.
- SQLite `data/career.db`, WAL mode, `PRAGMA foreign_keys = ON` per connection.

## Verified facts
- Greenhouse API is the ONLY reliable ingestion path. Lever/Ashby/Workable JSON APIs all fail (404/non-JSON).
- Greenhouse API returns metadata only; JDs are empty. Enrichment module (`src/ingestion/enrichment.py`) fetches job-page HTML to fill JDs.
- Each `execute_sql`/`query_*` opens a fresh connection — PRAGMA does NOT persist.

## Gotchas / lessons
- `job_id` (normalized_id) contains `|`, `/`, `?` — illegal in filenames. Sanitize in `src/resume/renderer.py` before building PDF filename.
- `match_score` (0-100) is written as NULL by CompliancePipeline; filled by `src/scoring/matcher.py` (MatchScorer) in hunt_engine step 2b.
- `JobCard` has no `pdf_path` attribute — check `resumes` table directly for PDF existence.

## Scoring
- MatchScorer: skills 55% + role 25% + seniority 20%, minus 8 per red flag. Deterministic, no LLM.
