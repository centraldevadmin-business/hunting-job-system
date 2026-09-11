"""
Reusable Playwright helpers for the Hunting Job System E2E suite.

Streamlit re-renders the whole app on every interaction (st.rerun()), so these
helpers wait for the app to settle before asserting.
"""
from playwright.sync_api import Page, expect


def login(page: Page, password: str, timeout: int = 30_000) -> None:
    """Log in and wait for the sidebar to appear."""
    page.goto(page.base_url or "http://localhost:8599")
    page.wait_for_selector('input[type="password"]', timeout=timeout)
    page.fill('input[type="password"]', password)
    page.click('button:has-text("Sign in")')
    page.wait_for_selector('section[data-testid="stSidebar"]', timeout=timeout)


def nav(page: Page, label: str, timeout: int = 15_000) -> None:
    """Click a sidebar nav item by its visible text (Streamlit radio)."""
    page.evaluate(
        """(label) => {
            const opts = document.querySelectorAll('section[data-testid="stSidebar"] [data-testid="stRadioOption"]');
            for (const o of opts) {
                const text = o.textContent || '';
                if (text.trim() === label) {
                    o.click();
                    return;
                }
            }
            throw new Error('nav item not found: ' + label);
        }""",
        label,
    )
    page.wait_for_timeout(1500)


def wait_for_idle(page: Page, timeout: int = 30_000) -> None:
    """Wait until Streamlit is idle (no script running, no exception)."""
    # Streamlit sets data-test-script-state="notRunning" when idle.
    page.wait_for_function(
        "() => document.querySelector('[data-test-script-state]')?.getAttribute('data-test-script-state') === 'notRunning'",
        timeout=timeout,
    )


def has_exception(page: Page) -> bool:
    """True if Streamlit rendered an exception banner."""
    return page.evaluate(
        "() => !!document.querySelector('[data-testid=\"stException\"]')"
    )


def main_content(page: Page) -> str:
    """Return the visible text of the main content area."""
    return page.evaluate(
        "() => { const m = document.querySelector('section[data-testid=\"stMain\"]'); return m ? m.textContent : ''; }"
    )


def main_html(page: Page) -> str:
    """Return the inner HTML of the main content area."""
    return page.evaluate(
        "() => { const m = document.querySelector('section[data-testid=\"stMain\"]'); return m ? m.innerHTML : ''; }"
    )


def assert_no_exception(page: Page) -> None:
    """Assert Streamlit has not rendered an exception."""
    assert not has_exception(page), "Streamlit rendered an exception"


def assert_idle(page: Page) -> None:
    """Assert Streamlit is idle and exception-free."""
    wait_for_idle(page)
    assert_no_exception(page)


def click_nav(page: Page, label: str) -> None:
    """Navigate and wait for the page to settle."""
    nav(page, label)
    wait_for_idle(page)
