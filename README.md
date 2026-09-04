# Job Search Pipeline

An automated pipeline for discovering companies with a public hiring-signal track record for a
target role profile, resolving each employer to its applicant tracking system, and surfacing new
matching postings daily.

## What it does

1. **`build_companies.py`** — Parses public DOL labor market disclosure filings, filters to
   certified petitions for DS/MLE/AI-type titles, strips staffing agencies, universities, and
   hospitals, and aggregates by normalized employer name.
2. **`resolve_slugs.py`** — Probes each candidate employer name against the Greenhouse, Lever,
   Ashby, SmartRecruiters, and Recruitee APIs to find its real ATS slug.
3. **`resolve_workday.py`** — Second-pass resolver for Workday-hosted career sites, which require
   brute-forcing a `tenant | wd-number | site` triple rather than a documented public API.
4. **`job_feed.py`** — The daily driver. Fetches current openings from every resolved employer,
   filters by title/seniority/location, and sends borderline postings to an LLM (Groq API) to
   classify whether a role is genuinely new-grad-eligible. Tracks previously-seen postings so
   repeat runs only surface what's new.

## Results

- 240K+ government disclosure records processed
- 1,175 target employers identified
- 237 of those resolved to a scrapable ATS, covering 41,000+ live postings
- ~250 roles surfaced after filtering — the daily shortlist

## Stack

Python, pandas, multithreaded `requests`, Groq API for LLM-based classification.

## Setup

```bash
pip install requests pandas openpyxl
export GROQ_API_KEY=...   # free tier at console.groq.com/keys

python3 build_companies.py LCA_Disclosure_Data_FY2025.xlsx
python3 resolve_slugs.py companies_new.csv
python3 resolve_workday.py 200
python3 job_feed.py
```

Company target lists and scraped job data are excluded from this repo (see `.gitignore`) since
they reflect one person's specific target list rather than reusable code.
