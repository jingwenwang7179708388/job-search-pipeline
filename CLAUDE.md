# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A MySQL-centered job recommender for new-grad Data/ML/AI candidates who need H-1B sponsorship,
built as a DSCI 551 course project on top of the owner's personal job-search scripts:

LCA data → company visa evidence (MySQL) → fetch postings from each company's ATS → job
lifecycle (new / updated / closed) → regex feature extraction → rule-based scoring against a
user profile → Top 10 → Streamlit UI + user feedback → 551 SQL and index experiments.

The 3-day plan, decisions and step-by-step progress live in `docs/PLAN.md` - read it first and
update its **Progress** section after finishing each step. Direction comes from
`docs/Job_Aggregation_Proposal_v3.docx`.

## Layout

```
common/          db.py (get_conn() from .env), names.py (clean_name, normalize, BODYSHOPS, TITLE_SKIP)
lca/             process_lca.py - LCA xlsx -> parquet cache -> companies, company_role_evidence
mapping/         resolve_slugs.py, resolve_workday.py - find each company's ATS slug (still used);
                 ats_slug_review.csv (hand-checked slugs) + load_ats_sources.py -> ats_sources
ingestion/       ats_clients.py (fetchers copied from legacy/job_feed.py), fetch_jobs.py -> jobs,
                 ingestion_runs (lifecycle rules in its docstring)
processing/      (planned) process_jobs.py
recommendation/  (planned) rank_jobs.py
app/             (planned) streamlit_app.py
sql/             schema.sql (+ planned queries.sql, indexes.sql)
experiments/     (planned) index_results.md
docs/            PLAN.md, proposal, flowchart; docs/archive/ for old proposals
data/raw/lca/    DOL LCA xlsx files (gitignored)
data/processed/  lca_parquet/ per-file cache, lca_all.parquet (gitignored)
data/            companies.csv (277 ATS-resolved companies), companies_new.csv, sponsors.csv,
                 companies_workday.csv, unresolved_slugs.txt (gitignored)
legacy/          old CSV pipeline: job_feed.py, build_companies.py, discover_slugs.py,
                 snapshot_corpus.py, test_wd.py - kept runnable, not extended
legacy/outputs/  jobs_*.csv, seen_jobs.json, ng_cache.json, unresolved.txt, *.log (gitignored)
```

New code imports only from `common/` (and later `ingestion/ats_clients.py`), never from
`legacy/`. `common/names.py` is a deliberate copy of the rules in `legacy/build_companies.py`.
Scripts are run from the project root as `python3 <dir>/<script>.py`; each script adds the root
to `sys.path` itself and builds file paths from `Path(__file__)`, not the working directory.

## Database

- MySQL **9.6.0**, Oracle installer at `/usr/local/mysql`, port **3306**, LaunchDaemon (starts
  at boot). The owner manages it in MySQL Workbench.
- DB `job_agg` (utf8mb4), user `job_agg_user`@localhost. Connection info in `.env`
  (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`), gitignored; read it via
  `common.db.get_conn()`. Never print or commit the password.
- `sql/schema.sql` creates 9 tables with `IF NOT EXISTS`. Schema changes: update
  `schema.sql` *and* apply an `ALTER TABLE` to the live DB.
- Homebrew `mysql@8.4` is installed but unused and stopped (its `/opt/homebrew/etc/my.cnf`
  was moved to port 3307). Don't start or uninstall it without asking.

## Running

```
python3 -m pip install -r requirements.txt
python3 lca/process_lca.py                          # first run reads 25 xlsx (~10 min), then cached
python3 mapping/resolve_slugs.py data/companies_new.csv   # only when the company list grows
python3 mapping/resolve_workday.py 200
python3 mapping/load_ats_sources.py                 # data/companies.csv + review -> ats_sources
python3 ingestion/fetch_jobs.py --ats greenhouse lever --per-ats 8   # or --source-ids / --all-active
# planned: processing/process_jobs.py -> recommendation/rank_jobs.py
#          -> streamlit run app/streamlit_app.py
```

Legacy pipeline (still runnable): `python3 legacy/build_companies.py data/raw/lca/*.xlsx`,
then `python3 legacy/job_feed.py` (needs `GROQ_API_KEY`).

## Decisions (see docs/PLAN.md for the full list)

- No Spark - pandas + Parquet. No LLM calls in the 3-day build - keywords and regex only.
- Tracks are defined in `docs/TRACKS.md` (10 Tracks + `OTHER`); that file is the source of
  truth. The rules are the frozen v5 (`experiments/track_rules_v5.py`), copied into
  `common/tracks.py` (`classify_title(title, soc_code=None)`, no pandas);
  `experiments/check_common_tracks.py` checks the copy is identical. Blind-label precision on
  the same sample: v4 71% -> v5 85%. Change rules by making a new experiment version, not by
  editing v4/v5 in place.
- `lca/process_lca.py` classifies every cleaned filing with `common.tracks.classify_title`
  (title + SOC) and writes all 10 Tracks (incl. SOFTWARE_ENGINEERING) to
  `company_role_evidence`; OTHER is never written. Seniority (`TITLE_SKIP`, with "member of
  technical staff" not counted as senior) is separate from the Track. The `track` /
  `role_family` ENUMs in `sql/schema.sql` hold the 11 `TRACKS` values. `fetch_jobs.py
  --per-ats` ranks sources by non-senior filings in `RANK_TRACKS` (DS + ML_ENGINEERING +
  ML_RESEARCH), overridable with `--tracks`.
- `companies` is insert-only: a company whose filings all became OTHER keeps its row but has
  no evidence (783 after the switch to v5, incl. 2 with ATS sources: Pickle Robot, Omni
  Instrument - SOC 17- filings). Don't delete them: `ats_sources` cascades.
- ATS slugs guessed by `mapping/resolve_*.py` are wrong ~8% of the time (generic names like
  apex/general/insurance hit other companies' boards). Check each new slug against the
  board's own company name and record the outcome in `mapping/ats_slug_review.csv`; matching
  a single word of the name is not enough.
- The employer universe is MySQL `companies` (every company with evidence);
  `non_senior_filing_count` is the primary visa evidence for ranking.
- One company split across legal entities (Amazon/AWS, Meta/Facebook) is a known, accepted
  limitation for now.
- Everything in the repo is English: code, comments, SQL comments, logs, docs. Talk to the
  owner in Chinese.

## Gotchas

- `mysql -u root` connects to the 9.6 **root** account (needs its password), not
  `job_agg_user`. Use `.env` credentials.
- Packages are installed with `pip install --user` into Homebrew Python 3.14; `python3 -I`
  disables user site-packages and then nothing imports. Use `-E -P` if you need isolation -
  but note `-P` also drops the script's own directory from `sys.path`, which breaks
  `legacy/snapshot_corpus.py`'s `import job_feed`.
- Workday rate-limits by returning HTTP 200 with an empty body; treat that as a failed fetch,
  never as "no postings" (matters for `missing_run_count` in ingestion).
- DOL quarterly LCA files are inconsistent: most are single-quarter, but some (FY2021 Q2/Q3,
  FY2023 Q2, FY2026 Q3) are cumulative. Always de-duplicate by `CASE_NUMBER`, keeping the
  latest quarter.
- pandas 3: `astype(str)` keeps missing values as NA instead of the string "nan"; call
  `fillna("")` before string matching.
- Company name → ATS slug matching is heuristic; `mapping/resolve_slugs.py`,
  `mapping/resolve_workday.py` and `common/names.py` each have their own legal-suffix regex.
