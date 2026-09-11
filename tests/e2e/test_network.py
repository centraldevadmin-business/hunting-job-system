"""
Section I — Network page.
"""
from helpers import click_nav, assert_no_exception, main_content, wait_for_idle


def test_network_loads_no_exception(authed_page):
    click_nav(authed_page, "Network")
    assert_no_exception(authed_page)


def test_network_renders_referral_paths(authed_page):
    click_nav(authed_page, "Network")
    content = main_content(authed_page)
    assert "Warm referral paths" in content or "referral" in content.lower()


def test_network_referral_copy_draft(authed_page):
    click_nav(authed_page, "Network")
    btn = authed_page.locator('button:has-text("Copy draft")')
    if btn.count() > 0:
        btn.first.click()
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)


def test_network_renders_interview_prep(authed_page):
    click_nav(authed_page, "Network")
    content = main_content(authed_page)
    assert "Interview prep" in content or "STAR" in content


def test_network_grade_answer(authed_page):
    """Paste an answer and grade it."""
    click_nav(authed_page, "Network")
    grade_btn = authed_page.locator('button:has-text("Grade answer")')
    if grade_btn.count() > 0:
        authed_page.get_by_label("Paste your answer to a question:").fill(
            "I led a project that reduced costs using SQL and Power BI."
        )
        grade_btn.first.click()
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)
