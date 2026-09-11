# Hunt UI Redesign (Session 2026-09-07)

## What changed
Rewrote `src/dashboard/app.py` from 3 cluttered tabs (Overview/Hunt/Analytics) into a single clean page centered on ONE button: "🚀 Start the hunt".

## New flow
1. Login gate (password `hunting2026`, or `DASHBOARD_PASSWORD` env).
2. One "🚀 Start the hunt" button runs the full pipeline via `src/dashboard/hunt_engine.py`.
3. Live progress bar (Ingesting → Scoring → Verifying → Tailoring → Done).
4. Clean ranked list of best-fit jobs: possibility pill (color-coded), title/company, fraud+company badges, salary, match bar, "Generate tailored CV" button, JD expander, tailored-CV preview, status pills, authorize/submit.

## New file: `src/dashboard/hunt_engine.py`
`run_hunt(progress=, resume_top_k=5)` chains IngestionEngine → CompliancePipeline → FraudPipeline, ranks eligible+fraud-clean jobs by possibility, builds tailored CVs for top matches, persists them to the `resumes` table.

## Gotchas learned
- `st.HTML` does NOT exist in Streamlit 1.63.0. Use `st.markdown(html, unsafe_allow_html=True)` instead.
- scored_jobs has a FK to raw_jobs — seed both when testing.
- Possibility = match × geo_gate × fraud_gate × historical_rate (0.15). Stripe jobs in DB are geo-ineligible → 0% (legitimate, not a bug).
- Edit .py files with `replace_string_in_file`, NOT `edit_notebook_file` (fails "Missing viewType").

## Verified
- 125 offline tests pass.
- Engine runs end-to-end in browser; CV PDF generates + previews inline + downloads.
