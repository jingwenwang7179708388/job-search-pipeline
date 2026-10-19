#!/usr/bin/env python3
"""
snapshot_corpus.py - save every open posting from every company into a local corpus (corpus.db)

Why this exists:
  job_feed.py fetches 40K+ postings per run, keeps the few hundred that pass the filters and
  discards the rest, and its CSV output has no job-description text. Retrieval and ranking
  experiments need the full corpus, including irrelevant postings: ranking them below the
  relevant ones is exactly what the model has to learn. This script stores that corpus.

Usage:
  python3 snapshot_corpus.py              # fetch every company in companies.csv
  python3 snapshot_corpus.py --limit 5    # first 5 companies only, for a trial run
  python3 snapshot_corpus.py --stats      # no network: print what corpus.db contains

No GROQ_API_KEY needed and no LLM calls. Does not read or write seen_jobs.json / jobs_*.csv.
Run daily and first_seen / last_seen record when each posting appeared and disappeared.
"""
import argparse
import csv
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import job_feed as jf  # reuse the existing per-ATS fetchers

# Paths are relative to this file so the script runs from any working directory
ROOT = Path(__file__).resolve().parent.parent
DB_FILE = ROOT / "legacy" / "outputs" / "corpus.db"
WORKERS = 6  # companies fetched in parallel; requests within one company stay sequential

# The Workday and SmartRecruiters list APIs carry no description; each posting needs its own
# request. job_feed only fetches descriptions for titles matching TITLE_INCLUDE. The corpus
# needs a wider net: every tech/data title gets a description (these are the hard negatives a
# ranker must separate, e.g. Senior, Intern, Data Engineer), while other roles (sales, nursing,
# retail...) keep the title only, which saves tens of thousands of requests.
BROAD_TECH = re.compile(
    r"\b(engineer\w*|scientist|data|machine learning|ml|ai|analyst|analytics|"
    r"research\w*|software|developer|quant\w*|statistic\w*|algorithm\w*|vision|"
    r"nlp|llm|robotics|perception|autonomy)\b", re.I)

strict_title_ok = jf.title_ok  # the original strict filter, kept to tag each posting (title_match)


def broad_title_ok(title):
    return bool(BROAD_TECH.search(title or ""))


# fetch_workday / fetch_smartrecruiters look up title_ok in the job_feed module at call time,
# so swapping it here makes them fetch descriptions under the wider rule without editing
# job_feed.py.
jf.title_ok = broad_title_ok

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    job_id        TEXT PRIMARY KEY,
    company       TEXT,
    ats           TEXT,
    slug          TEXT,
    title         TEXT,
    location      TEXT,
    posted        TEXT,
    url           TEXT,
    description   TEXT,
    is_us         INTEGER,   -- result of job_feed.is_us
    title_match   INTEGER,   -- passes job_feed's original strict title filter
    visa_flag     TEXT,      -- visa red-flag phrases found in the description
    keyword_score INTEGER,   -- job_feed.score keyword score, used later as the baseline
    first_seen    TEXT,
    last_seen     TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    started      TEXT,
    finished     TEXT,
    companies    INTEGER,
    failed       INTEGER,
    postings     INTEGER,
    new_postings INTEGER
);
"""

UPSERT = """
INSERT INTO postings (job_id, company, ats, slug, title, location, posted, url,
                      description, is_us, title_match, visa_flag, keyword_score,
                      first_seen, last_seen)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(job_id) DO UPDATE SET
    title = excluded.title,
    location = excluded.location,
    posted = CASE WHEN excluded.posted != '' THEN excluded.posted ELSE postings.posted END,
    url = excluded.url,
    -- if this run got no description (network hiccup etc.), keep the previous one
    description = CASE WHEN excluded.description != '' THEN excluded.description
                       ELSE postings.description END,
    is_us = excluded.is_us,
    title_match = excluded.title_match,
    visa_flag = excluded.visa_flag,
    keyword_score = excluded.keyword_score,
    last_seen = excluded.last_seen
"""


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect():
    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    return con


def to_row(job, company, ats, slug, stamp):
    title = job.get("title") or ""
    desc = job.get("desc") or ""
    rec = {"title": title, "desc": desc, "posted": job.get("posted") or ""}
    return (
        job["job_id"], company, ats, slug, title, job.get("location") or "",
        rec["posted"], job.get("url") or "", desc,
        int(jf.is_us(job.get("location") or "")),
        int(strict_title_ok(title)),
        jf.visa_flag(title, desc),
        jf.score(rec),
        stamp, stamp,
    )


def fetch_company(c):
    name = c["company"].strip()
    ats = (c.get("ats") or "").strip()
    slug = c["slug"].strip()
    if ats not in jf.FETCHERS:
        return name, ats, slug, None, f"unsupported ATS: {ats}"
    try:
        return name, ats, slug, jf.FETCHERS[ats](slug), None
    except Exception as e:  # one failing company must not stop the others
        return name, ats, slug, None, type(e).__name__


def print_stats(con):
    q = lambda sql: con.execute(sql).fetchone()[0]
    total = q("SELECT COUNT(*) FROM postings")
    if not total:
        print("corpus.db is empty; run python3 snapshot_corpus.py first")
        return
    print(f"Postings               {total}")
    print(f"Companies              {q('SELECT COUNT(DISTINCT company) FROM postings')}")
    print(f"With description       {q('SELECT COUNT(*) FROM postings WHERE length(description) > 200')}")
    print(f"US postings            {q('SELECT COUNT(*) FROM postings WHERE is_us = 1')}")
    print(f"Pass strict title      {q('SELECT COUNT(*) FROM postings WHERE title_match = 1')}")
    print(f"  of which in the US   {q('SELECT COUNT(*) FROM postings WHERE title_match = 1 AND is_us = 1')}")
    print("By ATS:")
    for ats, n, with_desc in con.execute(
            "SELECT ats, COUNT(*), SUM(length(description) > 200) FROM postings "
            "GROUP BY ats ORDER BY 2 DESC"):
        print(f"  {ats:<16}{n:>7} postings, {with_desc or 0:>7} with description")
    last = con.execute("SELECT started, finished, companies, failed, postings, new_postings "
                       "FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    if last:
        print(f"Last run               {last[0]} -> {last[1]} UTC, {last[2]} companies "
              f"({last[3]} failed), {last[4]} postings, {last[5]} new")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", default=str(ROOT / "data" / "companies.csv"))
    ap.add_argument("--limit", type=int, default=None, help="fetch only the first N companies (trial run)")
    ap.add_argument("--stats", action="store_true", help="no network: print corpus.db statistics")
    args = ap.parse_args()

    con = connect()
    if args.stats:
        print_stats(con)
        return

    if not os.path.exists(args.companies):
        sys.exit(f"Cannot find {args.companies}")
    with open(args.companies, encoding="utf-8-sig") as f:
        companies = [r for r in csv.DictReader(f) if (r.get("slug") or "").strip()]
    if args.limit:
        companies = companies[:args.limit]

    started, t0 = now_utc(), time.perf_counter()
    before = con.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    n_postings, failed = 0, []
    print(f"Fetching {len(companies)} companies ({WORKERS} in parallel), writing to {DB_FILE}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch_company, c) for c in companies]
        for i, fut in enumerate(as_completed(futures), 1):
            name, ats, slug, jobs, err = fut.result()
            if err:
                failed.append(f"{name}\t{slug}\t{err}")
                print(f"[{i}/{len(companies)}] {name} ... failed ({err})")
                continue
            stamp = now_utc()
            rows = [to_row(j, name, ats, slug, stamp) for j in jobs if j.get("job_id")]
            con.executemany(UPSERT, rows)
            con.commit()  # commit per company, so Ctrl+C keeps everything fetched so far
            n_postings += len(rows)
            print(f"[{i}/{len(companies)}] {name} ... {len(rows)} postings")

    after = con.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    con.execute("INSERT INTO runs (started, finished, companies, failed, postings, new_postings) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (started, now_utc(), len(companies), len(failed), n_postings, after - before))
    con.commit()

    print(f"\n{'=' * 54}")
    print(f"Took {(time.perf_counter() - t0) / 60:.1f} min, {len(failed)} companies failed")
    for line in failed:
        print(f"  failed: {line}")
    print_stats(con)
    print("=" * 54)


if __name__ == "__main__":
    main()
