# Lessons Learned — Hunting Job System

## Environment
- Python 3.12.13 via `uv python install 3.12`. Run everything with `uv run python ...` / `uv run pytest`.
- System Python is 3.9.6 — never use bare `python`/`pytest`.
- `uv` at `/Users/nafiz/.local/bin/uv`.

## Testing gotchas
- `pytest` on the full suite hangs if `IngestionEngine.ingest_all()` runs against real config — it makes live network calls to all 7 targets. In tests, monkeypatch `config_loader.load_config` to return a single target.
- `db_path()` returns a `pathlib.Path`, not a string. Mocks must return a `Path` (`.parent.mkdir` is called).
- `query_all`/`query_one` return dicts (sqlite3.Row → dict), so index by `r["id"]`, not `r[0]`.
- `raw_jobs` table has NO `confidence` column — confidence lives on the `Posting` object, not persisted.

## Domain gotchas
- Greenhouse `gh_jid` is the unique job id. `_normalize_url` must PRESERVE `gh_jid` while stripping utm/ref tracking params, or two distinct jobs collapse into one.
- Greenhouse board slug is NOT derivable from `https://stripe.com/jobs/search` — config needs an explicit `board:` field.
- Deel = Workable (needs API key), Notion = Ashby (needs API key). Only Stripe works via public Greenhouse API. Direct-careers pages (GitLab, Automattic, Doist, Zapier) are JS-rendered SPAs — HTML parse returns 0.

## Architecture
- Ingestion fallback chain: tier 1 Greenhouse API (high) → tier 2 HTML fallback (low) → tier 3 direct scrape (low) → tier 4 manual paste (low). First non-empty tier wins.
- Dedup key: `slug(company)|slug(role_title)|normalized_url`.

## Module 2 (Geo-Compliance & JD Distiller)
- Geo filter verdict priority: REJECT > ACCEPT > REVIEW > no-signal. Reject keywords win even if accept keywords present.
- `jd_distiller._detect_red_flags` regexes need `re.I` flag — `\bunpaid\b` without it misses "Unpaid" (capital). Always pass `re.I` to red-flag regexes.
- Equipment red flag regex matches "buy equipment" / "need to buy equipment" — test input must contain that phrasing.
- LLM (Grok) is only used by the distiller; regex fallback keeps it $0/no-key. `distill()` returns `source="llm"` or `"regex"`.
- Pipeline writes `scored_jobs` (geo_eligible 0/1, geo_confidence, status) + `jd_distill`. Use INSERT ... ON CONFLICT(id) DO UPDATE.

## Module 3 (Anti-Fraud / Risk Engine)
- FraudEngine: composite 0–100, green ≥70, amber ≥40, red <40. Red = auto-reject.
- Domain mismatch (posting host != known company domain) → score 0 (red). Strongest signal.
- Payment/equipment/Telegram/gift-card patterns → score 0 (red). Regex `buy\s+(?:\w+\s+)*equipment` matches "buy equipment" AND "buy your own equipment".
- Salary anomaly: posted > 3× min_salary → −35 (amber, not red). $120k with min $30k IS an anomaly (3×$30k=$90k).
- Free-domain contact email (gmail/yahoo/etc) → −35 (amber).
- FraudPipeline must `_ensure_scored(job_id)` before UPDATE — Module 2 may not have run, so no scored_jobs row exists yet.
- Added `company_reviews` table to schema (company PK, legitimacy, activity, composite, level).
- CompanyReviewEngine: legitimacy (company page 40 + contact 30 + address 30) weighted 0.6, activity (live postings) 0.4.

## Module 5 (Dashboard — Streamlit)
- Possibility = match_score × geo_gate × fraud_gate × historical_rate (multiplicative). Geo-ineligible → 0 (hard gate). Fraud red (score 0) → 0. Amber fraud scales proportionally. Unknown fraud_score → gate 1.0.
- Historical rate learned via Laplace smoothing: (k + prior*weight)/(n + weight), prior=0.15, weight=10. Labeled "unproven" until 30+ outcomes (outcomes_before_trusted).
- `load_config()` takes NO args (unlike `load_master_record(settings)`).
- `scored_jobs` has a FK to `raw_jobs` — any test inserting into scored_jobs must seed a raw_jobs row first (else IntegrityError).
- Dashboard degrades gracefully: when scored_jobs is empty (Module 2 not run), `list_job_cards` falls back to raw_jobs so the UI still opens.
- Streamlit boots headless: `uv run streamlit run src/dashboard/app.py --server.headless true --server.port 8599`; health at `/_stcore/health` returns "ok". macOS has no `timeout` command (exit 127) — use nohup + background.
- Login gate: default password `hunting2026`, override via `DASHBOARD_PASSWORD` env var. Auth stored in `st.session_state["authenticated"]`. `app.py` inserts the project root into `sys.path` so `src` imports work under `streamlit run`.
- `.streamlit/config.toml` sets headless=true, port 8599, CORS/xsrf off. Add `sidebarTextColor` to the theme to silence the invalid-color console warning.

## Module 13 (Multi-Agent Orchestrator)
- `src/agents/messages.py`: `Message` dataclass (kind, payload dict, correlation_id, status, detail, result) + `with_status(status, detail, result)` returns updated copy.
- `src/agents/orchestrator.py`: 7 agents (Ingestor, Compliance, Fraud, Match, Resume, Validator, Tracker) each with `.handle(msg)`. `Orchestrator.route(msg)` accepts a Message OR a plain dict {kind,payload,correlation_id}.
- Resume<->Validator debate: ResumeAgent returns needs_review with human_review bullets; ValidatorAgent accepts a bullet if it traces VERBATIM to a verified master fact (company/achievement/skill substring match), else leaves it flagged. `debate()` must return `result={"human_review": [], "accepted": accepted}` on success — NOT the old resume_reply.result.
- Zero new facts: agents only read master record / deterministic engines. LLM never injects facts.
- reportlab is NOT installed → ResumeAgent returns 'error' status. Tests must accept 'error' as a valid status for resume-related tests.
- FK chain in tests: seed raw_jobs (explicit id) → scored_jobs (matching id) → applications.

## Module 14 (Self-Improvement Loop)
- `src/self_improve/loop.py`: SelfImproveLoop with weekly_review, detect_drift, prompt_recommendations, run.
- SQLite stores timestamps with SPACE separator ("2026-09-10 02:54:17"), NOT ISO "T". String comparison for windowing FAILS. Added `_parse_ts()` helper to convert to datetime before comparing.
- `feedback_features.outcome_id` must be UNIQUE per outcome. Hardcoding to 1 causes JOIN row multiplication (3 outcomes -> 5 rows). Fetch auto-generated id via query_one after insert.
- detect_drift: recent = last period_days; prior = window BEFORE that (period_days .. 2*period_days). Both windows need separate cutoffs.
- `got_interview` can be None (LEFT JOIN with no feedback_features row) — coerce with `int(x or 0)`.
- Added `repository._iso_days_ago(days)` helper for windowed queries.

## Module 15 — Local Security Layer (encrypt/backup/audit)
- `src/security/encrypt.py`: Fernet encryption of DB at rest. Two modes: random key file (default) or PBKDF2-derived key from `security.encryption.passphrase` (salt stored in `<keyfile>.salt`). Key file ALWAYS holds base64 Fernet key (not raw 32 bytes).
- `src/security/backup.py`: timestamped backups (use `%f` microseconds in filename to avoid same-second collisions), prune (keep N), restore.
- `src/security/audit.py`: record/recent/since/summarize/count. `since()` MUST use ISO `T` format cutoff (audit stores ISO UTC via now_iso) — space-separated string comparison FAILS because 'T' > ' ' in ASCII.
- Test gotcha: `tmp_db` fixture only overrides `paths.db`, NOT `paths.backups` — backups write to real `data/backups`. Tests need `_tmp_settings(tmp_path)` helper.
- `status()` key_exists is False until encrypt_db() is called.

## Dashboard Learning page (Phase 8)
- Added "🧠 Learning" page to dashboard app.py between Insights and Network.
- data_access.py helpers: `learning_result()`, `weekly_review()`, `drift_report()`, `prompt_recommendations()`, `security_status()`, `audit_summary()`.
- Learning engine (Module 8) reads from `applications` table (status in INTERVIEW_STATUSES={interview,final,offer} → got_interview). `historical_rate()` is Bayesian-smoothed: (k + 0.15*10)/(n + 10), returns 15% prior when n==0.
- Self-improve loop (Module 14) reads from `application_outcomes` + `feedback_features`. `got_interview` column is in `feedback_features`, NOT `application_outcomes`.
- Weekly review filters by `updated_at` in window using `_parse_ts()` (handles space-separated SQLite timestamps).
- Test gotcha: seed BOTH `applications` (for learning engine) AND `application_outcomes`+`feedback_features` (for self-improve loop).
- CSS: use `ui-warning` for drift alerts (ui-error not defined; only ui-info/ui-success/ui-warning exist).

## Test environment note
- System Python 3.9.6 lacks reportlab → 3 reportlab failures in test_module4/test_end_to_end_workflow.
- venv Python (3.12) has reportlab → those 3 pass, but test_module8::test_learn_weights_with_signal fails with `assert -0.0 != 0`.
- The -0.0 bias is a pre-existing bug in LearningEngine.learn_weights(): for a perfectly separable dataset (all interviews have match=0.85, all applied have match=0.40), logistic regression drives bias → 0.0. Not related to Phase 8 dashboard work.
- Run tests with `.venv/bin/python -m pytest` for the full green suite (192 passed).
