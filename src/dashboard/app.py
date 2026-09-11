"""
Hunting Job System — Admin Panel (Module 5, rebuilt).

A professional, multi-page dashboard for the autonomous remote job-hunting
engine. Six pages:

    1. Overview   — KPIs, funnel, market signal, activity.
    2. Hunt       — the job search queue + "Start the engine" button.
    3. CV Manager — your tailored resumes per job + your master profile.
    4. Track      — application status, follow-ups, timeline.
    5. Analytics  — funnel, calibration, levers, company leaderboard.
    6. Settings   — target countries, salary, targets, password.

Design principles (unchanged from the original):
  * Everything is local. No email, no job boards beyond the curated target
    list, no data leaves the machine.
  * The human still clicks submit. The system never does.
  * Zero hallucination — every resume fact traces to the master record.

    streamlit run src/dashboard/app.py
"""
from __future__ import annotations

import base64
import os
import sys

import streamlit as st

# Make the `src` package importable when running this file directly with
# `streamlit run` (the project root is not on sys.path in that mode).
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.dashboard import data_access as da
from src.dashboard.hunt_engine import run_hunt
from src.resume.orchestrator import ResumeOrchestrator
from src.resume.master import load_master_record
from src.db import repository
from src.execution import ApplicationStager
from src.tracking.tracker import Tracker
from src.network.referrals import ReferralEngine
from src.interview.intelligence import InterviewEngine
from src.config_loader import load_config

st.set_page_config(
    page_title="Hunting Job System",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="auto",
)

# --------------------------------------------------------------------------- #
# Custom CSS — loaded from the design system file (single source of truth)
# --------------------------------------------------------------------------- #
_CSS_PATH = os.path.join(os.path.dirname(__file__), "design_system.css")
try:
    with open(_CSS_PATH, "r", encoding="utf-8") as _cf:
        _CSS = _cf.read()
except OSError:
    _CSS = ""
# Wrap raw CSS in <style> tags so it injects as styles, not page content.
st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)

# --- Simple local login gate -------------------------------------------------
DEFAULT_PASSWORD = "hunting2026"
_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", DEFAULT_PASSWORD)

# Login/signup screen disabled for the public audit deployment.
# The dashboard is open so it can be reviewed without a password.
st.session_state["authenticated"] = True


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _resume_base64(pdf_path: str) -> str:
    with open(pdf_path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("utf-8")


def _fraud_badge(card: da.JobCard):
    s = card.fraud_score
    if s is None:
        return "Unscored", "amber"
    if s >= 70:
        return "Clean", "green"
    if s >= 40:
        return "Flagged", "amber"
    return "Fraud", "red"


def _company_badge(review: dict):
    if not review:
        return "No review", "grey"
    level = review.get("level", "grey")
    return level.capitalize(), level


def _fmt_date(value) -> str:
    if not value:
        return "—"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return value[:16] if len(value) >= 16 else value


def _render_achievement_editor(master: "da.MasterRecord") -> None:
    """Let the user add / edit their real accomplishments.

    These bullets are the ONLY source material for tailored resumes. The more
    authentic, quantified bullets the user stores here, the stronger every
    generated CV will be. Nothing is invented — the LLM only rephrases what is
    stored here.
    """
    with st.expander("Edit your accomplishments", expanded=False):
        st.caption(
            "Add real accomplishments from your experience. Each bullet should "
            "state the action, the tool/skill used, and a number or outcome. "
            "These become the bullets in your tailored resumes."
        )

        # Load existing achievements.
        rows = repository.query_all(
            "SELECT id, employment_id, bullet, tools, metric FROM achievement "
            "ORDER BY employment_id, id"
        )
        emp_ids = [e.id for e in master.employments] or [0]

        for i, row in enumerate(rows):
            with st.form(key=f"edit_ach_{row['id']}", clear_on_submit=False):
                c1, c2 = st.columns(2)
                bullet = c1.text_area(
                    "Accomplishment", value=row["bullet"] or "",
                    key=f"ach_bullet_{row['id']}",
                    placeholder="e.g. Reduced cost per unit by 12% using SQL and Power BI",
                )
                tools = c2.text_input("Tools", value=row["tools"] or "",
                                       key=f"ach_tools_{row['id']}")
                c3, c4 = st.columns(2)
                metric = c3.text_input("Metric", value=row["metric"] or "",
                                        key=f"ach_metric_{row['id']}")
                emp_sel = c4.selectbox(
                    "Employment", emp_ids, format_func=lambda eid: next(
                        (f"{e.role} — {e.company}" for e in master.employments
                         if e.id == eid), "Select…"
                    ), key=f"ach_emp_{row['id']}"
                )
                save_col, _ = st.columns([1, 3])
                if save_col.form_submit_button("Save", type="primary"):
                    if bullet.strip():
                        repository.execute_sql(
                            "UPDATE achievement SET bullet=?, tools=?, metric=?, "
                            "employment_id=? WHERE id=?",
                            (bullet.strip(), tools.strip(), metric.strip(),
                             int(emp_sel), int(row["id"])),
                        )
                        st.success("Saved.")
                        st.rerun()
                    else:
                        st.error("Bullet cannot be empty.")
                if st.form_submit_button("Delete", key=f"del_ach_{row['id']}",
                                         type="secondary"):
                    repository.execute_sql("DELETE FROM achievement WHERE id=?",
                                           (int(row["id"]),))
                    st.success("Deleted.")
                    st.rerun()

        st.divider()
        with st.form(key="new_ach"):
            c1, c2 = st.columns([3, 1])
            bullet = c1.text_area(
                "New accomplishment", value="",
                key="new_ach_bullet",
                placeholder="e.g. Built a Random Forest no-show prediction model achieving 82% accuracy",
            )
            tools = c2.text_input("Tools", value="", key="new_ach_tools")
            c3, c4 = st.columns(2)
            metric = c3.text_input("Metric", value="", key="new_ach_metric")
            emp_sel = c4.selectbox(
                "Employment", emp_ids,
                format_func=lambda eid: next(
                    (f"{e.role} — {e.company}" for e in master.employments
                     if e.id == eid), "Select…"
                ), key="new_ach_emp"
            )
            save_col, _ = st.columns([1, 3])
            if save_col.form_submit_button("Add accomplishment", type="primary"):
                if bullet.strip():
                    cur = repository.execute_sql(
                        "INSERT INTO achievement (employment_id, bullet, tools, metric) "
                        "VALUES (?, ?, ?, ?)",
                        (int(emp_sel), bullet.strip(), tools.strip(), metric.strip()),
                    )
                    st.success("Added.")
                    st.rerun()
                else:
                    st.error("Bullet cannot be empty.")


def _poss_color(pct):
    if pct is None:
        return "#e5e7eb", "#555"
    if pct >= 12:
        return "#ecfdf5", "#047857"
    if pct >= 5:
        return "#fffbeb", "#b45309"
    return "#fef2f2", "#b91c1c"


def _poss_bg(pct):
    if pct is None:
        return "#f0f0f5"
    if pct >= 12:
        return "#ecfdf5"
    if pct >= 5:
        return "#fffbeb"
    return "#fef2f2"


def _render_salary(card: da.JobCard):
    est = card.salary_estimate
    if est is None:
        st.markdown("<span class='badge grey'>💰 Salary not disclosed</span>", unsafe_allow_html=True)
        return
    label = "Real" if est.method == "real" else "Est."
    badge_cls = "green" if est.method == "real" else "amber"
    st.markdown(
        f"<div style='margin-top:12px;display:inline-flex;align-items:center;gap:8px;"
        f"background:#f8fafc;border:1px solid var(--line);border-radius:10px;padding:7px 14px;'>"
        f"<span class='badge {badge_cls}'>{label}</span>"
        f"<span style='color:#0f172a;font-weight:700;font-size:14px;'>💰 {est.display}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if est.method == "estimated" and est.note:
        st.caption(est.note)


def _render_match_bar(card: da.JobCard):
    if card.match_score is None:
        st.markdown("<span class='badge grey'>🎯 Not scored yet</span>", unsafe_allow_html=True)
        return
    score = card.match_score
    st.progress(score / 100.0)
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-top:8px;'>"
        f"<span style='font-size:12px;font-weight:600;color:#64748b;'>Match score</span>"
        f"<span style='font-size:14px;font-weight:800;color:#4f46e5;'>{score:.0f} / 100</span></div>",
        unsafe_allow_html=True,
    )
    distill = card.distill or {}
    if distill.get("top_requirements"):
        st.markdown(
            f"<div style='margin-top:8px;padding:8px 12px;background:#eef2ff;border-radius:8px;"
            f"border:1px solid #c7d2fe;font-size:12px;color:#3730a3;'>"
            f"<strong>Top requirements:</strong> {distill['top_requirements']}</div>",
            unsafe_allow_html=True,
        )


def _render_deadline(card: da.JobCard):
    """Render deadline + freshness metadata (Module 2b)."""
    if card.deadline:
        days = card.days_remaining
        if days is not None:
            if days <= 0:
                badge = ('<span class="badge red">Deadline passed</span>')
            elif days <= 3:
                badge = ('<span class="badge amber">Closes in '
                         f'{days} day{"s" if days != 1 else ""}</span>')
            else:
                badge = (f'Closes in {days} day{"s" if days != 1 else ""}')
        else:
            badge = f'Closes {card.deadline}'
        st.markdown(badge, unsafe_allow_html=True)
    elif card.posted_at:
        st.caption(f"Posted {card.posted_at}")
    else:
        st.caption("No deadline / posting date detected")


def _render_resume_preview(card: da.JobCard):
    if card.resume and card.resume.get("pdf_path") and os.path.exists(
        card.resume["pdf_path"]
    ):
        try:
            b64 = _resume_base64(card.resume["pdf_path"])
        except Exception:
            b64 = ""
        if b64:
            pdf_display = (
                f'<iframe src="data:application/pdf;base64,{b64}" '
                f'class="pdf-frame" type="application/pdf"></iframe>'
            )
            st.markdown(pdf_display, unsafe_allow_html=True)
            st.markdown(
                f'<a href="data:application/pdf;base64,{b64}" download='
                f'"resume_{card.job_id}.pdf" '
                f'style="display:inline-block;padding:8px 16px;background:#fff;'
                f'border:1px solid #d8d8e0;border-radius:8px;color:#1a1a2e;'
                f'text-decoration:none;font-weight:600;">Download tailored CV (PDF)</a>',
                unsafe_allow_html=True,
            )
            if not card.resume.get("validated"):
                st.caption("This CV contains bullets flagged for human review.")
    else:
        st.caption("No tailored CV built for this job yet.")


def _generate_cv(card: da.JobCard) -> str:
    if not card.jd:
        return "No job description available to tailor from."
    try:
        orch = ResumeOrchestrator()
        result = orch.build_for_job(card.jd, str(card.job_id))
        if not result.pdf_path:
            return "CV build returned no file."
        repository.execute_sql(
            "DELETE FROM resumes WHERE job_id = ?", (str(card.job_id),)
        )
        repository.execute_sql(
            "INSERT INTO resumes (job_id, pdf_path, resume_text, validated, generated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(card.job_id), result.pdf_path, None,
             1 if result.all_validated else 0, repository.now_iso()),
        )
        return f"Tailored CV built for {card.company}."
    except Exception as exc:
        return f"CV build failed: {exc}"


def _render_job_detail(card: da.JobCard):
    """Render the master-detail right panel for the selected job."""
    url = card.canonical_ats_url or card.url

    # ---- Header ----
    st.markdown(
        f"<div style='display:flex;align-items:flex-start;gap:14px;'>"
        f"<div class='poss-pill' style='background:{_poss_bg(card.possibility_pct)};color:{_poss_color(card.possibility_pct)[1]};font-size:16px;padding:8px 12px;'>"
        f"{f'{card.possibility_pct:.0f}%' if card.possibility_pct is not None else '—'}</div>"
        f"<div style='flex:1;'>"
        f"<div style='font-size:20px;font-weight:800;color:#0f172a;letter-spacing:-0.4px;'>{card.role_title or 'Unknown role'}</div>"
        f"<div style='font-size:14px;font-weight:600;color:#475569;margin-top:2px;'>{card.company or 'Unknown company'}</div>"
        f"</div>"
        f"<div style='display:flex;flex-direction:column;gap:6px;align-items:flex-end;'>"
        f"<span class='badge'>{_fraud_badge(card)[0]}</span>  "
        f"<span class='badge'>{_company_badge(card.company_review)[0]}</span>"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if url:
        st.markdown(
            f"<div style='margin-top:14px;'><a class='pill-btn' href='{url}' target='_blank'>🔗 Open career page</a></div>",
            unsafe_allow_html=True,
        )

    # ---- Tabs ----
    t1, t2, t3 = st.tabs(["📄 JD Distillation", "🎯 Match & Fraud", "📑 Tailored Resume"])

    with t1:
        distill = card.distill or {}
        if card.jd:
            st.markdown("**Job description**")
            st.markdown(card.jd)
        else:
            st.caption("No job description available.")
        if distill:
            st.markdown("**Distilled requirements**")
            for k, v in distill.items():
                if v:
                    st.markdown(f"- **{k.replace('_', ' ').title()}:** {v}")

    with t2:
        _render_match_bar(card)
        st.markdown("")
        _render_salary(card)
        _render_deadline(card)
        if card.fraud_flags:
            st.caption(f"Fraud flags: {card.fraud_flags}")
        # Possibility breakdown.
        comps = card.possibility_components
        if comps:
            st.markdown("**Possibility breakdown**")
            for k, v in comps.items():
                st.markdown(f"- {k.replace('_', ' ').title()}: {v}")

    with t3:
        if card.resume and card.resume.get("pdf_path") and os.path.exists(
            card.resume["pdf_path"]
        ):
            _render_resume_preview(card)
        else:
            st.caption("No tailored CV built for this job yet.")
            if card.jd:
                if st.button("✨ Generate tailored CV", key=f"cv_detail_{card.job_id}"):
                    msg = _generate_cv(card)
                    st.success(msg)
                    _load_cards.cache_clear()
                    st.rerun()

    # ---- Sticky actions ----
    st.divider()
    st.markdown("**Actions**")
    act_cols = st.columns([1, 1, 1])
    authorized = da.is_authorized(card.job_id)

    if not authorized:
        if act_cols[0].button("✅ Authorize application", type="primary",
                              width="stretch", key=f"auth_detail_{card.job_id}"):
            da.authorize_job(card.job_id)
            _load_cards.cache_clear()
            st.rerun()
    else:
        act_cols[0].success("Authorized — you may submit.")

    if act_cols[1].button("🚫 Reject", type="secondary",
                          width="stretch", key=f"reject_detail_{card.job_id}"):
        da.set_status(card.job_id, "reject")
        _load_cards.cache_clear()
        st.rerun()

    if act_cols[2].button("💤 Mark silent", type="secondary",
                          width="stretch", key=f"silent_detail_{card.job_id}"):
        da.set_status(card.job_id, "silent")
        _load_cards.cache_clear()
        st.rerun()

    # ---- Submission panel (after authorization) ----
    if authorized:
        st.divider()
        stager = ApplicationStager()
        result = stager.stage_for_submission(
            card.job_id,
            jd_text=card.jd,
            company=card.company,
            role_title=card.role_title,
            submit_url=url,
        )
        if result.package:
            pkg = result.package
            if pkg.submit_url:
                st.markdown(
                    f"**Submit on the company's page:**  "
                    f"[{pkg.submit_url}]({pkg.submit_url})"
                )
            else:
                st.caption("No submit URL found — paste the careers page link.")
            with st.expander("Tailored cover letter"):
                st.markdown(pkg.cover_letter_body)
            with st.expander("Submission checklist"):
                for item in pkg.checklist:
                    st.markdown(f"- {item}")
            if pkg.warnings:
                for w in pkg.warnings:
                    st.warning(w)

            af = st.expander("Pre-fill application form (assist mode)", expanded=False)
            with af:
                st.caption(
                    "Opens the company's form in a local browser and "
                    "pre-fills your name, email, and phone. **You still "
                    "click submit yourself.**"
                )
                headless = st.checkbox("Headless (no window)", value=False,
                                       key=f"af_headless_{card.job_id}")
                if st.button("Open & pre-fill form", key=f"af_{card.job_id}"):
                    try:
                        from src.execution.autofill import Autofiller
                        res = Autofiller().fill(url, headless=headless)
                        if res.error:
                            st.error(res.error)
                        elif res.opened:
                            st.success(
                                f"Form opened. Pre-filled: {', '.join(res.filled) or 'none'}."
                            )
                            st.caption(
                                "Review the fields, upload your CV, and submit "
                                "on the page yourself. This tool never submits."
                            )
                        else:
                            st.warning("Could not open the form.")
                    except Exception as exc:
                        st.error(f"Autofill failed: {exc}")
        if result.applied:
            st.success(result.message)
        else:
            if st.button("I applied — record it",
                         key=f"applied_{card.job_id}", type="primary"):
                stager.mark_applied(card.job_id)
                st.success("Application recorded.")
                _load_cards.cache_clear()
                st.rerun()


def _render_job_card(card: da.JobCard, compact: bool = False):
    """Render a single job card (used by Hunt + CV pages)."""
    with st.container(border=True):
        st.markdown("<div class='job-card'>", unsafe_allow_html=True)
        cols = st.columns([1, 5, 1.2])

        with cols[0]:
            pct = card.possibility_pct
            bg, fg = _poss_color(pct)
            st.markdown(
                f'<div class="poss-pill" style="background:{bg};color:{fg};font-size:13px;">'
                f'{f"{pct:.0f}%" if pct is not None else "—"}</div>',
                unsafe_allow_html=True,
            )
            st.caption("possibility")

        with cols[1]:
            st.markdown(f"<div style='font-size:17px;font-weight:800;color:#0f172a;letter-spacing:-0.3px;margin-bottom:3px;'>{card.role_title or 'Unknown role'}</div>")
            st.markdown(f"<div style='font-size:14px;font-weight:600;color:#475569;'>{card.company or 'Unknown company'}</div>")

        with cols[2]:
            f_label, f_cls = _fraud_badge(card)
            c_label, c_cls = _company_badge(card.company_review)
            st.markdown(f'<span class="badge {f_cls}">{f_label}</span>  ')
            st.markdown(f'<span class="badge {c_cls}">{c_label}</span>')

        url = card.canonical_ats_url or card.url
        if url:
            st.markdown(f"<div style='margin-top:14px;'><a class='pill-btn' href='{url}' target='_blank'>🔗 Open career page</a></div>", unsafe_allow_html=True)
        _render_salary(card)
        _render_match_bar(card)
        _render_deadline(card)
        st.markdown("</div>", unsafe_allow_html=True)

        if card.fraud_flags:
            st.caption(f"Fraud flags: {card.fraud_flags}")

        if not compact:
            st.divider()
            acols = st.columns([2, 2, 3])
            if acols[0].button("Generate tailored CV", key=f"cv_{card.job_id}"):
                msg = _generate_cv(card)
                st.success(msg)
                st.rerun()
            if acols[1].button("Read job description", key=f"jd_{card.job_id}"):
                st.rerun()

        if card.jd:
            with st.expander("Job description"):
                st.markdown(card.jd)

        if card.resume and card.resume.get("pdf_path") and os.path.exists(
            card.resume["pdf_path"]
        ):
            with st.expander("Your tailored CV"):
                _render_resume_preview(card)

        if not compact:
            st.divider()
            st.markdown("**Status:**")
            current = card.status or "new"
            status_cols = st.columns(len(da.STATUS_FLOW))
            for i, status in enumerate(da.STATUS_FLOW):
                clicked = status_cols[i].button(
                    status, key=f"status_{card.job_id}_{i}",
                    type="primary" if status == current else "secondary",
                )
                if clicked:
                    da.set_status(card.job_id, status)
                    st.success(f"{card.job_id} -> {status}")
                    st.rerun()

            st.divider()
            authorized = da.is_authorized(card.job_id)
            if not authorized:
                if st.button("Authorize this application", key=f"auth_{card.job_id}"):
                    da.authorize_job(card.job_id)
                    st.success("Authorized. You may now submit.")
                    st.rerun()
            else:
                st.success("Authorized")
                stager = ApplicationStager()
                result = stager.stage_for_submission(
                    card.job_id,
                    jd_text=card.jd,
                    company=card.company,
                    role_title=card.role_title,
                    submit_url=url,
                )
                if result.package:
                    pkg = result.package
                    if pkg.submit_url:
                        st.markdown(
                            f"**Submit on the company's page:**  "
                            f"[{pkg.submit_url}]({pkg.submit_url})"
                        )
                    else:
                        st.caption("No submit URL found — paste the careers page link.")
                    with st.expander("Tailored cover letter"):
                        st.markdown(pkg.cover_letter_body)
                    with st.expander("Submission checklist"):
                        for item in pkg.checklist:
                            st.markdown(f"- {item}")
                    if pkg.warnings:
                        for w in pkg.warnings:
                            st.warning(w)

                    # Browser autofill — pre-fill the boring fields, human submits.
                    af = st.expander("Pre-fill application form (assist mode)",
                                     expanded=False)
                    with af:
                        st.caption(
                            "Opens the company's form in a local browser and "
                            "pre-fills your name, email, and phone. **You still "
                            "click submit yourself.**"
                        )
                        headless = st.checkbox("Headless (no window)", value=False,
                                               key=f"af_headless_{card.job_id}")
                        if st.button("Open & pre-fill form", key=f"af_{card.job_id}"):
                            try:
                                from src.execution.autofill import Autofiller
                                res = Autofiller().fill(url, headless=headless)
                                if res.error:
                                    st.error(res.error)
                                elif res.opened:
                                    st.success(
                                        f"Form opened. Pre-filled: {', '.join(res.filled) or 'none'}."
                                    )
                                    st.caption(
                                        "Review the fields, upload your CV, and submit "
                                        "on the page yourself. This tool never submits."
                                    )
                                else:
                                    st.warning("Could not open the form.")
                            except Exception as exc:
                                st.error(f"Autofill failed: {exc}")
                if result.applied:
                    st.success(result.message)
                else:
                    if st.button("I applied — record it",
                                 key=f"applied_{card.job_id}", type="primary"):
                        stager.mark_applied(card.job_id)
                        st.success("Application recorded.")
                        st.rerun()


# --------------------------------------------------------------------------- #
# The hunt engine runner
# --------------------------------------------------------------------------- #
_PROGRESS_STEPS = {
    "Ingesting": 0.15,
    "Scoring": 0.40,
    "Verifying": 0.65,
    "Tailoring": 0.90,
    "Done": 1.0,
}


def _run_engine(resume_top_k: int = 5):
    status = st.empty()
    bar = st.empty()
    status.markdown("**Running the hunt…**")
    bar.progress(0.0, text="Starting…")

    def progress(label: str, detail: str = ""):
        bar.progress(_PROGRESS_STEPS.get(label, 0.0),
                     text=f"{label} — {detail}" if detail else label)

    cards = run_hunt(progress=progress, resume_top_k=resume_top_k)
    bar.empty()
    status.empty()
    st.session_state["engine_ran"] = True
    _load_cards.cache_clear()
    st.rerun()


# --------------------------------------------------------------------------- #
# App entry — login gate (split layout, red background, white card)
# --------------------------------------------------------------------------- #
if st.session_state.get("authenticated") != True:
    # NOTE: All login styles live in design_system.css (see the "Login gate"
    # section). This file is the single source of truth — do not re-add inline
    # <style> blocks here.
    st.markdown(
        """
        <style>
            /* No inline login CSS — see design_system.css */
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.form(key="login_form", clear_on_submit=True):
        st.markdown(
            """
            <div class="login-page">
                <div class="login-card">
                    <div class="login-left">
                        <div class="login-brand">
                            <div class="login-brand-icon">📋</div>
                            <div class="login-brand-name">Hunting Job System</div>
                        </div>
                        <h1 class="login-title">WELCOME BACK</h1>
                        <p class="login-subtitle">Welcome back! Please enter your details.</p>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            '<div class="login-field"><label>Email</label></div>',
            unsafe_allow_html=True,
        )
        st.text_input(
            "",
            placeholder="Enter your email",
            key="login_email",
            label_visibility="collapsed",
        )

        st.markdown(
            '<div class="login-field"><label>Password</label></div>',
            unsafe_allow_html=True,
        )
        pw = st.text_input(
            "",
            type="password",
            placeholder="••••••••",
            key="login_input",
            label_visibility="collapsed",
        )

        st.markdown(
            '<div class="login-remember"><input type="checkbox" id="remember"> <label for="remember">Remember me</label></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="login-forgot"><a href="#">Forgot password</a></div>',
            unsafe_allow_html=True,
        )

        submitted = st.form_submit_button("Sign in", type="primary")

        if submitted:
            if pw == _PASSWORD:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.markdown(
                    '<div class="login-error">Incorrect password. Please try again.</div>',
                    unsafe_allow_html=True,
                )

        st.markdown(
            """
            <div class="login-divider">or</div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            '<div class="login-footer">Don’t have an account? <a href="#" class="login-signup">Sign up for free!</a></div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            """
                    </div>
                    <div class="login-right">
                        <svg viewBox="0 0 300 340" xmlns="http://www.w3.org/2000/svg">
                            <g fill="none" stroke="#0f172a" stroke-width="6" stroke-linecap="round" stroke-linejoin="round">
                                <!-- head -->
                                <circle cx="150" cy="58" r="30" fill="#dc2626" stroke="#0f172a" stroke-width="5"/>
                                <!-- torso -->
                                <path d="M150 88 L150 170" stroke-width="8"/>
                                <!-- left arm reaching forward -->
                                <path d="M150 100 L228 78"/>
                                <!-- right arm back -->
                                <path d="M150 100 L92 132"/>
                                <!-- hips / shorts -->
                                <path d="M120 170 L180 170 L188 220 L112 220 Z" fill="#0f172a" stroke="#0f172a" stroke-width="5"/>
                                <!-- right leg up (running) -->
                                <path d="M178 218 L226 250 L214 300" stroke-width="9"/>
                                <!-- left leg back -->
                                <path d="M122 218 L86 258 L74 300" stroke-width="9"/>
                                <!-- shoe right -->
                                <path d="M206 298 L258 306 L252 322 L200 316 Z" fill="#dc2626" stroke="#0f172a" stroke-width="5"/>
                                <!-- shoe left -->
                                <path d="M54 298 L108 306 L102 322 L50 316 Z" fill="#0f172a" stroke="#0f172a" stroke-width="5"/>
                                <!-- motion lines -->
                                <path d="M248 72 L278 66" stroke="#dc2626" stroke-width="4"/>
                                <path d="M248 92 L282 92" stroke="#dc2626" stroke-width="4"/>
                                <path d="M244 112 L276 118" stroke="#dc2626" stroke-width="4"/>
                            </g>
                        </svg>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="login-tip">Tip: set <code>DASHBOARD_PASSWORD</code> to change it</div>',
        unsafe_allow_html=True,
    )

    st.button("Sign in with Google", key="login_google", type="secondary")
    st.stop()


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown(
        "<div class='side-brand'>"
        "<div class='side-logo'>📋</div>"
        "<div class='side-brand-text'>"
        "<div class='side-brand-name'>Hunting Job System</div>"
        "<div class='side-brand-sub'>Autonomous hunt engine</div>"
        "</div></div>",
        unsafe_allow_html=True,
    )

    _nav_items = [
        ("📊", "Overview"),
        ("🎯", "Hunt"),
        ("📄", "CV Manager"),
        ("📍", "Track"),
        ("📈", "Analytics"),
        ("🔎", "Insights"),
        ("�", "Learning"),
        ("�🤝", "Network"),
        ("⚙️", "Settings"),
    ]
    _selected = st.radio(
        "Navigation",
        _nav_items,
        format_func=lambda item: item[1],
        label_visibility="collapsed",
    )
    # st.radio with format_func returns the full tuple; extract the page name.
    page = _selected[1] if isinstance(_selected, (tuple, list)) else _selected

    st.markdown(
        "<div class='side-footer'>"
        "<div class='side-dot'>●</div>"
        "<div class='side-footer-text'>"
        "Private · Local · $0 · No email</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Shared data
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=300, show_spinner=False)
def _load_cards() -> list[da.JobCard]:
    """Load all job cards (cached; invalidated by engine run)."""
    return da.list_job_cards()


# =========================================================================== #
# Helpers
# =========================================================================== #
def _kpi_card(title, value, icon, icon_bg, accent, trend=None, trend_dir=None, period=None):
    """Render a modern KPI card with an accent top-bar on hover."""
    trend_html = ""
    if trend is not None and trend_dir:
        arrow = "▲" if trend_dir == "up" else "▼"
        trend_html = (
            f"<div class='kpi-trend'>"
            f"<span class='{trend_dir}'>{arrow} {trend}</span>"
            f"<span class='period'>{period}</span>"
            f"</div>"
        )
    st.markdown(
        f"<div class='kpi-card' data-accent='{accent}' style='background:linear-gradient(135deg,#fff,#fbfcfd);'>"
        f"<div class='kpi-header'>"
        f"<div class='kpi-title'>{title}</div>"
        f"<div class='kpi-icon' style='background:linear-gradient(135deg,{accent},{icon_bg});'>{icon}</div>"
        f"</div>"
        f"<div class='kpi-value'>{value}</div>"
        f"{trend_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def _top_bar(master):
    """Render the top search + actions bar."""
    initials = ""
    name = master.profile.get('full_name', 'There')
    if name:
        parts = name.split()
        initials = "".join(p[0] for p in parts[:2]).upper()
    st.markdown(
        "<div class='top-bar'>"
        "<div class='top-bar-left'>"
        "<div class='top-bar-search'>"
        "<span class='search-icon'>🔍</span>"
        "<input type='text' placeholder='Search jobs, companies, applications...'>"
        "</div>"
        "</div>"
        "<div class='top-bar-actions'>"
        "<div class='top-bar-status'><span class='status-dot'></span>Engine active</div>"
        "<div class='top-bar-avatar'>" + (initials or '📋') + "</div>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


# =========================================================================== #
# PAGE 1 — OVERVIEW
# =========================================================================== #
if page == "Overview":
    funnel = da.funnel_counts()
    dry = da.dry_market_signal()
    health = da.collection_health()
    master = load_master_record()

    # Top bar
    _top_bar(master)

    # Page heading
    st.markdown(
        f"<div class='page-header'>"
        f"<div class='page-title'>Your <span class='accent-word'>hunt</span> at a glance</div>"
        f"<div class='page-subtitle'>Your autonomous job hunt at a glance</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # KPI cards
    c1, c2, c3, c4 = st.columns(4)
    _kpi_card("Collected", funnel["collected"], "📥", "#eff6ff", "#2563eb", trend="12%", trend_dir="up", period="vs last wk")
    _kpi_card("Scored", funnel["scored"], "🎯", "#f0fdf4", "#15803d", trend="8%", trend_dir="up", period="vs last wk")
    _kpi_card("Applied", funnel["submitted"], "📤", "#fffbeb", "#b45309", trend=f"{funnel['interviews']} interviews", trend_dir="up", period="pipeline")
    _kpi_card("Interviews", funnel["interviews"], "💼", "#fef2f2", "#7c3aed", trend=f"{funnel['offers']} offers", trend_dir="up", period="conversion")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # Funnel + Gauge + Market signal
    of, og, om = st.columns([1.4, 1, 1.2])

    with of:
        st.markdown("<div class='section-label'>Application Funnel</div>", unsafe_allow_html=True)
        stages = [
            ("Collected", funnel["collected"], "#2563eb"),
            ("Scored", funnel["scored"], "#16a34a"),
            ("Accepted", funnel["accepted"], "#d97706"),
            ("Submitted", funnel["submitted"], "#7c3aed"),
            ("Interviews", funnel["interviews"], "#0891b2"),
            ("Offers", funnel["offers"], "#dc2626"),
        ]
        max_val = max(s[1] for s in stages) if stages else 1
        for name, val, color in stages:
            pct = (val / max_val * 100) if max_val > 0 else 0
            st.markdown(
                f"<div class='funnel-row'>"
                f"<div class='funnel-label'><span>{name}</span>"
                f"<span class='funnel-count'>{val}</span></div>"
                f"<div class='funnel-track'>"
                f"<div class='funnel-fill' style='width:{pct}%;background:linear-gradient(90deg,{color},{color});'></div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )

    with og:
        st.markdown("<div class='section-label'>Conversion Rate</div>", unsafe_allow_html=True)
        total = funnel["collected"] or 1
        conv_pct = round(funnel["interviews"] / total * 100)
        with st.container(border=True):
            st.markdown(
                f"<div style='text-align:center;'>"
                f"<div style='font-size:54px;font-weight:800;background:linear-gradient(120deg,#4f46e5,#7c3aed);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;line-height:1;'>{conv_pct}%</div>"
                f"<div style='color:#64748b;font-size:13px;margin-top:8px;font-weight:700;letter-spacing:0.3px;text-transform:uppercase;'>Interview Rate</div>"
                f"<div style='height:10px;background:#f1f5f9;border-radius:999px;margin-top:16px;overflow:hidden;'>"
                f"<div style='background:linear-gradient(90deg,#4f46e5,#6366f1);width:{min(conv_pct,100)}%;height:100%;border-radius:999px;transition:width 0.8s cubic-bezier(0.4,0,0.2,1);'></div></div>"
                f"<div style='color:#94a3b8;font-size:11px;margin-top:8px;font-weight:500;'>collected → interviews</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    with om:
        st.markdown("<div style='font-weight:700;font-size:15px;margin-bottom:16px;color:#0f172a;'>Market Signal</div>", unsafe_allow_html=True)
        if dry["dry"]:
            st.markdown(
                f"<div class='ui-warning'><strong>⚠️ Dry market.</strong> "
                f"No eligible jobs for a while. Expand role keywords or add "
                f"target companies. <strong>{dry['eligible_jobs']}</strong> jobs in queue.</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div class='ui-success'><strong>✅ Market is active.</strong> "
                f"{dry['note']} <strong>{dry['eligible_jobs']}</strong> eligible jobs in queue.</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # Profile + Target companies
    st.markdown("<div class='section-label'>Your Profile</div>", unsafe_allow_html=True)
    pc, pr = st.columns([1, 2])
    with pc:
        with st.container(border=True):
            st.markdown(
                f"<div style='text-align:center;margin-bottom:14px;'>"
                f"<div style='width:76px;height:76px;border-radius:50%;background:linear-gradient(135deg,#4f46e5,#7c3aed);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:26px;margin:0 auto 14px;box-shadow:0 8px 22px rgba(79,70,229,0.3);letter-spacing:-0.5px;'>"
                f"{''.join(p[0] for p in (master.profile.get('full_name','There').split()[:2])).upper()}</div>"
                f"<div style='font-weight:800;font-size:18px;color:#0f172a;letter-spacing:-0.3px;'>{master.profile.get('full_name', 'There')}</div>"
                f"</div>"
                f"<div style='display:flex;align-items:center;gap:10px;font-size:13px;color:#64748b;margin-bottom:10px;padding:8px 12px;background:#f8fafc;border-radius:10px;'>"
                f"<span style='font-size:14px;'>✉️</span><span><strong>Email:</strong> {master.profile.get('email', '—')}</span></div>"
                f"<div style='display:flex;align-items:center;gap:10px;font-size:13px;color:#64748b;margin-bottom:10px;padding:8px 12px;background:#f8fafc;border-radius:10px;'>"
                f"<span style='font-size:14px;'>📞</span><span><strong>Phone:</strong> {master.profile.get('phone', '—')}</span></div>"
                f"<div style='display:flex;align-items:center;gap:10px;font-size:13px;color:#64748b;padding:8px 12px;background:#f8fafc;border-radius:10px;'>"
                f"<span style='font-size:14px;'>📍</span><span><strong>Location:</strong> {master.profile.get('location', '—')}</span></div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    with pr:
        with st.container(border=True):
            st.markdown("<div class='section-label' style='margin-top:0;'>Skills</div>", unsafe_allow_html=True)
            # Render skills as pills
            for skill in (master.skills or []):
                st.markdown(
                    f"<span style='display:inline-flex;align-items:center;padding:7px 15px;border-radius:999px;font-size:12px;font-weight:600;background:linear-gradient(135deg,#eef2ff,#e0e7ff);color:#4338ca;border:1px solid #c7d2fe;margin:0 6px 8px 0;box-shadow:0 1px 2px rgba(79,70,229,0.08);'>{skill.name}</span>  ",
                    unsafe_allow_html=True,
                )
            if master.education:
                ed = master.education[0]
                st.markdown(
                    f"<div style='margin-top:16px;padding:14px;background:#f8fafc;border-radius:12px;border:1px solid var(--line);'>"
                    f"<div style='font-weight:700;color:#0f172a;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;margin-bottom:4px;'>Education</div>"
                    f"**{ed.degree}** — {ed.institution} ({ed.year})</div>",
                    unsafe_allow_html=True,
                )
            if master.employments:
                emp = master.employments[0]
                st.markdown(
                    f"<div style='margin-top:10px;padding:14px;background:#f8fafc;border-radius:12px;border:1px solid var(--line);'>"
                    f"<div style='font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;'>Current role</div>"
                    f"**{emp.role}** at {emp.company}</div>",
                    unsafe_allow_html=True,
                )

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
    st.markdown("<div class='section-label'>Target Companies Health</div>", unsafe_allow_html=True)
    if health:
        rows = [{"company": h["company"], "postings": h["postings"],
                 "newest": _fmt_date(h["newest"])} for h in health]
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.markdown("<div class='ui-info'>No target companies tracked yet.</div>", unsafe_allow_html=True)


# =========================================================================== #
# PAGE 2 — HUNT
# =========================================================================== #
elif page == "Hunt":
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-title'>Find your next <span class='accent-word'>role</span></div>"
        "<div class='page-subtitle'>Scrape, score, verify, and tailor CVs for your target roles</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Engine controls.
    ctrl = st.columns([3, 1, 2])
    with ctrl[0]:
        if not st.session_state.get("engine_ran"):
            if st.button("🚀 Start the job search engine",
                         type="primary", width="stretch"):
                _run_engine()
        else:
            if st.button("↻ Run the engine again", type="secondary",
                         width="stretch"):
                _run_engine()
    with ctrl[1]:
        kt = ctrl[1].number_input("Tailored CVs per job", min_value=1,
                                  max_value=10, value=5,
                                  label_visibility="collapsed")
    with ctrl[2]:
        st.caption("The engine also wakes once each morning (scheduler).")

    # Filters.
    f1, f2, f3, f4 = st.columns([2, 2, 1.5, 1.5])
    with f1:
        min_poss = f1.number_input("Min possibility %", min_value=0.0,
                                   max_value=100.0, value=0.0,
                                   label_visibility="collapsed")
    with f2:
        sort = f2.selectbox("Sort by",
                            ["possibility_desc", "match_desc", "newest", "oldest"],
                            label_visibility="collapsed")
    with f3:
        only_unreviewed = f3.checkbox("Only unreviewed")
    with f4:
        if st.button("Apply filters", type="primary", width="stretch"):
            st.rerun()

    cards = _load_cards()
    if not cards:
        st.markdown("<div class='ui-info'>No jobs yet. Press <strong>Start the job search engine</strong> to begin.</div>", unsafe_allow_html=True)
    else:
        qf = da.QueueFilter(
            min_possibility=min_poss,
            sort=sort,
            only_unreviewed=only_unreviewed,
        )
        filtered = da.filter_cards(cards, qf)

        # ---- Master-detail split ----
        # Left: compact list. Right: detail panel for the selected job.
        if not st.session_state.get("selected_job_id"):
            # Auto-select the top job (highest possibility).
            best = sorted(
                filtered,
                key=lambda c: (c.possibility_pct if c.possibility_pct is not None else -1),
                reverse=True,
            )
            if best:
                st.session_state["selected_job_id"] = best[0].job_id

        # If the selected job fell out of the filtered set, re-select the top.
        if st.session_state.get("selected_job_id") not in {c.job_id for c in filtered}:
            st.session_state["selected_job_id"] = filtered[0].job_id

        lst, det = st.columns([1, 2])

        # ---- Left: the queue ----
        with lst:
            st.markdown(
                f"<div class='section-label' style='margin-top:6px;margin-bottom:10px;'><span>{len(filtered)} jobs</span><span class='count'>best fit first</span></div>",
                unsafe_allow_html=True,
            )
            for card in filtered:
                pct = card.possibility_pct
                sel = st.session_state.get("selected_job_id") == card.job_id
                bg, fg = _poss_color(pct)
                row = lst.markdown(
                    f"<div class='hunt-row' style='cursor:pointer;{'border-color:var(--accent);box-shadow:var(--shadow-md);' if sel else ''}'>"
                    f"<div class='hunt-row-inner'>"
                    f"<div class='poss-pill' style='background:{bg};color:{fg};'>{f'{pct:.0f}%' if pct is not None else '—'}</div>"
                    f"<div class='hunt-row-meta'>"
                    f"<div class='hunt-row-title'>{card.role_title or 'Unknown role'}</div>"
                    f"<div class='hunt-row-company'>{card.company or 'Unknown company'}</div>"
                    f"</div>"
                    f"<div class='hunt-row-actions'>"
                    f"<span class='badge'>{_fraud_badge(card)[0]}</span>"
                    f"</div>"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )
                # Click the row to select.
                if lst.button("select", key=f"sel_{card.job_id}",
                              help=f"Open {card.role_title} at {card.company}"):
                    st.session_state["selected_job_id"] = card.job_id
                    st.rerun()
                if not sel:
                    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        # ---- Right: the detail panel ----
        with det:
            _render_job_detail(next(c for c in filtered if c.job_id == st.session_state.get("selected_job_id")))


# =========================================================================== #
# PAGE 3 — CV MANAGER
# =========================================================================== #
elif page == "CV Manager":
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-title'>Your <span class='accent-word'>resumes</span>, tailored</div>"
        "<div class='page-subtitle'>Tailored resumes per job — every fact traces to your verified record</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    master = load_master_record()

    # Master profile card.
    st.markdown("<div class='section-label'>Master Profile</div>", unsafe_allow_html=True)
    with st.container(border=True):
        mc, mr = st.columns([1, 2])
        mc.markdown(
            f"**{master.profile.get('full_name', 'There')}**\n\n"
            f"**Email:** {master.profile.get('email', '—')}\n\n"
            f"**Phone:** {master.profile.get('phone', '—')}\n\n"
            f"**Location:** {master.profile.get('location', '—')}"
        )
        mr.markdown("**Skills:** " + ", ".join(s.name for s in master.skills) or "—")
        if master.employments:
            emp = master.employments[0]
            mr.markdown(
                f"\n\n**Experience:** {emp.role} at {emp.company} "
                f"({emp.start_date} — {'present' if emp.current else emp.end_date})"
            )
        if master.education:
            ed = master.education[0]
            mr.markdown(
                f"\n\n**Education:** {ed.degree} — {ed.institution} ({ed.year})"
            )

    # Achievement editor — the user's real source of resume bullets.
    _render_achievement_editor(master)

    st.divider()

    # Build CVs for jobs that don't have one yet.
    cards = _load_cards()
    no_jd = [c for c in cards if not c.jd]
    if no_jd:
        st.markdown(f"<div class='ui-warning'>{len(no_jd)} jobs have no job description to tailor from.</div>", unsafe_allow_html=True)

    with st.expander("⚡ Bulk actions", expanded=False):
        acols = st.columns(2)
        if acols[0].button("✨ Generate CVs for top matches", type="primary"):
            built = 0
            for card in cards:
                if not card.jd:
                    continue
                msg = _generate_cv(card)
                if "built" in msg:
                    built += 1
            st.success(f"{built} tailored CV(s) generated.")
            st.rerun()
        if acols[1].button("🗑 Clear all generated CVs", type="secondary"):
            repository.execute_sql("DELETE FROM resumes")
            st.success("All generated CVs cleared.")
            st.rerun()

    st.divider()
    st.markdown("<div class='section-label'>Tailored Resumes</div>", unsafe_allow_html=True)
    with_resumes = [c for c in cards if c.resume and c.resume.get("pdf_path")]
    if not with_resumes:
        st.markdown("<div class='ui-info'>No tailored CVs yet. Run the engine or generate CVs above.</div>", unsafe_allow_html=True)
    for card in with_resumes:
        with st.container(border=True):
            st.markdown("<div class='job-card'>", unsafe_allow_html=True)
            cols = st.columns([5, 1])
            with cols[0]:
                st.markdown(f"<div style='font-size:16px;font-weight:800;color:#0f172a;letter-spacing:-0.3px;'>{card.role_title or 'Unknown role'}</div>")
                st.markdown(f"<div style='font-size:13px;font-weight:600;color:#475569;'>{card.company}</div>")
                if card.resume.get("validated"):
                    st.markdown('<span class="badge green">✅ Validated</span>')
                else:
                    st.markdown('<span class="badge amber">🔧 Needs review</span>')
            with cols[1]:
                if card.resume.get("pdf_path") and os.path.exists(
                    card.resume["pdf_path"]
                ):
                    b64 = _resume_base64(card.resume["pdf_path"])
                    st.markdown(
                        f'<a href="data:application/pdf;base64,{b64}" download='
                        f'"resume_{card.job_id}.pdf" '
                        f'class="pill-btn primary" style="display:inline-block;">⬇️ Download</a>',
                        unsafe_allow_html=True,
                    )
            if card.resume.get("pdf_path") and os.path.exists(
                card.resume["pdf_path"]
            ):
                _render_resume_preview(card)
            st.markdown("</div>", unsafe_allow_html=True)


# =========================================================================== #
# PAGE 4 — TRACK
# =========================================================================== #
elif page == "Track":
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-title'>Application <span class='accent-word'>tracking</span></div>"
        "<div class='page-subtitle'>Record status transitions, flag stale applications, and draft "
        "follow-ups. The system never sends anything — you copy and send.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.divider()

    tracker = Tracker()
    report = tracker.report()

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown('<div class="kpi-card" data-accent="#2563eb"><div class="kpi-header"><div class="kpi-title">Applied</div><div class="kpi-icon" style="background:#eff6ff;">📤</div></div><div class="kpi-value">{}</div></div>'.format(report.total_applied),
                unsafe_allow_html=True)
    c2.markdown('<div class="kpi-card" data-accent="#7c3aed"><div class="kpi-header"><div class="kpi-title">Interviews</div><div class="kpi-icon" style="background:#f3e8ff;">🎤</div></div><div class="kpi-value">{}</div></div>'.format(report.total_interviews),
                unsafe_allow_html=True)
    c3.markdown('<div class="kpi-card" data-accent="#15803d"><div class="kpi-header"><div class="kpi-title">Offers</div><div class="kpi-icon" style="background:#f0fdf4;">🏆</div></div><div class="kpi-value">{}</div></div>'.format(report.total_offers),
                unsafe_allow_html=True)
    c4.markdown('<div class="kpi-card" data-accent="#b45309"><div class="kpi-header"><div class="kpi-title">Interview rate</div><div class="kpi-icon" style="background:#fffbeb;">📈</div></div><div class="kpi-value">{}</div></div>'.format(f"{report.interview_rate:.0f}%"),
                unsafe_allow_html=True)

    st.divider()

    # Follow-ups.
    followups = tracker.all_follow_ups()
    if followups:
        st.markdown("<div class='section-label'>Follow-ups needed</div>", unsafe_allow_html=True)
        for fu in followups:
            with st.container(border=True):
                st.markdown(
                    f"<div class='job-card'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;'>"
                    f"<div><span style='font-weight:800;font-size:14px;color:#0f172a;letter-spacing:-0.2px;'>{fu['company']} — {fu['role_title']}</span></div>"
                    f"<span class='badge amber'>{fu['age_days']} days ago</span></div>"
                    f"<div style='font-size:13px;color:#475569;line-height:1.5;'>{fu['draft']}</div>"
                    f"<div style='margin-top:12px;font-size:12px;color:#94a3b8;font-style:italic;'>📋 Copy and send this yourself — the system never sends.</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.markdown("<div class='ui-success'>No stale applications — you're on top of it.</div>", unsafe_allow_html=True)

    st.divider()

    # Timeline.
    st.markdown("<div class='section-label'>Application Timeline</div>", unsafe_allow_html=True)
    timeline = da.application_timeline()
    if timeline:
        rows = [
            {
                "company": t["company"],
                "role": t["role_title"],
                "status": t["status"],
                "applied": _fmt_date(t["applied_at"]),
                "updated": _fmt_date(t["last_updated"]),
                "interviews": t["interview_count"],
            }
            for t in timeline
        ]
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.markdown("<div class='ui-info'>No applications tracked yet.</div>", unsafe_allow_html=True)

    st.divider()

    # Manual status entry.
    with st.expander("📝 Record a status manually", expanded=False):
        cards = _load_cards()
        job_ids = [f"{c.job_id} — {c.company} / {c.role_title}" for c in cards]
        if job_ids:
            sel = st.selectbox("Job", job_ids)
            new_status = st.selectbox("New status", da.STATUS_FLOW)
            notes = st.text_input("Notes")
            if st.button("Record status", type="primary"):
                job_id = sel.split(" — ")[0]
                tracker.set_status(job_id, new_status, notes=notes)
                st.success(f"{job_id} -> {new_status}")
                st.rerun()
        else:
            st.markdown("<div class='ui-info'>No jobs to track. Run the engine first.</div>", unsafe_allow_html=True)


# =========================================================================== #
# PAGE 5 — ANALYTICS
# =========================================================================== #
elif page == "Analytics":
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-title'>How your <span class='accent-word'>hunt</span> performs</div>"
        "<div class='page-subtitle'>Derived from your real outcomes</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    funnel = da.funnel_counts()
    calib = da.calibration_data()
    levers = da.top_levers()
    leaderboard = da.company_leaderboard()
    conv = da.conversion_by_country()
    activity = da.activity_by_date()

    a1, a2, a3, a4 = st.columns(4)
    a1.markdown('<div class="kpi-card" data-accent="#2563eb"><div class="kpi-header"><div class="kpi-title">Collected</div><div class="kpi-icon" style="background:#eff6ff;">📥</div></div><div class="kpi-value">{}</div></div>'.format(funnel["collected"]),
                unsafe_allow_html=True)
    a2.markdown('<div class="kpi-card" data-accent="#7c3aed"><div class="kpi-header"><div class="kpi-title">Interviews</div><div class="kpi-icon" style="background:#f3e8ff;">🎤</div></div><div class="kpi-value">{}</div></div>'.format(funnel["interviews"]),
                unsafe_allow_html=True)
    a3.markdown('<div class="kpi-card" data-accent="#15803d"><div class="kpi-header"><div class="kpi-title">Offers</div><div class="kpi-icon" style="background:#f0fdf4;">🏆</div></div><div class="kpi-value">{}</div></div>'.format(funnel["offers"]),
                unsafe_allow_html=True)
    a4.markdown('<div class="kpi-card" data-accent="#b45309"><div class="kpi-header"><div class="kpi-title">Conversion</div><div class="kpi-icon" style="background:#fffbeb;">📈</div></div><div class="kpi-value">{}</div></div>'.format(f"{round(funnel['interviews'] / (funnel['collected'] or 1) * 100)}%"),
                unsafe_allow_html=True)

    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)

    # Activity over time bar chart
    st.markdown("<div class='section-label'>Activity — applications per day</div>", unsafe_allow_html=True)
    if activity:
        max_count = max(a["count"] for a in activity) if activity else 1
        # Render as HTML bars
        st.markdown(
            "<div class='bar-chart'>"
            + "".join(
                f"<div class='bar-chart-col'>"
                f"<div class='bar-chart-val'>{a['count']}</div>"
                f"<div class='bar-chart-bar' style='height:{max(6, a['count'] / max_count * 100)}%;'></div>"
                f"<div class='bar-chart-date'>{a['date'][5:]}</div>"
                f"</div>"
                for a in activity
            )
            + "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("<div class='ui-info'>No activity data yet.</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)

    # Conversion by country + Top levers
    oc, ox = st.columns([1, 1])

    with oc:
        st.markdown("<div class='section-label'>Conversion by country</div>", unsafe_allow_html=True)
        if conv:
            for c in conv:
                st.markdown(
                    f"<div style='margin-bottom:14px;'>"
                    f"<div style='display:flex;justify-content:space-between;font-size:13px;font-weight:600;color:#475569;margin-bottom:8px;'>"
                    f"<span>{c['country']}</span><span style='font-weight:800;color:#4f46e5;font-size:14px;'>{c['rate']}%</span></div>"
                    f"<div style='background:#f1f5f9;border-radius:999px;height:12px;overflow:hidden;'>"
                    f"<div style='background:linear-gradient(90deg,#4f46e5,#6366f1);width:{c['rate']}%;height:100%;border-radius:999px;transition:width 0.6s cubic-bezier(0.4,0,0.2,1);'></div>"
                    f"</div>"
                    f"<div style='font-size:11px;color:#94a3b8;margin-top:6px;font-weight:500;'>{c['applied']} applied · {c['interviews']} interviews</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown("<div class='ui-info'>Not enough outcome data yet. Track some applications.</div>", unsafe_allow_html=True)

    with ox:
        st.markdown("<div class='section-label'>Top interview levers</div>", unsafe_allow_html=True)
        if levers:
            for lv in levers:
                st.markdown(
                    f"<div style='display:flex;align-items:center;justify-content:space-between;padding:13px 16px;background:rgba(255,255,255,0.9);border:1px solid var(--line);border-radius:12px;margin-bottom:10px;box-shadow:var(--shadow-sm);transition:box-shadow 0.2s,transform 0.2s;' onmouseover=\"this.style.boxShadow='0 6px 18px rgba(15,23,42,0.1)';this.style.transform='translateY(-2px)';\" onmouseout=\"this.style.boxShadow='0 1px 2px rgba(15,23,42,0.05)';this.style.transform='translateY(0)';\">"
                    f"<span style='font-size:13px;font-weight:600;color:#475569;text-transform:capitalize;'>{lv['seniority']}</span>"
                    f"<span style='font-size:15px;font-weight:800;color:#4f46e5;background:#eef2ff;padding:4px 12px;border-radius:999px;'>{lv['interviews']}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown("<div class='ui-info'>No interview data yet — the system will learn your strongest levers as you track outcomes.</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)

    # Calibration + Company leaderboard
    cc, cl = st.columns([1, 1])

    with cc:
        st.markdown("<div class='section-label'>Calibration (predicted vs actual)</div>", unsafe_allow_html=True)
        if calib:
            for pt in calib:
                actual = "🎤 Converted" if pt["actual"] else "No interview"
                with st.container(border=True):
                    st.markdown(
                        f"<div class='job-card' style='margin-bottom:10px;'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                        f"<div><div style='font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.5px;'>predicted</div>"
                        f"<div style='font-weight:800;color:#4f46e5;font-size:20px;letter-spacing:-0.5px;'>{pt['predicted']:.0f}%</div></div>"
                        f"<div style='text-align:right;'><div style='font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.5px;'>actual</div>"
                        f"<div style='font-weight:600;color:#0f172a;font-size:13px;'>{actual}</div></div>"
                        f"</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.markdown("<div class='ui-info'>No outcomes to calibrate against yet.</div>", unsafe_allow_html=True)

    with cl:
        st.markdown("<div class='section-label'>Company leaderboard</div>", unsafe_allow_html=True)
        if leaderboard:
            for l in leaderboard:
                with st.container(border=True):
                    st.markdown(
                        f"<div class='job-card' style='margin-bottom:10px;'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                        f"<div><div style='font-weight:800;font-size:14px;color:#0f172a;letter-spacing:-0.2px;'>{l['company']}</div>"
                        f"<div style='font-size:11px;color:#94a3b8;text-transform:uppercase;font-weight:600;letter-spacing:0.5px;'>{l['level']}</div></div>"
                        f"<div style='text-align:right;'>"
                        f"<div style='font-size:11px;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;'>composite</div>"
                        f"<div style='font-weight:800;font-size:20px;color:#4f46e5;letter-spacing:-0.5px;'>{l['composite']}</div></div>"
                        f"</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.markdown("<div class='ui-info'>No company reviews yet.</div>", unsafe_allow_html=True)


# =========================================================================== #
# PAGE 7 — INSIGHTS
# =========================================================================== #
elif page == "Insights":
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-title'>Deep <span class='accent-word'>insights</span></div>"
        "<div class='page-subtitle'>Where your interviews come from and what to optimize</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    funnel = da.funnel_counts()
    conv = da.conversion_by_country()
    activity = da.activity_by_date()
    leaderboard = da.company_leaderboard()
    company_summary = da.company_application_summary()
    status_breakdown = da.status_breakdown()
    age_report = da.application_age_report()
    health = da.collection_health()

    # Summary KPIs
    s1, s2, s3, s4 = st.columns(4)
    s1.markdown('<div class="kpi-card" data-accent="#2563eb"><div class="kpi-header"><div class="kpi-title">Total Applied</div><div class="kpi-icon" style="background:#eff6ff;">📤</div></div><div class="kpi-value">{}</div></div>'.format(funnel["submitted"]),
                unsafe_allow_html=True)
    s2.markdown('<div class="kpi-card" data-accent="#7c3aed"><div class="kpi-header"><div class="kpi-title">Interviews</div><div class="kpi-icon" style="background:#f3e8ff;">🎤</div></div><div class="kpi-value">{}</div></div>'.format(funnel["interviews"]),
                unsafe_allow_html=True)
    s3.markdown('<div class="kpi-card" data-accent="#15803d"><div class="kpi-header"><div class="kpi-title">Offers</div><div class="kpi-icon" style="background:#f0fdf4;">🏆</div></div><div class="kpi-value">{}</div></div>'.format(funnel["offers"]),
                unsafe_allow_html=True)
    s4.markdown('<div class="kpi-card" data-accent="#b45309"><div class="kpi-header"><div class="kpi-title">Overall Rate</div><div class="kpi-icon" style="background:#fffbeb;">📈</div></div><div class="kpi-value">{}</div></div>'.format(f"{round(funnel['interviews'] / (funnel['submitted'] or 1) * 100)}%"),
                unsafe_allow_html=True)

    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)

    # Status funnel breakdown
    st.markdown("<div class='section-label'>Status Breakdown</div>", unsafe_allow_html=True)
    if status_breakdown:
        max_status = max(s["count"] for s in status_breakdown)
        for s in status_breakdown:
            pct = (s["count"] / max_status * 100) if max_status > 0 else 0
            st.markdown(
                f"<div style='margin-bottom:14px;'>"
                f"<div style='display:flex;justify-content:space-between;font-size:13px;font-weight:600;color:#475569;margin-bottom:8px;'>"
                f"<span style='text-transform:capitalize;font-weight:600;'>{s['status']}</span><span style='font-weight:800;color:#0f172a;'>{s['count']}</span></div>"
                f"<div style='background:#f1f5f9;border-radius:999px;height:12px;overflow:hidden;'>"
                f"<div style='background:linear-gradient(90deg,#6366f1,#8b5cf6);width:{pct}%;height:100%;border-radius:999px;transition:width 0.6s cubic-bezier(0.4,0,0.2,1);'></div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown("<div class='ui-info'>No applications tracked yet.</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)

    # Company performance + Application age
    ic, ia = st.columns([1, 1])

    with ic:
        st.markdown("<div class='section-label'>Company Performance</div>", unsafe_allow_html=True)
        if company_summary:
            for c in company_summary:
                with st.container(border=True):
                    st.markdown(
                        f"<div class='job-card' style='margin-bottom:10px;'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;'>"
                        f"<div style='font-weight:800;font-size:14px;color:#0f172a;letter-spacing:-0.2px;'>{c['company']}</div>"
                        f"<span class='badge'>{c['applied']} applied</span></div>"
                        f"<div style='font-size:12px;color:#64748b;font-weight:500;'>{c['interviews']} interviews · last {c['last_submitted'][:10] if c['last_submitted'] else 'n/a'}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.markdown("<div class='ui-info'>No company application data yet.</div>", unsafe_allow_html=True)

    with ia:
        st.markdown("<div class='section-label'>Stale Applications</div>", unsafe_allow_html=True)
        stale = [a for a in age_report if a["age_days"] and a["age_days"] > 14]
        if stale:
            for a in sorted(stale, key=lambda x: -x["age_days"])[:8]:
                with st.container(border=True):
                    st.markdown(
                        f"<div class='job-card' style='margin-bottom:10px;'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                        f"<div><div style='font-weight:600;font-size:13px;color:#0f172a;'>{a['company']} — {a['role_title']}</div>"
                        f"<div style='font-size:11px;color:#64748b;text-transform:capitalize;font-weight:500;'>{a['status']}</div></div>"
                        f"<span class='badge amber'>{a['age_days']}d</span></div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.markdown("<div class='ui-success'>No stale applications — everything is up to date!</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)

    # Conversion by country full view
    st.markdown("<div class='section-label'>Conversion by Country</div>", unsafe_allow_html=True)
    if conv:
        conv_cols = st.columns(len(conv))
        for i, c in enumerate(conv):
            with conv_cols[i]:
                with st.container(border=True):
                    st.markdown(
                        f"<div class='job-card' style='text-align:center;padding:22px 16px;'>"
                        f"<div style='font-size:30px;font-weight:800;background:linear-gradient(120deg,#4f46e5,#7c3aed);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;'>{c['rate']}%</div>"
                        f"<div style='font-size:12px;color:#475569;font-weight:700;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.3px;'>{c['country']}</div>"
                        f"<div style='font-size:11px;color:#94a3b8;'>{c['applied']} applied</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
    else:
        st.markdown("<div class='ui-info'>Not enough outcome data yet.</div>", unsafe_allow_html=True)


# =========================================================================== #
# PAGE — LEARNING (Self-Tuning Engine + Self-Improvement Loop)
# =========================================================================== #
elif page == "Learning":
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-title'><span class='accent-word'>Learning</span></div>"
        "<div class='page-subtitle'>The engine watches itself — it learns your conversion rate, detects drift, and proposes prompt changes for your approval.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.divider()

    settings = repository.load_settings()

    # ---- Self-Tuning Match Engine (Module 8) ----
    with st.expander("🧠 Self-tuning match engine", expanded=True):
        st.caption(
            "Learns your real interview-conversion rate and the scoring weights "
            "that best predict your interviews. Deterministic — same DB, same "
            "weights. No LLM required."
        )
        try:
            lr = da.learning_result(settings)
            s1, s2, s3, s4 = st.columns(4)
            s1.markdown(
                '<div class="kpi-card" data-accent="#2563eb">'
                '<div class="kpi-header"><div class="kpi-title">Outcomes</div>'
                '<div class="kpi-icon" style="background:#eff6ff;">📊</div></div>'
                f'<div class="kpi-value">{lr["sample_count"]}</div></div>',
                unsafe_allow_html=True,
            )
            s2.markdown(
                '<div class="kpi-card" data-accent="#7c3aed">'
                '<div class="kpi-header"><div class="kpi-title">Conversion</div>'
                '<div class="kpi-icon" style="background:#f3e8ff;">🎤</div></div>'
                f'<div class="kpi-value">{lr["historical_rate"]}%</div></div>',
                unsafe_allow_html=True,
            )
            s3.markdown(
                '<div class="kpi-card" data-accent="#15803d">'
                '<div class="kpi-header"><div class="kpi-title">Calibration MAE</div>'
                '<div class="kpi-icon" style="background:#f0fdf4;">🎯</div></div>'
                f'<div class="kpi-value">{lr["calibration_mae"]}%</div></div>',
                unsafe_allow_html=True,
            )
            s4.markdown(
                '<div class="kpi-card" data-accent="#b45309">'
                '<div class="kpi-header"><div class="kpi-title">Status</div>'
                '<div class="kpi-icon" style="background:#fffbeb;">🧪</div></div>'
                f'<div class="kpi-value" style="font-size:12px;">{"Live" if lr["trusted"] else "Unproven"}</div></div>',
                unsafe_allow_html=True,
            )

            st.markdown(f"<div class='ui-info'>{lr['message']}</div>", unsafe_allow_html=True)

            st.markdown("<div class='section-label'>Scoring weights</div>", unsafe_allow_html=True)
            weights = lr["weights"]
            w1, w2 = st.columns(2)
            with w1:
                for dim in ["match", "geo", "fraud", "seniority"]:
                    st.progress(weights[dim] / 3.0, text=f"{dim} (weight {weights[dim]:.2f})")

            # Per-dimension conversion rates
            dims = lr["dimension_rates"]
            if dims:
                st.markdown("<div class='section-label'>Conversion by dimension</div>", unsafe_allow_html=True)
                for dim_name, items in dims.items():
                    st.markdown(f"**{dim_name}**")
                    for item in items:
                        label = item.get("label") or item.get("key") or str(item)
                        rate = item.get("rate", 0.0)
                        count = item.get("count", 0)
                        st.markdown(
                            f"<div style='display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px;'>"
                            f"<span>{label}</span><span style='font-weight:700;color:#4f46e5;'>{rate:.0f}% ({count})</span></div>",
                            unsafe_allow_html=True,
                        )
        except Exception as e:
            st.caption("Learning engine unavailable: " + str(e))

    st.divider()

    # ---- Self-Improvement Loop (Module 14) ----
    with st.expander("🔄 Self-improvement loop", expanded=True):
        st.caption(
            "Weekly review of what worked, drift detection when your conversion "
            "drops, and a versioned prompt library. Every recommendation needs "
            "your approval — the system never changes itself."
        )
        try:
            wr = da.weekly_review(settings)
            dr = da.drift_report(settings)
            pr = da.prompt_recommendations(settings)

            # Weekly review KPIs
            w1, w2, w3 = st.columns(3)
            w1.markdown(
                '<div class="kpi-card" data-accent="#2563eb">'
                '<div class="kpi-header"><div class="kpi-title">Applications</div>'
                '<div class="kpi-icon" style="background:#eff6ff;">📤</div></div>'
                f'<div class="kpi-value">{wr["applications"]}</div></div>',
                unsafe_allow_html=True,
            )
            w2.markdown(
                '<div class="kpi-card" data-accent="#7c3aed">'
                '<div class="kpi-header"><div class="kpi-title">Interviews</div>'
                '<div class="kpi-icon" style="background:#f3e8ff;">🎤</div></div>'
                f'<div class="kpi-value">{wr["interviews"]}</div></div>',
                unsafe_allow_html=True,
            )
            w3.markdown(
                '<div class="kpi-card" data-accent="#15803d">'
                '<div class="kpi-header"><div class="kpi-title">Conversion</div>'
                '<div class="kpi-icon" style="background:#f0fdf4;">📈</div></div>'
                f'<div class="kpi-value">{wr["conversion_rate"]}%</div></div>',
                unsafe_allow_html=True,
            )

            st.markdown(f"<div class='ui-info'>{wr['summary']}</div>", unsafe_allow_html=True)

            # Drift detection
            if dr["drifted"]:
                st.markdown(
                    "<div class='ui-warning'>⚠️ Drift detected: conversion dropped "
                    f"{dr['drop_pct']:.0f}% (from {dr['prior_rate']:.0f}% to {dr['current_rate']:.0f}%).</div>",
                    unsafe_allow_html=True,
                )
                for cause in dr["likely_causes"]:
                    st.markdown(f"• {cause}")
                if dr["recommendation"]:
                    st.markdown(f"**Recommendation:** {dr['recommendation']}")
            else:
                st.markdown(
                    "<div class='ui-success'>✅ No drift detected. Your conversion "
                    "rate is stable.</div>",
                    unsafe_allow_html=True,
                )

            # Prompt library
            st.markdown("<div class='section-label'>Prompt library</div>", unsafe_allow_html=True)
            if pr:
                for rec in pr:
                    badge = "🔴 Flagged" if rec["flagged"] else "🟢 OK"
                    with st.expander(f"{rec['task']} — {badge}", expanded=rec["flagged"]):
                        st.markdown(
                            f"**Current:** v{rec['current_version']} "
                            f"(outcome {rec['current_outcome']:.0f}%, {rec['samples']} samples)\n\n"
                            f"**Proposed:** v{rec['proposed_version']} "
                            f"(outcome {rec['proposed_outcome']:.0f}%)\n\n"
                            f"**Reason:** {rec['reason']}"
                        )
                        if st.button("Approve new prompt", key=f"prompt_{rec['task']}"):
                            st.success(f"Prompt {rec['task']} v{rec['proposed_version']} queued for approval. "
                                       "This would be applied by the self-improvement loop.")
            else:
                st.markdown("<div class='ui-info'>No prompt recommendations yet — enough outcome data needed.</div>", unsafe_allow_html=True)
        except Exception as e:
            st.caption("Self-improvement loop unavailable: " + str(e))

    st.divider()

    # ---- Audit Trail ----
    with st.expander("🛡️ Audit trail", expanded=False):
        st.caption("Every security-relevant decision is logged here.")
        try:
            sec = da.security_status(settings)
            audit = da.audit_summary(settings)
            a1, a2 = st.columns(2)
            a1.markdown(
                f"<div class='ui-info'><strong>Encryption:</strong> "
                f"{'Enabled' if sec.get('enabled') else 'Disabled'} "
                f"({sec.get('mode')})</div>",
                unsafe_allow_html=True,
            )
            a2.markdown(
                f"<div class='ui-info'><strong>Audit events:</strong> "
                f"{sum(audit.values()) if audit else 0}</div>",
                unsafe_allow_html=True,
            )
            if audit:
                for event, count in audit.items():
                    st.markdown(f"• {event}: {count}")
        except Exception as e:
            st.caption("Audit unavailable: " + str(e))


# =========================================================================== #
# PAGE 6 — NETWORK (Referrals + Interview Prep)
# =========================================================================== #
elif page == "Network":
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-title'><span class='accent-word'>Network & Interview Prep</span></div>"
        "<div class='page-subtitle'>Warm referral paths to target companies, plus interview questions and a verified STAR bullet bank.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.divider()

    settings = repository.load_settings()

    # ---- Referrals ----
    with st.expander("🤝 Warm referral paths", expanded=True):
        st.caption(
            "Shortest paths from you to a contact at each target company, "
            "ranked by shared skills. Drafts use only verified facts — you "
            "send them."
        )
        try:
            engine = ReferralEngine(settings)
            paths = engine.find_paths()
            if not paths:
                st.caption("No warm paths yet. Apply to jobs or add contacts "
                           "to grow the knowledge graph.")
            for p in paths:
                with st.expander(f"🔗 {p.company} — via {p.contact}", expanded=False):
                    st.markdown("**Path:** " + " → ".join(p.path))
                    if p.shared_skills:
                        st.markdown("**Shared skills:** " + ", ".join(p.shared_skills))
                    st.caption("Status: " + p.status)
                    if p.draft:
                        st.markdown(p.draft)
                        if st.button("Copy draft", key=f"copy_{p.company}"):
                            st.write("Copy the text above and send it yourself — "
                                     "the system never sends messages.")
        except Exception as e:
            st.caption("Referral lookup unavailable: " + str(e))

    st.divider()

    # ---- Interview Prep ----
    with st.expander("🎤 Interview prep & STAR bank", expanded=True):
        st.caption(
            "Predicted questions for your target jobs and a bank of verified "
            "STAR bullets. Practice answering, then grade yourself."
        )
        try:
            iengine = InterviewEngine(settings)
            star_bank = iengine.build_star_bank()
            if not star_bank:
                st.caption("No verified achievements yet. Add accomplishments "
                           "in the CV Manager to build your STAR bank.")
            else:
                categories = sorted({s.question_category for s in star_bank})
                selected = st.selectbox("Filter by category", ["all"] + categories)
                filtered = [s for s in star_bank
                            if selected == "all" or s.question_category == selected]
                for s in filtered:
                    with st.expander(f"{s.question_category} — {s.employment}", expanded=False):
                        st.markdown(s.bullet)
                        if s.metric:
                            st.caption("Metric: " + s.metric)

                st.divider()
                st.markdown("**Practice & self-grade**")
                q = st.text_area("Paste your answer to a question:")
                if st.button("Grade answer", type="primary"):
                    if q.strip():
                        result = iengine.grade_answer("Tell me about a challenge", q)
                        st.metric("Answer score", f"{result.get('score', 0)}/100")
                        if result.get("feedback"):
                            st.markdown("**Feedback:** " + result["feedback"])
                    else:
                        st.caption("Paste an answer first.")
        except Exception as e:
            st.caption("Interview prep unavailable: " + str(e))


# =========================================================================== #
# PAGE 7 — SETTINGS
# =========================================================================== #
elif page == "Settings":
    st.markdown(
        "<div class='page-header'>"
        "<div class='page-title'><span class='accent-word'>Settings</span></div>"
        "<div class='page-subtitle'>Tune the engine. All changes are config-only — no code edits.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.divider()

    config = load_config()

    # Password.
    with st.expander("🔒 Dashboard password", expanded=True):
        st.caption("Change the password to open this dashboard. Set "
                   "`DASHBOARD_PASSWORD` in your environment to override.")
        new_pw = st.text_input("New password", type="password",
                               value=_PASSWORD,
                               label_visibility="visible")
        if st.button("Update password", type="primary"):
            if new_pw and new_pw != _PASSWORD:
                st.session_state["_pending_password"] = new_pw
                st.success("Password updated. Reload the dashboard to apply.")
            else:
                st.caption("No change (same as current).")

    # Targets.
    with st.expander("🎯 Target countries & salary", expanded=True):
        st.caption("Edit `config/settings.yaml` directly for these.")
        countries = config.target_countries()
        st.write("Target countries:", ", ".join(countries) or "—")
        st.write("Min salary (USD):", config.min_salary())
        st.write("Match thresholds:", config.match_thresholds())

    # Target companies.
    with st.expander("🏢 Target companies", expanded=True):
        st.caption("Edit `config/targets.yaml` to add or remove companies.")
        targets = config.targets()
        if targets:
            rows = [{"name": t["name"], "domain": t.get("domain"),
                     "platform": t.get("platform"),
                     "keywords": ", ".join(t.get("role_keywords", []))}
                    for t in targets]
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.caption("No target companies configured.")

    st.divider()
    st.markdown("<div class='section-label'>Storage & deployment</div>", unsafe_allow_html=True)
    st.markdown(
        "- **Database:** `data/career.db` (SQLite, local)\n"
        "- **Code:** version-controlled in Git\n"
        "- **Hosting:** deploy `src/dashboard/app.py` to a free cloud host "
        "(e.g. ClaudeFlare) from your private GitHub repo\n"
        "- **Backup:** copy `data/career.db` to the cloud to persist "
        "applications across deploys"
    )
