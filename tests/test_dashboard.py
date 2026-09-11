"""
Full end-to-end automated test suite for the Hunting Job System dashboard.

Runs as a normal user would: log in, visit every page, click every button,
fill forms, and verify nothing breaks.

Usage:
    uv run python tests/test_dashboard.py

Exits 0 if all tests pass, 1 if any fail.
"""
import os
import sys
import time
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8599")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "hunting2026")

# --------------------------------------------------------------------------- #
# Tiny test harness
# --------------------------------------------------------------------------- #
_results = []


def record(name, passed, detail=""):
    _results.append((name, passed, detail))
    mark = "PASS" if passed else "FAIL"
    line = f"[{mark}] {name}"
    if detail and not passed:
        line += f"  — {detail}"
    print(line)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def login(page, password):
    """Log in and return True on success."""
    page.goto(BASE_URL)
    page.wait_for_selector('input[type="password"]', timeout=15000)
    page.fill('input[type="password"]', password)
    page.click('button:has-text("Sign in")')
    page.wait_for_timeout(2000)
    # After login the sidebar should be present.
    page.wait_for_selector('section[data-testid="stSidebar"]', timeout=15000)
    return True


def nav(page, label):
    """Click a sidebar nav item by its visible text."""
    page.evaluate(
        """(label) => {
            const radios = document.querySelectorAll('section[data-testid="stSidebar"] [role="radio"]');
            for (const r of radios) {
                const p = r.querySelector('p');
                if (p && p.textContent.trim() === label) {
                    r.click();
                    break;
                }
            }
        }""",
        label,
    )
    page.wait_for_timeout(1500)


def script_state(page):
    return page.evaluate(
        "() => document.querySelector('[data-test-script-state]')?.getAttribute('data-test-script-state')"
    )


def main_content(page):
    return page.evaluate(
        "() => { const m = document.querySelector('section[data-testid=\"stMain\"]'); return m ? m.textContent : ''; }"
    )


def main_html(page):
    return page.evaluate(
        "() => { const m = document.querySelector('section[data-testid=\"stMain\"]'); return m ? m.innerHTML : ''; }"
    )


def has_exception(page):
    return page.evaluate(
        "() => { const e = document.querySelector('[data-testid=\"stException\"]'); return !!e; }"
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("console", lambda msg: print(f"  [console] {msg.type}: {msg.text}"))
        page.on("page error", lambda err: print(f"  [page error] {err}"))

        # --- 1. Login gate -------------------------------------------------- #
        print("\n=== LOGIN ===")
        page.goto(BASE_URL)
        try:
            page.wait_for_selector('input[type="password"]', timeout=15000)
            record("Login page renders password field", True)
        except Exception as e:
            record("Login page renders password field", False, str(e))
            # If login page didn't render, abort.
            browser.close()
            return

        # Wrong password should show error.
        page.fill('input[type="password"]', "wrongpassword")
        page.click('button:has-text("Sign in")')
        page.wait_for_timeout(1500)
        err = page.locator('.login-error').count()
        record("Wrong password shows error", err > 0, f"error elements: {err}")

        # Correct password.
        login(page, PASSWORD)
        record("Correct password logs in", True)

        # Sidebar present with all nav items.
        nav_labels = ["Overview", "Hunt", "CV Manager", "Track", "Analytics", "Insights", "Settings"]
        for label in nav_labels:
            found = page.evaluate(
                "(l) => { const r = document.querySelectorAll('section[data-testid=\"stSidebar\"] [role=\"radio\"]'); for (const x of r) { const p = x.querySelector('p'); if (p && p.textContent.trim() === l) return true; } return false; }",
                label,
            )
            record(f"Sidebar shows nav item: {label}", found)

        # --- 2. Overview page ---------------------------------------------- #
        print("\n=== OVERVIEW ===")
        nav(page, "Overview")
        ss = script_state(page)
        record("Overview: script completed (notRunning)", ss == "notRunning", f"state={ss}")
        record("Overview: no exception", not has_exception(page))
        mc = main_content(page)
        record("Overview: renders Dashboard heading", "Dashboard" in mc)
        record("Overview: renders KPI cards", mc.count("Collected") > 0 and mc.count("Scored") > 0)
        record("Overview: renders funnel section", "Application Funnel" in mc or "Funnel" in mc)
        record("Overview: renders market signal", "Market Signal" in mc)
        record("Overview: renders your profile", "Your Profile" in mc)
        record("Overview: renders target companies", "Target Companies" in mc or "Companies" in mc)

        # --- 3. Hunt page --------------------------------------------------- #
        print("\n=== HUNT ===")
        nav(page, "Hunt")
        ss = script_state(page)
        record("Hunt: script completed", ss == "notRunning", f"state={ss}")
        record("Hunt: no exception", not has_exception(page))
        mc = main_content(page)
        record("Hunt: renders Job Search heading", "Job Search" in mc)
        record("Hunt: renders Start engine button", "Start the job search engine" in mc)
        record("Hunt: renders filters section", "Filters" in mc)
        record("Hunt: renders job count", "jobs" in mc)
        # Job cards should exist (94 cards in DB).
        record("Hunt: renders job cards", mc.count("possibility") > 0 or mc.count("Generate tailored CV") > 0)
        # Number input present.
        kt = page.locator('input[type="number"]').count()
        record("Hunt: has CVs-per-job number input", kt > 0, f"count={kt}")

        # Apply filters button.
        page.click('button:has-text("Apply filters")')
        page.wait_for_timeout(1500)
        record("Hunt: Apply filters button works", script_state(page) == "notRunning")

        # --- 4. CV Manager page -------------------------------------------- #
        print("\n=== CV MANAGER ===")
        nav(page, "CV Manager")
        ss = script_state(page)
        record("CV Manager: script completed", ss == "notRunning", f"state={ss}")
        record("CV Manager: no exception", not has_exception(page))
        mc = main_content(page)
        record("CV Manager: renders Master Profile", "Master Profile" in mc)
        record("CV Manager: renders achievement editor", "Edit your accomplishments" in mc)
        record("CV Manager: renders bulk actions", "Bulk actions" in mc)
        record("CV Manager: renders Tailored Resumes", "Tailored Resumes" in mc)

        # --- 5. Track page -------------------------------------------------- #
        print("\n=== TRACK ===")
        nav(page, "Track")
        ss = script_state(page)
        record("Track: script completed", ss == "notRunning", f"state={ss}")
        record("Track: no exception", not has_exception(page))
        mc = main_content(page)
        record("Track: renders KPI cards", mc.count("Applied") > 0 and mc.count("Interviews") > 0)
        record("Track: renders follow-ups section", "Follow-ups" in mc)
        record("Track: renders timeline", "Timeline" in mc)
        record("Track: has manual status expander", "Record a status manually" in mc)

        # --- 6. Analytics page --------------------------------------------- #
        print("\n=== ANALYTICS ===")
        nav(page, "Analytics")
        ss = script_state(page)
        record("Analytics: script completed", ss == "notRunning", f"state={ss}")
        record("Analytics: no exception", not has_exception(page))
        mc = main_content(page)
        record("Analytics: renders funnel KPIs", mc.count("Collected") > 0 and mc.count("Conversion") > 0)
        record("Analytics: renders calibration", "Calibration" in mc)
        record("Analytics: renders top levers", "levers" in mc.lower())
        record("Analytics: renders company leaderboard", "leaderboard" in mc.lower() or "Company" in mc)

        # --- 7. Insights page ---------------------------------------------- #
        print("\n=== INSIGHTS ===")
        nav(page, "Insights")
        ss = script_state(page)
        record("Insights: script completed", ss == "notRunning", f"state={ss}")
        record("Insights: no exception", not has_exception(page))
        mc = main_content(page)
        record("Insights: renders heading", "Insights" in mc)
        record("Insights: renders status breakdown", "Status Breakdown" in mc)
        record("Insights: renders company performance", "Company Performance" in mc)
        record("Insights: renders stale applications", "Stale" in mc)
        record("Insights: renders conversion by country", "Conversion by Country" in mc)

        # --- 8. Settings page ---------------------------------------------- #
        print("\n=== SETTINGS ===")
        nav(page, "Settings")
        ss = script_state(page)
        record("Settings: script completed", ss == "notRunning", f"state={ss}")
        record("Settings: no exception", not has_exception(page))
        mc = main_content(page)
        record("Settings: renders password expander", "Dashboard password" in mc)
        record("Settings: renders target countries", "Target countries" in mc)
        record("Settings: renders target companies", "Target companies" in mc)
        record("Settings: renders storage info", "Storage" in mc or "Database" in mc)

        # --- 9. Interactive: record status manually (Track) ---------------- #
        print("\n=== INTERACTIVE: RECORD STATUS ===")
        nav(page, "Track")
        page.wait_for_timeout(1000)
        # Open the expander.
        page.click('text=Record a status manually')
        page.wait_for_timeout(1000)
        # Select a job and a new status, then record.
        try:
            page.select_option('section[data-testid="stMain"] select', value="accepted")
            page.wait_for_timeout(500)
            # Find the Record status button and click.
            rec_btn = page.locator('button:has-text("Record status")')
            if rec_btn.count() > 0:
                rec_btn.first.click()
                page.wait_for_timeout(2000)
                record("Track: Record status button works", script_state(page) == "notRunning")
            else:
                record("Track: Record status button exists", False, "button not found")
        except Exception as e:
            record("Track: Record status flow", False, str(e))

        # --- 10. Interactive: update password (Settings) ------------------- #
        print("\n=== INTERACTIVE: UPDATE PASSWORD ===")
        nav(page, "Settings")
        page.wait_for_timeout(1000)
        try:
            pw_input = page.locator('input[type="password"]')
            if pw_input.count() > 0:
                pw_input.fill("hunting2026")  # same value -> should show no-change
                page.click('button:has-text("Update password")')
                page.wait_for_timeout(1500)
                record("Settings: Update password button works", script_state(page) == "notRunning")
            else:
                record("Settings: password input exists", False, "no password input")
        except Exception as e:
            record("Settings: Update password flow", False, str(e))

        # --- 11. Interactive: generate CVs (CV Manager) -------------------- #
        print("\n=== INTERACTIVE: GENERATE CVs ===")
        nav(page, "CV Manager")
        page.wait_for_timeout(1000)
        try:
            gen_btn = page.locator('button:has-text("Generate CVs for top matches")')
            if gen_btn.count() > 0:
                gen_btn.first.click()
                page.wait_for_timeout(3000)
                record("CV Manager: Generate CVs button works", script_state(page) == "notRunning")
            else:
                record("CV Manager: Generate CVs button exists", False, "button not found")
        except Exception as e:
            record("CV Manager: Generate CVs flow", False, str(e))

        # --- 12. Interactive: start engine (Hunt) -------------------------- #
        print("\n=== INTERACTIVE: START ENGINE ===")
        nav(page, "Hunt")
        page.wait_for_timeout(1000)
        try:
            start_btn = page.locator('button:has-text("Start the job search engine")')
            if start_btn.count() > 0:
                start_btn.first.click()
                page.wait_for_timeout(5000)
                record("Hunt: Start engine button works", script_state(page) == "notRunning")
            else:
                record("Hunt: Start engine button exists", False, "button not found")
        except Exception as e:
            record("Hunt: Start engine flow", False, str(e))

        browser.close()

    # --- Summary ----------------------------------------------------------- #
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"TOTAL: {len(_results)} tests — {passed} passed, {failed} failed")
    print("=" * 60)
    if failed:
        print("\nFAILURES:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  ✗ {name} — {detail}")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run_tests() else 0)
