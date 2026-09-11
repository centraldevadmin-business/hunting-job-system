#!/usr/bin/env python3
"""Generate a comprehensive project overview document (.docx) for the Hunting Job System."""
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()

# --- Base styles ---
normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(11)

def heading(text, level=1):
    h = doc.add_heading(text, level=level)
    return h

def para(text, bold=False, italic=False, size=11):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    run.italic = italic
    run.font.size = Pt(size)
    return p

def bullet(text, style="List Bullet"):
    return doc.add_paragraph(text, style=style)

def numbered(text):
    p = doc.add_paragraph(style="List Paragraph")
    run = p.add_run(text)
    return p

# ============================================================
# TITLE PAGE
# ============================================================
title = doc.add_paragraph()
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
tr = title.add_run("Hunting Job System")
tr.bold = True
tr.font.size = Pt(28)
tr.font.color.rgb = RGBColor(0x4f, 0x8c, 0xff)

sub = doc.add_paragraph()
sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
sr = sub.add_run("Autonomous Remote Career Execution & Tracking Engine")
sr.font.size = Pt(16)
sr.italic = True

sub2 = doc.add_paragraph()
sub2.alignment = WD_ALIGN_PARAGRAPH.CENTER
sr2 = sub2.add_run("Complete Project Documentation")
sr2.font.size = Pt(14)

sub3 = doc.add_paragraph()
sub3.alignment = WD_ALIGN_PARAGRAPH.CENTER
sr3 = sub3.add_run("Zero-cost · Zero-hallucination · Human-in-the-loop")
sr3.font.size = Pt(12)
sr3.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

doc.add_paragraph()  # spacer

# ============================================================
# 1. EXECUTIVE SUMMARY
# ============================================================
heading("1. Executive Summary", 1)
para(
    "The Hunting Job System is a Python application that automates the discovery and "
    "preparation half of a remote job search, while deliberately leaving the submission "
    "to a human. It is built for a single candidate — a Bangladesh-based Business / Systems "
    "Analyst targeting remote roles at curated companies across the UK, EU, USA, Canada, "
    "Australia, New Zealand, and the Middle East."
)
para(
    "It is intentionally NOT a spammy application bot. It is a \"precision strike\" system: "
    "it would rather surface 3–10 vetted, tailored roles than 100 generic ones. The system "
    "finds jobs on target companies' own career pages, vets each posting for geo-eligibility "
    "and fraud, tailors a zero-hallucination resume for every match, and stages everything "
    "locally so the human can review, authorize, and click submit on the company's page."
)

# ============================================================
# 2. CORE PRINCIPLES
# ============================================================
heading("2. Three Core Principles", 1)

principles = [
    ("$0 Cost", "No paid APIs, no cloud subscriptions, no usage billing. LLM and embedding "
     "work runs on free tiers (Grok, Gemini); everything else runs locally on the user's machine."),
    ("Zero Hallucination", "Every fact in a generated resume traces back to the candidate's "
     "verified master record. The system may rephrase and reorder experience — it may never "
     "invent a skill, metric, date, tool, or employer."),
    ("Human-in-the-Loop", "The system discovers, scores, and prepares. The human reviews, "
     "authorizes, and clicks submit. It never sends email, touches an inbox, or applies on the user's behalf."),
]
for name, desc in principles:
    p = doc.add_paragraph()
    r = p.add_run(name + ": ")
    r.bold = True
    p.add_run(desc)

# ============================================================
# 3. WHAT WE ARE DOING (THE LOOP)
# ============================================================
heading("3. What We Are Doing — The Daily Loop", 1)
para(
    "The system runs on a simple two-part daily rhythm: an automatic morning pass and a "
    "human-driven evening review."
)
heading("Morning (automatic)", 2)
para(
    "The system wakes once at dawn and, for each target company, scrapes its OWN career page, "
    "applies a geo-compliance filter, runs a deterministic fraud score, scores the match against "
    "the candidate's profile, ranks results by \"brutal possibility,\" and tailors a resume for "
    "each match. Everything is staged locally in SQLite."
)
heading("Evening (human-driven)", 2)
para(
    "The user opens the Streamlit dashboard, reviews each job side-by-side (JD + match breakdown "
    "+ tailored resume), clicks AUTHORIZE on the ones they want, the system opens the company's "
    "own application page pre-filled, and the user clicks submit. Outcomes (interview / reject / "
    "silent) are logged and feed a self-tuning engine that makes future cycles smarter."
)

para("Data flow:", bold=True)
para(
    "target company → career-page posting → geo-filtered → fraud-scored → company-reviewed → "
    "possibility-scored → tailored resume → staged record → (human authorize) → direct-company-page "
    "submit → tracked",
    italic=True
)

# ============================================================
# 4. TECHNOLOGY STACK
# ============================================================
heading("4. Technology Stack", 1)
para("The entire stack is free or self-hosted — no paid services anywhere.")

stack = [
    ("Scraping / Ingestion", "Playwright, BeautifulSoup, lxml", "Free"),
    ("Vector Store", "ChromaDB (local, embedded)", "Free"),
    ("LLM (rephrasing, distilling)", "Grok / Gemini (free tier)", "Free tier"),
    ("Embeddings", "Gemini free tier (text-embedding-004)", "Free tier"),
    ("PDF Generation", "ReportLab", "Free"),
    ("Dashboard", "Streamlit", "Free"),
    ("Database", "SQLite3", "Free"),
    ("Fraud / Geo / Match Rules", "Deterministic Python (no API)", "Free"),
    ("Scheduling", "APScheduler", "Free"),
    ("Hosting", "User's own machine (or Streamlit Community Cloud)", "Free"),
]
table = doc.add_table(rows=1, cols=3)
table.style = "Light Grid Accent 1"
hdr = table.rows[0].cells
hdr[0].text = "Layer"
hdr[1].text = "Technology"
hdr[2].text = "Cost"
for row in table.rows:
    for c in row.cells:
        for p in c.paragraphs:
            p.paragraph_format.space_after = Pt(2)
for layer, tech, cost in stack:
    cells = table.add_row().cells
    cells[0].text = layer
    cells[1].text = tech
    cells[2].text = cost

para("", 0)
para("Programming language: Python 3.11+. Testing: pytest. Deployment: Docker + Streamlit Community Cloud.", italic=True)

# ============================================================
# 5. KEY FEATURES
# ============================================================
heading("5. Key Features", 1)

heading("5.1 Core Pipeline (Implemented)", 2)
core = [
    ("Precision Ingestion", "Monitors target companies' OWN career pages (no job boards). "
     "Fallback chain: Greenhouse JSON API → Lever/Ashby/Workable APIs → HTML parse → direct careers "
     "page → manual paste. Each result carries a confidence label. Automatic dedup."),
    ("Geo-Compliance & JD Distiller", "Deterministic keyword rules decide eligibility "
     "(worldwide/B2B/EOR = accept; US citizen/domestic = reject; ambiguous = human-review queue). "
     "Country Eligibility Matrix tags each posting. JD Distiller extracts structured requirements."),
    ("Anti-Fraud & Company Review", "Deterministic binary gate (no LLM): domain-match, "
     "payment/equipment requests, salary absurdity, free-domain contact email. Composite 0–100 score "
     "→ green/amber/red; red is auto-rejected. Per-company reputation score."),
    ("Zero-Hallucination Resume Engine (Core IP)", "Master record is the only source of facts. "
     "Vector retrieval of authentic bullets, deterministic validator rejects unknown tokens, "
     "Claim-Integrity Layer catches distortion, variant generator emits 2–3 A/B structures, "
     "ReportLab renders ATS-friendly PDFs, zero-mistakes cross-checks."),
    ("Streamlit Dashboard", "Overview (KPIs, funnel, market signal), Hunt (daily queue ranked by "
     "\"brutal possibility\"), CV Manager, Track, Analytics, Settings. \"Brutal possibility\" is a "
     "multiplicative, honest percentage."),
    ("Direct Application (Human-Assisted)", "ATS resolver maps a posting to the company's real "
     "application page, browser assist pre-fills known fields, human checkpoint pauses before submit, "
     "submission package assembles resume + cover letter + checklist."),
    ("Status Tracking", "One-click status lifecycle (staged → applied → screen → interview → "
     "final → offer/reject/silent), silent tracker flags stuck applications, follow-up message drafts."),
]
for name, desc in core:
    p = doc.add_paragraph()
    r = p.add_run(name + ": ")
    r.bold = True
    p.add_run(desc)

heading("5.2 Advanced Layer (Planned)", 2)
advanced = [
    ("Self-Tuning Engine", "Learns which countries/seniority/skill-combos get YOU interviews; "
     "Bayesian smoothing (only claims self-tuning at 30+ outcomes)."),
    ("Knowledge Graph", "Networks of companies/people/roles/skills; similar-company lead discovery."),
    ("Referral & Network", "Warm-path finder + personalized referral drafts (never auto-sends)."),
    ("Interview Intelligence", "Predicted questions, STAR bullet bank, mock-interview grader, company-intel digest."),
    ("Negotiation & Comp", "Market benchmarking, offer analysis, talking-points scripts."),
    ("Multi-Agent Orchestrator", "Specialized agents passing structured messages with human gates."),
    ("Self-Improvement Loop", "Weekly review, drift detection, versioned prompt library, audit trail."),
]
for name, desc in advanced:
    p = doc.add_paragraph(style="List Bullet")
    r = p.add_run(name + ": ")
    r.bold = True
    p.add_run(desc)

heading("5.3 Hardening Layer (Woven Throughout)", 2)
for item in [
    "Claim-Integrity Layer — catches distortion of real facts, not just invention",
    "Ingestion Fallback Chain — no single source can kill the pipeline",
    "Local Security — encrypts the SQLite file (Fernet), timestamped local backups, audit log",
    "Market-Sentiment Signal — \"dry market, not a system problem\"",
    "Maintenance-Mode Detector — stops recommending self-tuned weights when idle",
    "Self-Audit / Drift Detection — the system watches its own predictions",
]:
    bullet(item)

# ============================================================
# 6. WHAT WE WANT TO ACHIEVE
# ============================================================
heading("6. What We Want to Achieve", 1)
para(
    "The long-term vision is a compounding flywheel: every outcome makes the next cycle smarter."
)
goals = [
    "Surface 3–10 vetted, tailored roles per week instead of 100 generic ones",
    "Guarantee zero hallucination — every resume fact is provable from the master record",
    "Keep the entire system at $0 operation, forever",
    "Protect privacy — all data stays on the user's machine, never transmitted",
    "Eliminate the risk of fabricated claims that could damage the candidate's reputation",
    "Make the job hunt a sniper operation, not a spray-and-pray bot",
    "Self-improve over time so the same daily effort produces progressively better results",
]
for i, goal in enumerate(goals, 1):
    p = doc.add_paragraph(style="List Paragraph")
    r = p.add_run(f"{i}. ")
    r.bold = True
    p.add_run(goal)

# ============================================================
# 7. HARD CONSTRAINTS
# ============================================================
heading("7. Hard Constraints (Design Guardrails)", 1)
for item in [
    "No job boards — monitors target companies' own career pages only",
    "No cold outreach — applies where the company has posted",
    "No email access — status is tracked manually, one click at a time",
    "No local GPU — NLP runs on free-tier cloud APIs",
    "Zero hallucination — every resume fact traces to the confirmed master record",
    "Human executes — the system never submits for the user",
]:
    bullet(item)

# ============================================================
# 8. DEPLOYMENT
# ============================================================
heading("8. Deployment", 1)
para(
    "The application is containerized with a Dockerfile and can be deployed on any container "
    "platform. It has been deployed to Streamlit Community Cloud, which builds and serves the app "
    "directly from the GitHub repository. The dashboard runs headless on port 8501 with the "
    "login/signup gate disabled for public auditing."
)

# ============================================================
# 9. HOW TO RUN
# ============================================================
heading("9. How To Run It", 1)
steps = [
    "Set up the environment: python3 -m venv .venv && source .venv/bin/activate",
    "Install dependencies: pip install -e '.[dev]' && playwright install chromium",
    "Add free-tier keys: cp .env.example .env (then fill in GOOGLE_API_KEY)",
    "Initialize the database: python -m src.db.repository",
    "Run the dashboard: streamlit run src/dashboard/app.py",
    "(Optional) run one hunt cycle: python scheduler.py --once",
    "Run tests: pytest",
]
for i, step in enumerate(steps, 1):
    p = doc.add_paragraph(style="List Paragraph")
    r = p.add_run(f"{i}. ")
    r.bold = True
    p.add_run(step)

# ============================================================
# 10. CANDIDATE PROFILE
# ============================================================
heading("10. The Candidate Profile (Source of Truth)", 1)
para(
    "The system is built around one person. Before any job is hunted, the candidate's verified "
    "career record is loaded as a structured master record. Every field is reviewed and confirmed "
    "before it enters the record. The master record is the only allowed source of facts — the "
    "system may rephrase, reorder, and reweight authentic experience, but may never add a skill, "
    "metric, tool, date, degree, or employer that is not in the confirmed record."
)

# ============================================================
# SAVE
# ============================================================
out = "/Users/nafiz/Downloads/Hunting job system/Hunting-Job-System-Overview.docx"
doc.save(out)
print("Saved:", out)
