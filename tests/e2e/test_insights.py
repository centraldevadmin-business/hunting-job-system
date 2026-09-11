"""
Section G — Insights page.
"""
from helpers import click_nav, assert_no_exception, main_content


def test_insights_loads_no_exception(authed_page):
    click_nav(authed_page, "Insights")
    assert_no_exception(authed_page)


def test_insights_renders_heading(authed_page):
    click_nav(authed_page, "Insights")
    assert "insights" in main_content(authed_page)


def test_insights_renders_status_breakdown(authed_page):
    click_nav(authed_page, "Insights")
    assert "Status Breakdown" in main_content(authed_page)


def test_insights_renders_company_performance(authed_page):
    click_nav(authed_page, "Insights")
    assert "Company Performance" in main_content(authed_page)


def test_insights_renders_stale_applications(authed_page):
    click_nav(authed_page, "Insights")
    assert "Stale" in main_content(authed_page)


def test_insights_renders_conversion_by_country(authed_page):
    click_nav(authed_page, "Insights")
    assert "Conversion by Country" in main_content(authed_page)
