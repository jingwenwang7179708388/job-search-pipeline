# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal job-hunting pipeline for the owner's own DS/MLE/AI job search in the US, with H-1B
visa sponsorship as a hard constraint. It is not a general-purpose tool — the filtering logic
(title keywords, seniority exclusions, visa red-flag phrases, US location hints) is tuned to one
person's profile and lives as module-level constants at the top of `job_feed.py` and
`build_companies.py`. When asked to adjust "what jobs show up," edit those constant lists rather
than the surrounding logic.

## Pipeline (run in this order)

1. **`build_companies.py`** — Input: DOL H-1B LCA disclosure Excel/CSV file(s) (downloaded
   manually, not stored in repo). Filters to CERTIFIED, non-withdrawn petitions for DS/MLE-type
   titles, strips staffing/consulting body shops and universities/hospitals, aggregates by
   normalized employer name. Outputs `sponsors.csv` (full ranked list) and `companies_new.csv`
   (feed for the next step).
   ```
   pip install pandas openpyxl
   python3 build_companies.py LCA_Disclosure_Data_FY2025.xlsx [more_files.xlsx ...]
   ```

2. **`resolve_slugs.py`** — Takes a companies CSV (e.g. `companies_new.csv`) and brute-force
   probes candidate name variants against Greenhouse/Lever/Ashby/SmartRecruiters/Recruitee APIs
   to find each company's real ATS slug. Writes resolved rows to `companies.csv` and unresolved
   company names to `unresolved.txt`.
   ```
   python3 resolve_slugs.py companies_new.csv
   ```

3. **`resolve_workday.py`** — Second pass for companies that didn't resolve in step 2, since
   Workday requires guessing a `tenant|wd-number|site-name` triple by brute-force POST rather than
   a documented public API. Reads `sponsors.csv`, skips companies already in `companies.csv`,
   writes hits to `companies_workday.csv` (same schema as `companies.csv`, `slug` is
   pipe-delimited `tenant|wd1/wd3/wd5|site`). Takes how many top-ranked (by sponsor count)
   unresolved companies to attempt:
   ```
   python3 resolve_workday.py 200
   ```
   `companies_workday.csv` is a separate output — merge it into `companies.csv` manually if you
   want `job_feed.py` to pick it up (see Architecture note below).

4. **`job_feed.py`** — The daily driver. Reads `companies.csv`, fetches current openings from
   each company's ATS, filters by title/location, flags visa red flags in the job description,
   scores and ranks, and writes `jobs_YYYY-MM-DD.csv`. Tracks previously-seen job IDs in
   `seen_jobs.json` so default runs only surface new postings. Every job that passes the
   title/location filter also gets sent to the Groq API (free tier) for New Grad
   classification (`classify_new_grad()`) — keyword matching alone isn't reliable for "does this
   JD actually require years of professional experience," so that judgment call is delegated to
   an LLM. Requires `GROQ_API_KEY` to be set; the script exits immediately with a clear message
   if it isn't. Results are cached to `ng_cache.json` keyed by `job_id`, so a job already
   classified is never re-sent to the LLM on a later run. Failed classifications (reason
   starts with `classification failed`) are deliberately NOT cached and NOT added to `seen_jobs.json`, so
   they retry on the next run; `check_groq()` aborts the whole run up front on a bad key (401)
   or a retired model (404) instead of emitting a CSV full of failures. The LLM gets a
   `NG_DESC_CHARS` (1500) character excerpt built by `ng_excerpt()` — the JD's opening plus
   windows around every years/degree/graduation mention — not the first 1500 characters,
   which are almost always company boilerplate with no requirements in them. The free tier's
   binding limit is tokens per minute, not requests, hence the low `NG_RATE_LIMIT_RPM`; a
   full run takes tens of minutes. The candidate's graduation term/degree (`GRAD_TERM`,
   `GRAD_DEGREE`) and the model used (`GROQ_MODEL`) are constants at the top of the file, same
   spirit as the other profile constants — edit those, not the classification logic, when the
   situation changes (e.g. next semester, or switching models if the free tier's rate limits or
   accuracy become a problem). Pass `--limit N` to cap how many jobs get sent to the LLM in a
   single run, for cheaply checking classification quality/latency before a full run.
   ```
   python3 job_feed.py            # only jobs not seen in a previous run
   python3 job_feed.py --all      # all currently open matching jobs, ignores/doesn't update seen_jobs.json
   python3 job_feed.py --companies companies_workday.csv   # run against a different company list
   ```

`test_wd.py` is a scratch script for manually probing a single known Workday tenant when
debugging `resolve_workday.py` — not part of the pipeline, run ad hoc.

## Architecture notes

- **ATS fetchers are keyed by name in a dict** (`FETCHERS` in `job_feed.py`, `PROBES` in
  `resolve_slugs.py`). Adding support for a new ATS means writing a `fetch_<name>` /
  `try_<name>` function with the same signature and registering it in that dict — the rest of
  the pipeline is ATS-agnostic downstream of the fetcher.
- **`job_feed.py` does not natively support Workday.** `companies_workday.csv` uses `ats=workday`
  but there is no `fetch_workday` in `FETCHERS`, so those rows are currently silently skipped
  (`FETCHERS[ats] if ats in FETCHERS else []`). Anyone finishing the Workday integration needs to
  add a fetcher that splits the pipe-delimited `slug` (`tenant|wd_number|site`) and hits
  `https://{tenant}.{wd_number}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` — the working
  POST shape is already proven out in `resolve_workday.py`'s `probe()` and `test_wd.py`.
- **`TITLE_EXCLUDE` matches whole words** (`_word_hit`), so entries are bare tokens (`"ii"`,
  `"sr"`, `"intern"`) — don't add padding spaces to fake a boundary. Interns/co-ops are
  excluded there: the owner wants new-grad full-time roles only.
- **Company name → slug matching is heuristic**, not authoritative. `clean_name()` /
  `candidates()` / `tenants()` in the different scripts each reimplement similar (but not
  identical) legal-suffix-stripping regexes independently — don't assume changing one keeps the
  others in sync.
- **State lives in flat CSV/JSON files, not a database**: `sponsors.csv` and `companies*.csv` are
  durable, hand-curated-ish company lists; `seen_jobs.json` and `jobs_YYYY-MM-DD.csv` are
  generated/dated outputs from `job_feed.py` runs. `unresolved.txt` is overwritten each run of
  `resolve_slugs.py` / `job_feed.py`, not appended.
- Everything in this repo is in English: code, comments, docstrings, console output and the LLM
  prompt. Do not add Chinese text anywhere when editing.

## Dependencies

No `requirements.txt`. Needed packages: `requests` (all scripts, including calling the Groq API
in `job_feed.py` — no separate SDK needed), `pandas` + `openpyxl` (`build_companies.py` only, for
reading the DOL Excel export). `job_feed.py`'s New Grad classification via Groq's free tier also
needs the `GROQ_API_KEY` env var set, get one free at console.groq.com/keys.
