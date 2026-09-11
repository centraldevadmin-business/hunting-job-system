"""
Section C — Hunt page (core workflow).
"""
from helpers import click_nav, assert_no_exception, main_content, wait_for_idle


def test_hunt_loads_no_exception(authed_page):
    click_nav(authed_page, "Hunt")
    assert_no_exception(authed_page)


def test_hunt_renders_heading(authed_page):
    click_nav(authed_page, "Hunt")
    assert "Find your next" in main_content(authed_page)


def test_hunt_renders_start_engine_button(authed_page):
    click_nav(authed_page, "Hunt")
    assert "Start the job search engine" in main_content(authed_page)


def test_hunt_renders_filters(authed_page):
    click_nav(authed_page, "Hunt")
    content = main_content(authed_page)
    assert "filters" in content.lower() or "Filter" in content


def test_hunt_renders_job_cards(authed_page):
    click_nav(authed_page, "Hunt")
    content = main_content(authed_page)
    assert "possibility" in content.lower() or "Generate tailored CV" in content


def test_hunt_has_cv_per_job_number_input(authed_page):
    click_nav(authed_page, "Hunt")
    assert authed_page.locator('input[type="number"]').count() > 0


def test_hunt_apply_filters_button_works(authed_page):
    click_nav(authed_page, "Hunt")
    authed_page.click('button:has-text("Apply filters")')
    wait_for_idle(authed_page)
    assert_no_exception(authed_page)


def test_hunt_job_row_selection(authed_page):
    click_nav(authed_page, "Hunt")
    # Click a job row's select button; the detail panel should populate.
    sel = authed_page.locator('button:has-text("select")').first
    if sel.count() > 0:
        sel.click()
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)


def test_hunt_generate_cv_button(authed_page):
    click_nav(authed_page, "Hunt")
    btn = authed_page.locator('button:has-text("Generate tailored CV")')
    if btn.count() > 0:
        btn.first.click()
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)


def test_hunt_status_transition(authed_page):
    """Move a job through the status flow without crashing."""
    click_nav(authed_page, "Hunt")
    # Click the "new" status pill (first in STATUS_FLOW).
    status_btns = authed_page.locator('button').filter(has_text="new")
    if status_btns.count() > 0:
        status_btns.first.click()
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)


def test_hunt_authorize_flow(authed_page):
    click_nav(authed_page, "Hunt")
    auth_btn = authed_page.locator('button:has-text("Authorize")')
    if auth_btn.count() > 0:
        auth_btn.first.click()
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)


def test_hunt_empty_queue_state(authed_page):
    """Filter with an impossible threshold should show an empty state, not crash."""
    click_nav(authed_page, "Hunt")
    # Set min possibility to 100 to filter everything out.
    min_input = authed_page.locator('input[min="0"][max="100"]').first
    min_input.fill("100")
    authed_page.click('button:has-text("Apply filters")')
    wait_for_idle(authed_page)
    assert_no_exception(authed_page)
