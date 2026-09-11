-- ============================================================
-- SQLite schema for the hunting-job-system.
-- Verified career master record (immutable source of truth) +
-- ingestion pipeline + tracking + advanced layer.
-- ============================================================

-- ----- Verified career master record -----

CREATE TABLE IF NOT EXISTS career_profile (
    id INTEGER PRIMARY KEY,
    full_name TEXT,
    email TEXT,
    phone TEXT,
    location TEXT,
    linked_url TEXT,
    portfolio_url TEXT
);

CREATE TABLE IF NOT EXISTS employment (
    id INTEGER PRIMARY KEY,
    company TEXT,
    role TEXT,
    start_date TEXT,
    end_date TEXT,
    current INTEGER,          -- 1 = current job, 0 = past
    location TEXT
);

CREATE TABLE IF NOT EXISTS achievement (
    id INTEGER PRIMARY KEY,
    employment_id INTEGER,
    bullet TEXT,                       -- authentic accomplishment
    tools TEXT,                        -- comma list, e.g. "SQL, Tableau"
    metric TEXT,                       -- e.g. "reduced latency 40%"
    FOREIGN KEY(employment_id) REFERENCES employment(id)
);

CREATE TABLE IF NOT EXISTS skill (
    name TEXT PRIMARY KEY,
    level TEXT
);

CREATE TABLE IF NOT EXISTS education (
    id INTEGER PRIMARY KEY,
    institution TEXT,
    degree TEXT,
    year INTEGER,
    verified INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS constraints (
    id INTEGER PRIMARY KEY,
    target_roles TEXT,                 -- comma list
    target_countries TEXT,             -- comma list
    min_salary INTEGER,
    notice_period_days INTEGER,
    visa_needs TEXT
);

-- ----- Ingestion -----

CREATE TABLE IF NOT EXISTS raw_jobs (
    id TEXT PRIMARY KEY,                 -- normalized id
    source TEXT, source_type TEXT, url TEXT,
    company TEXT, role_title TEXT, jd TEXT,
    salary TEXT, location TEXT, posted_at TEXT,
    canonical_ats_url TEXT,               -- resolved if known
    fetched_at TEXT
);

-- ----- Enriched ingestion -----

-- Deadline + posting metadata for every raw job.
CREATE TABLE IF NOT EXISTS job_deadlines (
    job_id TEXT PRIMARY KEY REFERENCES raw_jobs(id),
    deadline TEXT,                       -- ISO date if the JD names one
    posted_at TEXT,                      -- when the posting was published
    days_remaining INTEGER,              -- computed from deadline
    is_urgent INTEGER DEFAULT 0,         -- deadline within 7 days
    freshness TEXT DEFAULT 'fresh',      -- fresh|aging|stale
    last_checked TEXT
);

-- ----- Pipeline results -----

CREATE TABLE IF NOT EXISTS scored_jobs (
    id TEXT PRIMARY KEY REFERENCES raw_jobs(id),
    geo_eligible INTEGER, geo_confidence REAL,
    fraud_score INTEGER, fraud_flags TEXT,
    match_score REAL,
    status TEXT,                          -- pending|accepted|rejected|review
    reviewed_at TEXT
);

-- JD Distiller output (structured 'what they really want')

CREATE TABLE IF NOT EXISTS jd_distill (
    job_id TEXT PRIMARY KEY REFERENCES scored_jobs(id),
    top_requirements TEXT,                -- comma list
    must_have TEXT, nice_to_have TEXT,
    red_flags TEXT, actual_seniority TEXT,
    distilled_at TEXT
);

-- Company review (legitimacy + hiring activity), aggregated per company

CREATE TABLE IF NOT EXISTS company_reviews (
    company TEXT PRIMARY KEY,
    legitimacy INTEGER, activity INTEGER, composite INTEGER,
    level TEXT                          -- green|amber|red
);

-- Resume variants (deterministic A/B)

CREATE TABLE IF NOT EXISTS resume_variants (
    id INTEGER PRIMARY KEY,
    job_id TEXT REFERENCES scored_jobs(id),
    variant TEXT,                         -- skills-first|impact-first|chronological
    resume_text TEXT, validated INTEGER,
    created_at TEXT
);

-- Resumes

CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY,
    job_id TEXT REFERENCES scored_jobs(id),
    pdf_path TEXT, resume_text TEXT,
    validated INTEGER, generated_at TEXT
);

-- Human authorization + execution

CREATE TABLE IF NOT EXISTS authorized_jobs (
    job_id TEXT PRIMARY KEY REFERENCES scored_jobs(id),
    authorized_at TEXT, submitted INTEGER, submitted_at TEXT, submit_url TEXT
);

-- Inbound tracking (MANUAL status tracking — NO email access)

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id TEXT REFERENCES scored_jobs(id),
    contact_email TEXT, ats_url TEXT,
    status TEXT,                          -- applied|screen|interview|final|offer|reject|silent
    status_confidence REAL,               -- how confident the classifier is
    last_updated TEXT,
    applied_at TEXT,                      -- when you submitted
    interview_count INTEGER DEFAULT 0,    -- number of interviews scheduled
    notes TEXT
);

-- ===== 100x ADVANCED LAYER TABLES =====

-- Outcome feedback (the compounding learning engine)

CREATE TABLE IF NOT EXISTS application_outcomes (
    id INTEGER PRIMARY KEY,
    job_id TEXT REFERENCES scored_jobs(id),
    status TEXT,                          -- applied|screen|interview|final|offer|reject|silent
    stage_at TEXT,                        -- where it stopped
    notes TEXT,
    created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS feedback_features (
    id INTEGER PRIMARY KEY,
    outcome_id INTEGER REFERENCES application_outcomes(id),
    skill_match REAL, domain_match REAL, seniority_match REAL,
    country_match REAL, comp_match REAL, resume_rating REAL,
    fraud_score REAL, source_type TEXT, applied_at TEXT,
    got_interview INTEGER                   -- the label the model learns from
);

-- Auto-tuned scoring weights (updated weekly from real outcomes)

CREATE TABLE IF NOT EXISTS scoring_model (
    key TEXT PRIMARY KEY,                  -- e.g. "w_skill", "w_domain", "bias"
    value REAL,
    updated_at TEXT
);

-- Knowledge graph: companies, people, roles, skills, outcomes

CREATE TABLE IF NOT EXISTS kg_nodes (
    id INTEGER PRIMARY KEY,
    kind TEXT,                             -- company|person|role|skill|outcome
    name TEXT, entity_id TEXT,
    payload TEXT,                          -- JSON metadata
    first_seen TEXT
);

CREATE TABLE IF NOT EXISTS kg_edges (
    id INTEGER PRIMARY KEY,
    from_node INTEGER REFERENCES kg_nodes(id),
    to_node INTEGER REFERENCES kg_nodes(id),
    rel TEXT,                              -- works_at|knows|applied_to|similar_to|refers_to
    weight REAL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS referrals (
    id INTEGER PRIMARY KEY,
    company_id INTEGER REFERENCES kg_nodes(id),
    contact_id INTEGER REFERENCES kg_nodes(id),
    relationship TEXT, path TEXT,          -- e.g. "you -> X -> target contact"
    status TEXT,                           -- identified|drafted|sent|accepted|referral
    notes TEXT
);

-- Interview + conversion engine

CREATE TABLE IF NOT EXISTS interview_prep (
    id INTEGER PRIMARY KEY,
    job_id TEXT REFERENCES scored_jobs(id),
    likely_questions TEXT, tech_questions TEXT,
    questions_to_ask TEXT, company_intel TEXT,
    mock_log TEXT,                         -- mock-interview transcript + grade
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS negotiations (
    id INTEGER PRIMARY KEY,
    job_id TEXT REFERENCES scored_jobs(id),
    offer_amount TEXT, benchmark TEXT,
    strategy TEXT, status TEXT, notes TEXT
);

-- Local security: encrypted-file key + backup + audit log

CREATE TABLE IF NOT EXISTS security_audit (
    id INTEGER PRIMARY KEY,
    event TEXT,                           -- backup|decrypt_attempt|weight_change|prompt_change
    detail TEXT, timestamp TEXT
);

-- Versioned prompt library (self-improvement loop)

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY,
    task TEXT,                            -- e.g. 'jd_distiller', 'resume_rewrite'
    version INTEGER, prompt TEXT,
    outcome REAL,                         -- avg interview rate for this prompt
    updated_at TEXT
);
