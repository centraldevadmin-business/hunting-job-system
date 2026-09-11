# Playwright E2E Test Suite — Hunting Job System

End-to-end tests that drive the Streamlit dashboard exactly like a human user:
log in, navigate every page, click every button, fill forms, and assert nothing
breaks.

## Prerequisites

- Python venv activated: `.venv/bin/python`
- Chromium installed for Playwright (already cached on this machine).
- The dashboard **running** on `http://localhost:8599`.

## 1. Start the dashboard

```bash
.venv/bin/streamlit run src/dashboard/app.py \
    --server.headless true \
    --server.port 8599 \
    --server.address 127.0.0.1
```

Leave this running in a separate terminal.

## 2. Run the tests

```bash
.venv/bin/python -m playwright test tests/e2e
```

### Options

```bash
# Custom URL / password
DASHBOARD_URL=http://localhost:8599 DASHBOARD_PASSWORD=hunting2026 \
    .venv/bin/python -m playwright test tests/e2e

# Single file
.venv/bin/python -m playwright test tests/e2e/test_hunt.py

# Single test
.venv/bin/python -m playwright test tests/e2e/test_auth.py::test_correct_password_logs_in

# HTML report
.venv/bin/python -m playwright test tests/e2e --reporter=html
# then: open playwright-report/index.html
```

## What's covered

| File | Section |
|------|---------|
| `test_auth.py` | Authentication & Session |
| `test_overview.py` | Overview page |
| `test_hunt.py` | Hunt page (core workflow) |
| `test_cv_manager.py` | CV Manager page |
| `test_track.py` | Track page |
| `test_analytics.py` | Analytics page |
| `test_insights.py` | Insights page |
| `test_learning.py` | Learning page (advanced layer) |
| `test_network.py` | Network page |
| `test_settings.py` | Settings page |
| `test_cross_cutting.py` | Cross-cutting / edge cases |

## Safety

- The `db_backup` fixture backs up `data/career.db` before each test and
  restores it after, so status mutations never corrupt your real database.
  Apply it to any test that mutates state:

  ```python
  def test_something(authed_page, db_backup):
      ...
  ```

## Notes

- Streamlit re-renders on every interaction, so helpers wait for the app to
  settle (`wait_for_idle`) before asserting.
- Network-dependent features (engine run, CV generation) degrade gracefully
  without API keys; tests guard optional interactions with `.count() > 0`.
