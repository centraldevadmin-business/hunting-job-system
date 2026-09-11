"""
Section H — Learning page (advanced layer).
"""
from helpers import click_nav, assert_no_exception, main_content, wait_for_idle


def test_learning_loads_no_exception(authed_page):
    click_nav(authed_page, "Learning")
    assert_no_exception(authed_page)


def test_learning_renders_self_tuning(authed_page):
    click_nav(authed_page, "Learning")
    content = main_content(authed_page)
    assert "Self-tuning" in content or "self-tuning" in content.lower()


def test_learning_renders_weights(authed_page):
    click_nav(authed_page, "Learning")
    assert "Scoring weights" in main_content(authed_page)


def test_learning_renders_self_improvement(authed_page):
    click_nav(authed_page, "Learning")
    content = main_content(authed_page)
    assert "Self-improvement" in content or "self-improvement" in content.lower()


def test_learning_renders_prompt_library(authed_page):
    click_nav(authed_page, "Learning")
    assert "Prompt library" in main_content(authed_page)


def test_learning_approve_prompt_button(authed_page):
    """Click 'Approve new prompt' if a recommendation exists."""
    click_nav(authed_page, "Learning")
    btn = authed_page.locator('button:has-text("Approve new prompt")')
    if btn.count() > 0:
        btn.first.click()
        wait_for_idle(authed_page)
        assert_no_exception(authed_page)

