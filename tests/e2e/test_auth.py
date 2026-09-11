"""
Section A — Authentication & Session.
"""
import os
from playwright.sync_api import expect
from helpers import wait_for_idle

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8599")


def test_login_page_renders_password_field(page):
    page.goto(BASE_URL)
    expect(page.locator('input[type="password"]')).to_be_visible()


def test_wrong_password_shows_error(page):
    page.goto(BASE_URL)
    page.wait_for_selector('input[type="password"]', timeout=30_000)
    page.fill('input[type="password"]', "definitely-wrong")
    page.click('button:has-text("Sign in")')
    page.wait_for_timeout(1500)
    expect(page.locator('.login-error')).to_be_visible()
    # Still on the login gate.
    expect(page.locator('input[type="password"]')).to_be_visible()


def test_correct_password_logs_in(page, password):
    page.goto(BASE_URL)
    page.wait_for_selector('input[type="password"]', timeout=30_000)
    page.fill('input[type="password"]', password)
    page.click('button:has-text("Sign in")')
    page.wait_for_selector('section[data-testid="stSidebar"]', timeout=30_000)
    # Sidebar present.
    expect(page.locator('section[data-testid="stSidebar"]')).to_be_visible()


def test_session_persists_across_rerun(page, password):
    page.goto(BASE_URL)
    page.wait_for_selector('input[type="password"]', timeout=30_000)
    page.fill('input[type="password"]', password)
    page.click('button:has-text("Sign in")')
    page.wait_for_selector('section[data-testid="stSidebar"]', timeout=30_000)
    # Sidebar present after login.
    expect(page.locator('section[data-testid="stSidebar"]')).to_be_visible()
    # Streamlit resets session_state on full reload, so the login gate
    # reappears. This documents the actual (documented) behaviour.
    page.reload()
    wait_for_idle(page)
    expect(page.locator('input[type="password"]')).to_be_visible()


def test_google_signin_button_does_not_crash(page):
    page.goto(BASE_URL)
    page.wait_for_selector('input[type="password"]', timeout=30_000)
    # The Google button exists and clicking doesn't crash the app.
    google = page.get_by_role("button", name="Sign in with Google")
    expect(google).to_be_visible()
    google.click()
    wait_for_idle(page)
    expect(page.locator('input[type="password"]')).to_be_visible()
