"""
Section E — Track page.
"""
from helpers import click_nav, assert_no_exception, main_content, wait_for_idle


def test_track_loads_no_exception(authed_page):
    click_nav(authed_page, "Track")
    assert_no_exception(authed_page)


def test_track_renders_kpi_cards(authed_page):
    click_nav(authed_page, "Track")
    content = main_content(authed_page)
    assert "Applied" in content and "Interviews" in content


def test_track_renders_followups(authed_page):
    click_nav(authed_page, "Track")
    # With no follow-ups seeded, the page shows the empty-state message.
    content = main_content(authed_page)
    assert "No stale applications" in content or "Follow-ups needed" in content


def test_track_renders_timeline(authed_page):
    click_nav(authed_page, "Track")
    assert "Timeline" in main_content(authed_page)


def test_track_has_manual_status_expander(authed_page):
    click_nav(authed_page, "Track")
    assert "Record a status manually" in main_content(authed_page)


def test_track_record_status_manually(authed_page):
    """Open the expander and record a status."""
    click_nav(authed_page, "Track")
    expander = authed_page.locator('text=Record a status manually')
    if expander.count() > 0:
        expander.first.click()
        wait_for_idle(authed_page)
        rec_btn = authed_page.locator('button:has-text("Record status")')
        if rec_btn.count() > 0:
            rec_btn.first.click()
            wait_for_idle(authed_page)
            assert_no_exception(authed_page)
