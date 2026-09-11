"""
Playwright E2E fixtures for the Hunting Job System dashboard.

Builds on the `pytest-playwright` plugin, which provides the built-in
`page` and `browser` fixtures. We add:

  - `password`      : the dashboard password (from env or default).
  - `authed_page`   : a page already logged in (function-scoped, reused within
                      a test file).
  - `db_backup`     : backs up career.db before a test and restores it after,
                      so status mutations never corrupt the real database.

Run the dashboard first:
    .venv/bin/streamlit run src/dashboard/app.py \
        --server.headless true --server.port 8599 --server.address 127.0.0.1

Then:
    .venv/bin/python -m pytest tests/e2e -q

Browser launch is configured in pytest.ini (chromium, headless).
"""
import os
import shutil
from datetime import datetime

import pytest

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8599")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "hunting2026")

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "career.db",
)


def _backup_db():
    """Copy career.db -> a timestamped backup, return the backup path."""
    os.makedirs(os.path.dirname(DB_PATH) + "/backups_e2e", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(os.path.dirname(DB_PATH), f"career.db.backup.e2e.{stamp}")
    shutil.copy2(DB_PATH, backup)
    return backup


def _restore_db(backup):
    """Restore career.db from the backup taken before the test."""
    if os.path.exists(backup):
        shutil.copy2(backup, DB_PATH)


@pytest.fixture()
def password():
    return PASSWORD


@pytest.fixture()
def authed_page(page, password):
    """A logged-in page, reused across tests in the same file."""
    # Navigate and log in.
    page.goto(BASE_URL)
    page.wait_for_selector('input[type="password"]', timeout=30_000)
    page.fill('input[type="password"]', password)
    page.click('button:has-text("Sign in")')
    page.wait_for_selector('section[data-testid="stSidebar"]', timeout=30_000)
    yield page
    page.close()


@pytest.fixture()
def db_backup():
    """Back up career.db before the test and restore it after."""
    backup = _backup_db()
    yield backup
    _restore_db(backup)
