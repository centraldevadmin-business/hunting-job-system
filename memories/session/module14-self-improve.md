# Module 14 — Self-Improvement Loop (COMPLETE)

Built 2026-01-20. 7/7 tests pass. Full suite: 172 passed, 3 failed (pre-existing reportlab ModuleNotFoundError in test_module4 + test_end_to_end_workflow — NOT related).

Files:
- src/self_improve/loop.py — SelfImproveLoop with weekly_review, detect_drift, prompt_recommendations, run
- tests/test_module14.py — 7 tests
- src/db/repository.py — added _iso_days_ago helper

Key gotchas:
- SQLite stores timestamps with SPACE separator ("2026-09-10 02:54:17"), not ISO "T". String comparison for windowing FAILS. Use _parse_ts() to convert to datetime before comparing.
- feedback_features.outcome_id must be UNIQUE per outcome. Hardcoding to 1 causes JOIN row multiplication (3 outcomes -> 5 rows). Fetch the auto-generated id via query_one after insert.
- detect_drift: recent = last period_days; prior = the window BEFORE that (period_days .. 2*period_days). Both windows need separate cutoffs.
- got_interview can be None (LEFT JOIN with no feedback_features row) — coerce with int(x or 0).
