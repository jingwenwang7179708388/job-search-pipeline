# Plan: 3-day build (DSCI 551)

Source of truth for direction: `docs/Job_Aggregation_Proposal_v3.docx`. This file tracks the
3-day execution plan and its progress; update the **Progress** section after every step.

## Goal

A MySQL-centered job recommender for new-grad Data/ML/AI candidates who need H-1B sponsorship:

LCA data → company visa evidence (MySQL) → fetch postings from ATS → job lifecycle
(new / updated / closed) → feature extraction → rule-based scoring against a user profile →
Top 10 → Streamlit UI + user feedback → DSCI 551 SQL and index experiments.

The old CSV pipeline (`build_companies → resolve_slugs → job_feed → jobs_*.csv`) is kept in
`legacy/` and is no longer extended.

## Decisions

- Database: MySQL 9.6 at `/usr/local/mysql`, port 3306, DB `job_agg`, user `job_agg_user`,
  credentials in `.env`. Homebrew `mysql@8.4` is unused (stopped, not uninstalled).
- No Spark: pandas + Parquet.
- Python: Homebrew `python3` (3.14); dependencies in `requirements.txt`.
- Employer universe comes from MySQL: `companies` holds every company with evidence.
  `company_role_evidence.non_senior_filing_count` is the primary evidence for ranking.
  `sponsors.csv` and `build_companies.py` are legacy.
- Tracks: `DATA_ANALYTICS` / `DATA_SCIENCE` / `ML_AI` / `OTHER` (keyword rules);
  "applied scientist" counts as `ML_AI`.
- One company split across legal entities (Amazon/AWS, Meta/Facebook): not handled in these
  3 days; listed as a known limitation.
- No LLM calls in these 3 days: keywords and regex only.
- Code, comments, SQL comments and logs in English.

Priority: DB correctness > data in the DB > Top 10 > feedback saved > 551 experiments > UI polish.

## Day 1 - database and data pipeline

1. MySQL + `job_agg` + 9 tables (`sql/schema.sql`).
2. `lca/process_lca.py` → `companies`, `company_role_evidence`; add `non_senior_filing_count`.
3. Load the 277 companies in `data/companies.csv` into `ats_sources` (ATS type, slug/URL),
   matched to `companies` by normalized name; list the ones that don't match.
4. `ingestion/fetch_jobs.py`: fetch 5-10 Greenhouse + 5-10 Lever companies into `jobs`.
   - New posting → INSERT, `first_seen_at = last_seen_at = NOW()`, `processing_status = pending`.
   - Existing → update `last_seen_at`; never overwrite `first_seen_at`.
   - `content_hash` over cleaned title + location + JD (HTML, extra whitespace and dates
     removed); hash changed → update content, `processing_status = pending`.
   - Only when this run fetched the company successfully *and* got postings back, missing
     postings get `missing_run_count + 1`; failed or empty responses don't count (Workday
     returns an empty 200 when rate-limited). Postings that appear reset to 0; ≥ 3 → closed,
     set `closed_at`.
   - One `ingestion_runs` row per fetch.
5. Check: counts of `companies` / `company_role_evidence` / `jobs`; `jobs` grouped by status.

## Day 2 - job processing and recommendations

1. `processing/process_jobs.py`, pending only: Track classification (`OTHER` →
   `global_eligible = FALSE`); regex for years of experience, seniority, citizenship /
   clearance / sponsorship restrictions; fixed skill list (Python, SQL, R as a whole word,
   Tableau, Power BI, Spark, AWS, PyTorch, TensorFlow, Machine Learning); visa evidence level
   Likely (same-Track non-senior filings ≥ 5 and no JD restriction) / Unclear (1-4) /
   Unlikely (no filings in the last 3 years) / Blocked (JD restriction).
2. Test user: DATA_SCIENCE + ML_AI; Python / SQL / Spark / PyTorch; 1 year; CA / NY /
   Remote; needs sponsorship.
3. `recommendation/rank_jobs.py`: 48-hour window (fall back to 30 days, UI shows "Demo time
   window: 30 days / Production target: 48 hours"); Track 25 / skills 25 / experience 15 /
   location 10 / freshness 10 / visa 15; at most 2 per company; Top 10 into `recommendations`.
4. `app/streamlit_app.py`: database overview / Top 10 cards / feedback buttons writing
   `user_interactions` / SQL experiment page.
5. Check: generate Top 10 in Streamlit → click Saved → the row is in MySQL.

## Day 3 - 551 experiments, tests, cleanup

1. `sql/queries.sql`, 5 queries: recent new jobs; target-Track jobs; jobs the user hasn't
   seen; company LCA evidence; new jobs per ATS.
2. `sql/indexes.sql` + `experiments/index_results.md`: `jobs(first_seen_at)` vs
   `jobs(status, first_seen_at)`; `job_features(role_family, global_eligible, job_id)`;
   EXPLAIN ANALYZE, median of several runs; label any simulated data.
3. End-to-end test: no duplicate inserts; `first_seen_at` not overwritten; Updated detected;
   OTHER and closed never in Top 10; ranks are 1-10; feedback written to the DB.
4. ER diagram, README results, `requirements.txt`, screenshots.

## Notes for upcoming steps

- `sql/schema.sql` vs this plan: `jobs.status` is `ENUM('active','closed')` (the plan says
  "open" - use `active`); `job_features` uses `role_family`, `seniority`, `min_years_experience`,
  `requires_citizenship`, `requires_clearance`, `no_sponsorship`, `visa_evidence`, `skills`.
- Fetch frequency: the MVP fetches every ATS source on the same schedule, so `ats_sources` has
  no frequency column. Tiered fetching (e.g. more often for high-evidence companies) is a
  decided follow-up, not part of the 3-day build.

## Progress

- **2026-10-19 - Day 1 step 1 done.** MySQL 9.6.0 on 3306; `job_agg` (utf8mb4) and
  `job_agg_user` created in Workbench; 9 tables from `sql/schema.sql`; login via `.env`
  verified from the CLI and from Python (`common/db.py`).
- **2026-10-19 - Day 1 step 2 done (except `non_senior_filing_count`).**
  `lca/process_lca.py` over 25 LCA files (FY2020 Q1 - FY2026 Q3): 4,269,415 raw records →
  218,087 in a target Track → 193,597 after CASE_NUMBER de-dup → 178,232 Certified →
  166,351 after dropping staffing/university/hospital employers. `companies` = 22,567,
  `company_role_evidence` = 29,217 (DATA_ANALYTICS 14,499 / DATA_SCIENCE 9,059 /
  ML_AI 5,659 companies).
- **2026-10-19 - Repo reorganized** into `common/ lca/ mapping/ legacy/ sql/ docs/ data/`;
  legacy scripts use script-relative paths; all scripts import cleanly.
