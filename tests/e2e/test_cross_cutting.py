"""
Section K — Cross-Cutting / Edge Cases.

These run against every page to guarantee no page ever renders a Streamlit
exception, and that the app always settles to an idle state.
"""
from helpers import click_nav, assert_no_exception, wait_for_idle

PAGES = [
    "Overview", "Hunt", "CV Manager", "Track", "Analytics",
    "Insights", "Learning", "Network", "Settings",
]


def test_every_page_loads_without_exception(authed_page):
    """Each page must render without a Streamlit exception."""
    for page_name in PAGES:
        click_nav(authed_page, page_name)
        assert_no_exception(authed_page)


def test_every_page_settles_to_idle(authed_page):
    """Each page must return to the 'notRunning' idle state."""
    for page_name in PAGES:
        click_nav(authed_page, page_name)
        wait_for_idle(authed_page)


def test_sidebar_has_all_nav_items(authed_page):
    """All 9 nav items must be present in the sidebar."""
    click_nav(authed_page, "Overview")
    expected = set(PAGES)
    found = authed_page.evaluate(
        """() => {
            const opts = document.querySelectorAll('section[data-testid="stSidebar"] [data-testid="stRadioOption"]');
            return Array.from(opts).map(o => {
                return (o.textContent || '').trim();
            });
        }"""
    )
    assert expected.issubset(set(found)), f"Missing nav items: {expected - set(found)}"


def test_no_page_error_events(authed_page):
    """Capture any JS page errors during navigation."""
    errors = []
    authed_page.on("page error", lambda e: errors.append(str(e)))
    for page_name in PAGES:
        click_nav(authed_page, page_name)
    assert errors == [], f"JS page errors: {errors}"
