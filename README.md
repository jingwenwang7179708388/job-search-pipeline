# H-1B-Aware Job Recommender

New-grad Data / ML / AI candidates who need H-1B sponsorship waste most of their search on
postings from companies that never sponsor, and find good postings days after they open. This
project recommends freshly posted jobs and attaches visa evidence to each one: how many H-1B
filings the company has made for that kind of role, from government data.

It is also a DSCI 551 course project, so MySQL is the core: every stage reads from and writes to
the `job_agg` database.

## Data sources

- **DOL LCA disclosure data** (H-1B Labor Condition Applications), FY2020 Q1 - FY2026 Q3, 25
  quarterly xlsx files from the U.S. Department of Labor. Some files are cumulative, so records
  are de-duplicated by `CASE_NUMBER`.
- **Company applicant tracking systems (ATS)**: public job-board APIs of Greenhouse, Lever,
  Ashby, SmartRecruiters, Recruitee, Workable and Workday.

## Pipeline

```
LCA xlsx ─► lca/process_lca.py ─► companies, company_role_evidence
                                          │
data/companies.csv (ATS slugs) ─► ats_sources
                                          │
ATS APIs ─► ingestion/fetch_jobs.py ─► jobs (+ ingestion_runs)   new / updated / closed
                                          │
            processing/process_jobs.py ─► job_features           track, seniority, skills, visa flags
                                          │
            recommendation/rank_jobs.py ─► recommendations       Top 10 per user
                                          │
            app/streamlit_app.py ─► user_interactions            viewed / saved / applied / dismissed
```

See `docs/flowchart_v3.html` for the full diagram and `docs/PLAN.md` for status.
Stages after `process_lca.py` are being built.

## Setup

1. MySQL 9.x running locally. Create the database and a user, then the tables:
   ```sql
   CREATE DATABASE job_agg CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
   CREATE USER 'job_agg_user'@'localhost' IDENTIFIED BY '...';
   GRANT ALL PRIVILEGES ON job_agg.* TO 'job_agg_user'@'localhost';
   ```
   ```bash
   mysql -h 127.0.0.1 -u job_agg_user -p job_agg < sql/schema.sql
   ```
2. `.env` in the project root (never committed):
   ```
   DB_HOST=127.0.0.1
   DB_PORT=3306
   DB_USER=job_agg_user
   DB_PASSWORD=...
   DB_NAME=job_agg
   ```
3. Python 3.14: `python3 -m pip install -r requirements.txt`
4. Put the DOL LCA xlsx files in `data/raw/lca/`.

## Running

```bash
python3 lca/process_lca.py        # LCA -> companies + company_role_evidence (first run ~10 min, then cached)
python3 mapping/resolve_slugs.py data/companies_new.csv   # find ATS slugs (when the list grows)
python3 mapping/resolve_workday.py 200                    # second pass for Workday
python3 mapping/load_ats_sources.py      # resolved slugs + mapping/ats_slug_review.csv -> ats_sources
python3 ingestion/fetch_jobs.py --ats greenhouse lever --per-ats 8   # postings -> jobs, ingestion_runs
```

The next stages (`processing/`, `recommendation/`, `app/`) will be added here as they are built.

## Current numbers

| | |
|---|---|
| LCA records read | 4,269,415 (25 files) |
| Certified filings after cleaning (de-dup, Certified, no staffing/university/hospital) | 2,716,527 |
| ... in one of the 10 Tracks (not OTHER) | 1,392,266 (SOFTWARE_ENGINEERING 1,017,907) |
| Companies with evidence | 61,697 (`companies` table: 62,480 rows) |
| Company × Track evidence rows | 109,168 |
| Guessed ATS slugs (`data/companies.csv`) | 277, of which 23 (8.3%) point to another company |
| `ats_sources` rows | 251 (243 active) |
| Jobs ingested so far | 2,518 from 15 Greenhouse/Lever sources |

## DSCI 551 experiments

Queries (`sql/queries.sql`), index experiments (`sql/indexes.sql`) and results
(`experiments/index_results.md`): _to be added on day 3._

## Known limitations

- One company can appear as several legal entities (Amazon.com Services vs. Amazon Web
  Services, Meta Platforms vs. Facebook); name normalization does not merge them.
- Normalized names are rule-based and can look odd (e.g. `JPMORGAN CHASE &`); the UI shows the
  raw name.
- Only 277 of the 22,567 companies are resolved to an ATS; large companies with in-house career
  sites (Google, Meta, Amazon, Apple...) are not covered.
- Track classification, seniority and visa restrictions use keyword / regex rules, no LLM.

## Legacy

`legacy/` holds the original personal pipeline (`build_companies.py` → `mapping/resolve_*.py` →
`job_feed.py` → daily `jobs_*.csv`, with Groq-based new-grad classification). It still runs but
is no longer extended.
