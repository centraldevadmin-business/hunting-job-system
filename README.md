# Hunting Job System

An autonomous remote career execution and tracking engine.

**Goal:** find remote Business Analyst / Systems Analyst roles at curated target
companies, tailor a zero-hallucination resume for each, and stage everything for
**human-executed submission**.

## Hard constraints (non-negotiable)

- **$0 cost** — no paid APIs, no cloud subscriptions, no usage billing.
- **No local compute** — NLP/embeddings run on free-tier cloud APIs (Grok, Gemini).
- **No email access** — status is tracked manually on the dashboard (one click).
- **No job boards** — monitor target companies' OWN career pages only.
- **No cold outreach** — apply where the company has posted, on their own page.
- **Zero hallucination** — every resume fact traces to your confirmed master record.
- **Human-in-the-loop** — the system discovers and prepares; **you** click submit.
- **Manual status tracking** — no inbox permissions, no OAuth.

## Architecture

See `../PLAN.md` for the full business requirements, architecture, and design
decisions. See `../MODULE_STEPS.md` for incremental, verifiable build steps.

## Project layout

```
hunting-job-system/
├── pyproject.toml          # dependencies (all free / open-source)
├── .env.example            # free-tier API keys (copy to .env)
├── .gitignore              # never commit secrets / db / pdfs
├── scheduler.py            # daily orchestration (wakes once at dawn)
├── config/
│   ├── settings.yaml       # thresholds, countries, paths
│   └── targets.yaml        # curated target companies
├── src/
│   ├── db/                 # schema, connection, repository
│   ├── ingestion/          # Module 1
│   ├── compliance/         # Module 2
│   ├── fraud/              # Module 3
│   ├── resume/             # Module 4 (CORE)
│   ├── distiller/          # JD distiller
│   ├── security/           # hardening: encryption + backups
│   ├── dashboard/          # Module 5 (Streamlit)
│   ├── execution/          # Module 6
│   ├── tracking/           # Module 7
│   ├── learning/           # Module 8
│   ├── knowledge/          # Module 9
│   ├── network/            # Module 10
│   ├── interview/          # Module 11
│   ├── comp/               # Module 12
│   ├── agents/             # Module 13
│   └── self_improve/       # Module 14
├── tests/                  # unit tests
├── data/                   # local sqlite db + cache + backups (git-ignored)
└── output/resumes/         # generated PDFs (git-ignored)
```

## Setup (Module 0)

```bash
# 1. Create a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -e ".[dev]"

# 3. Install Playwright browsers (for ingestion + execution)
playwright install chromium

# 4. Copy env and fill in free-tier keys
cp .env.example .env

# 5. Initialize the database
python -m src.db.repository
```

## Run

```bash
# Run a single hunt cycle (manual / test)
python scheduler.py --once

# Start the dashboard
streamlit run src/dashboard/app.py
```

## Build order

1. Module 0 — scaffolding (this file)
2. Module 4 — master record + validator (core IP)
3. Module 1 — Greenhouse ingestion + fallback chain
4. Module 2 — geo-compliance + JD distiller
5. Module 3 — anti-fraud + company review
6. Module 5 — dashboard (Hunt + Analytics)
7. Module 6 — direct company-page application
8. Module 7 — status tracking
9. Module 8 — self-tuning (only after 30+ outcomes)
10. Modules 9–14 — advanced layer
11. Hardening layer — woven throughout

## Testing

```bash
pytest
```
