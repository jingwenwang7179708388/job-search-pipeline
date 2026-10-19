-- schema.sql - tables for the job_agg database
-- Uses IF NOT EXISTS: rerunning raises no error and never drops existing data

-- 1. Companies (from LCA data)
CREATE TABLE IF NOT EXISTS companies (
    company_id      INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    company_name    VARCHAR(255) NOT NULL,              -- raw LCA employer name
    normalized_name VARCHAR(255) NOT NULL,              -- cleaned name, used for de-duplication (every company with H-1B filings in a target track)
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_companies_normalized (normalized_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- 2. LCA evidence per company per Track
CREATE TABLE IF NOT EXISTS company_role_evidence (
    evidence_id     INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    company_id      INT UNSIGNED NOT NULL,
    track           ENUM('DATA_ANALYTICS','DATA_SCIENCE','ML_AI') NOT NULL,
    filing_count    INT UNSIGNED NOT NULL,
    latest_year     SMALLINT UNSIGNED NOT NULL,         -- fiscal year of the most recent filing
    states          VARCHAR(500),                       -- comma-separated state codes
    average_wage    DECIMAL(12,2),                      -- converted to annual salary (average over this company's filings in this track)
    UNIQUE KEY uq_evidence_company_track (company_id, track),
    FOREIGN KEY (company_id) REFERENCES companies(company_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. Applicant tracking system (ATS) per company: where to fetch its postings, i.e. which ATS it uses and its account name (slug) there
CREATE TABLE IF NOT EXISTS ats_sources (
    ats_source_id   INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    company_id      INT UNSIGNED NOT NULL,
    ats_type        VARCHAR(30) NOT NULL,               -- greenhouse / lever / ...
    slug            VARCHAR(255) NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_success_at DATETIME,
    UNIQUE KEY uq_ats_type_slug (ats_type, slug),
    FOREIGN KEY (company_id) REFERENCES companies(company_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. Ingestion log: one row per fetch from a source - start/end time, success or failure, how many postings came back and how many were new/updated/closed
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    ats_source_id   INT UNSIGNED NOT NULL,
    started_at      DATETIME NOT NULL,
    finished_at     DATETIME,
    status          ENUM('running','success','failed') NOT NULL DEFAULT 'running',
    jobs_returned   INT UNSIGNED DEFAULT 0,
    jobs_new        INT UNSIGNED DEFAULT 0,
    jobs_updated    INT UNSIGNED DEFAULT 0,
    jobs_closed     INT UNSIGNED DEFAULT 0,
    error_message   TEXT,
    FOREIGN KEY (ats_source_id) REFERENCES ats_sources(ats_source_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. Jobs: one row per fetched posting - its content plus its lifecycle (first seen, last seen, closed or not, processed or not)
CREATE TABLE IF NOT EXISTS jobs (
    job_id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    company_id        INT UNSIGNED NOT NULL,
    ats_source_id     INT UNSIGNED NOT NULL,
    external_job_id   VARCHAR(255) NOT NULL,            -- the ATS's own posting ID
    title             VARCHAR(500) NOT NULL,
    description_text  MEDIUMTEXT,                       -- JD with HTML stripped
    location_text     VARCHAR(500),
    job_url           VARCHAR(1000),
    content_hash      CHAR(64) NOT NULL,                -- SHA-256(title + location + JD)
    status            ENUM('active','closed') NOT NULL DEFAULT 'active',
    first_seen_at     DATETIME NOT NULL,                -- written on first insert only, never updated
    last_seen_at      DATETIME NOT NULL,
    content_updated_at DATETIME,                        -- when the content (content_hash) last changed
    closed_at         DATETIME,
    missing_run_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
    processing_status ENUM('pending','processed','failed') NOT NULL DEFAULT 'pending',
    UNIQUE KEY uq_jobs_source_external (ats_source_id, external_job_id),
    FOREIGN KEY (company_id)    REFERENCES companies(company_id),
    FOREIGN KEY (ats_source_id) REFERENCES ats_sources(ats_source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6. Job features (written by processing/process_jobs.py): structured fields extracted from the JD - track, seniority, years of experience, skills, visa restrictions
CREATE TABLE IF NOT EXISTS job_features (
    job_id               BIGINT UNSIGNED PRIMARY KEY,
    role_family          ENUM('DATA_ANALYTICS','DATA_SCIENCE','ML_AI','OTHER') NOT NULL,
    seniority            ENUM('intern','entry','mid','senior','unknown') NOT NULL DEFAULT 'unknown',
    min_years_experience TINYINT UNSIGNED,              -- NULL when not found
    requires_citizenship BOOLEAN NOT NULL DEFAULT FALSE,
    requires_clearance   BOOLEAN NOT NULL DEFAULT FALSE,
    no_sponsorship       BOOLEAN NOT NULL DEFAULT FALSE,
    visa_evidence        ENUM('Likely','Unclear','Unlikely','Blocked') NOT NULL DEFAULT 'Unclear',
    skills               JSON,                          -- e.g. ["python","sql"]
    global_eligible      BOOLEAN NOT NULL DEFAULT TRUE, -- FALSE on citizenship/clearance restrictions
    processed_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7. User profiles
CREATE TABLE IF NOT EXISTS users (
    user_id             INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    name                VARCHAR(100) NOT NULL,
    target_tracks       JSON NOT NULL,                  -- e.g. ["DATA_SCIENCE","ML_AI"]
    skills              JSON NOT NULL,
    years_experience    TINYINT UNSIGNED NOT NULL DEFAULT 0,
    preferred_locations JSON NOT NULL,                  -- e.g. ["CA","NY","Remote"]
    needs_sponsorship   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 8. Recommendations (one batch of Top 10 per generation)
CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id           INT UNSIGNED NOT NULL,
    job_id            BIGINT UNSIGNED NOT NULL,
    batch_id          VARCHAR(40) NOT NULL,             -- shared by the 10 rows of one generation
    rank_position     TINYINT UNSIGNED NOT NULL,
    total_score       DECIMAL(5,2) NOT NULL,
    score_breakdown   JSON,                             -- per-component scores, shown in the UI as the explanation
    generated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_rec_batch_rank (batch_id, rank_position),
    UNIQUE KEY uq_rec_batch_job  (batch_id, job_id),
    CHECK (rank_position BETWEEN 1 AND 10),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (job_id)  REFERENCES jobs(job_id)   ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 9. User feedback
CREATE TABLE IF NOT EXISTS user_interactions (
    interaction_id    BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id           INT UNSIGNED NOT NULL,
    job_id            BIGINT UNSIGNED NOT NULL,
    recommendation_id BIGINT UNSIGNED,                  -- set when the action came from a recommendation card
    action            ENUM('viewed','saved','applied','dismissed') NOT NULL,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)           REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (job_id)            REFERENCES jobs(job_id)   ON DELETE CASCADE,
    FOREIGN KEY (recommendation_id) REFERENCES recommendations(recommendation_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;