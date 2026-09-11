"""
Section D — CV Manager page.
"""
from helpers import click_nav, assert_no_exception, main_content, wait_for_idle


def test_cv_manager_loads_no_exception(authed_page):
    click_nav(authed_page, "CV Manager")
    assert_no_exception(authed_page)


def test_cv_manager_renders_master_profile(authed_page):
    click_nav(authed_page, "CV Manager")
    assert "Master Profile" in main_content(authed_page)


def test_cv_manager_renders_achievement_editor(authed_page):
    click_nav(authed_page, "CV Manager")
    assert "Edit your accomplishments" in main_content(authed_page)


def test_cv_manager_renders_bulk_actions(authed_page):
    click_nav(authed_page, "CV Manager")
    assert "Bulk actions" in main_content(authed_page)


def test_cv_manager_renders_tailored_resumes(authed_page):
    click_nav(authed_page, "CV Manager")
    assert "Tailored Resumes" in main_content(authed_page)


def test_cv_manager_add_accomplishment(authed_page):
    """Add a new accomplishment via the editor form."""
    click_nav(authed_page, "CV Manager")
    form = authed_page.locator('form[key="new_ach"]')
    if form.count() > 0:
        form.fill('textarea', "Led a cross-team initiative to ship a new reporting feature")
        form.fill('input[key="new_ach_tools"]', "SQL, Jira")
        form.fill('input[key="new_ach_metric"]', "30% faster reporting")
        form.click('button:has-text("Add accomplishment")')
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)


def test_cv_manager_clear_all_cvss(authed_page):
    click_nav(authed_page, "CV Manager")
    # The button lives inside a collapsed "Bulk actions" expander.
    expander = authed_page.locator('div[data-testid="stExpander"]').filter(has_text="⚡ Bulk actions").first
    if expander.count() > 0:
        # Click the expander's toggle (the arrow button) to expand it.
        toggle = expander.locator('button').first
        if toggle.count() > 0 and not toggle.is_visible():
            expander.click()
        elif expander.evaluate("el => el.getAttribute('aria-expanded') === 'false'"):
            expander.click()
    wait_for_idle(authed_page)
    btn = authed_page.locator('button:has-text("Clear all generated CVs")')
    if btn.count() > 0:
        btn.click()
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)
