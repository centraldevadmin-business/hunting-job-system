"""
Section B — Overview page.
"""
from helpers import click_nav, assert_no_exception, main_content


def test_overview_loads_no_exception(authed_page):
    click_nav(authed_page, "Overview")
    assert_no_exception(authed_page)


def test_overview_renders_kpi_cards(authed_page):
    click_nav(authed_page, "Overview")
    content = main_content(authed_page)
    assert "Collected" in content
    assert "Scored" in content


def test_overview_renders_funnel(authed_page):
    click_nav(authed_page, "Overview")
    content = main_content(authed_page)
    assert "Funnel" in content or "Application Funnel" in content


def test_overview_renders_market_signal(authed_page):
    click_nav(authed_page, "Overview")
    content = main_content(authed_page)
    assert "Market Signal" in content


def test_overview_renders_profile(authed_page):
    click_nav(authed_page, "Overview")
    content = main_content(authed_page)
    assert "Profile" in content


def test_overview_renders_target_companies(authed_page):
    click_nav(authed_page, "Overview")
    content = main_content(authed_page)
    assert "Target Companies" in content or "Companies" in content
