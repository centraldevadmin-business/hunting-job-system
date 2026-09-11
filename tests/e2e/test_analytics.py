"""
Section F — Analytics page.
"""
from helpers import click_nav, assert_no_exception, main_content


def test_analytics_loads_no_exception(authed_page):
    click_nav(authed_page, "Analytics")
    assert_no_exception(authed_page)


def test_analytics_renders_funnel_kpis(authed_page):
    click_nav(authed_page, "Analytics")
    content = main_content(authed_page)
    assert "Collected" in content and "Conversion" in content


def test_analytics_renders_calibration(authed_page):
    click_nav(authed_page, "Analytics")
    assert "Calibration" in main_content(authed_page)


def test_analytics_renders_top_levers(authed_page):
    click_nav(authed_page, "Analytics")
    assert "levers" in main_content(authed_page).lower()


def test_analytics_renders_company_leaderboard(authed_page):
    click_nav(authed_page, "Analytics")
    content = main_content(authed_page)
    assert "leaderboard" in content.lower() or "Company" in content
