"""
Section J — Settings page.
"""
from helpers import click_nav, assert_no_exception, main_content, wait_for_idle


def test_settings_loads_no_exception(authed_page):
    click_nav(authed_page, "Settings")
    assert_no_exception(authed_page)


def test_settings_renders_password_expander(authed_page):
    click_nav(authed_page, "Settings")
    assert "Dashboard password" in main_content(authed_page)


def test_settings_renders_target_countries(authed_page):
    click_nav(authed_page, "Settings")
    assert "Target countries" in main_content(authed_page)


def test_settings_renders_target_companies(authed_page):
    click_nav(authed_page, "Settings")
    assert "Target companies" in main_content(authed_page)


def test_settings_renders_storage_info(authed_page):
    click_nav(authed_page, "Settings")
    content = main_content(authed_page)
    assert "Storage" in content or "Database" in content


def test_settings_update_password_button(authed_page):
    """Click 'Update password' with the same value — should not crash."""
    click_nav(authed_page, "Settings")
    pw_input = authed_page.locator('input[type="password"]')
    if pw_input.count() > 0:
        pw_input.fill("hunting2026")
        authed_page.click('button:has-text("Update password")')
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)
