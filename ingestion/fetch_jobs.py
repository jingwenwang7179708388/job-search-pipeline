#!/usr/bin/env python3
"""
ingestion/fetch_jobs.py - fetch open postings from ats_sources into jobs, tracking each job's lifecycle

Per source, one ingestion_runs row, and these rules:
  - New posting (no row for this ats_source_id + external_job_id): INSERT with
    first_seen_at = last_seen_at = run time, status = active, processing_status = pending.
  - Existing posting: last_seen_at = run time, missing_run_count = 0 (first_seen_at is never
    touched). A closed posting that reappears is reopened (status active, closed_at NULL).
  - content_hash = SHA-256 of cleaned title + location + JD (HTML, extra whitespace and dates
    removed). A changed hash updates the content, sets content_updated_at and puts the job
    back to processing_status = pending.
  - Only when this source's fetch succeeded AND returned postings, active postings that did
    not appear get missing_run_count + 1; at MISSING_TO_CLOSE they become closed with
    closed_at = run time. A failed fetch (incl. Workday rate limiting) or an empty response
    changes no missing counts.

Usage (from the project root):
  python3 ingestion/fetch_jobs.py --ats greenhouse lever --per-ats 8
      top 8 active sources per ATS, ranked by the company's non-senior DS + ML_AI filings
  python3 ingestion/fetch_jobs.py --source-ids 12 40 77
  python3 ingestion/fetch_jobs.py --all-active
"""

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common.db import get_conn                       # noqa: E402
from ingestion.ats_clients import FETCHERS           # noqa: E402

MISSING_TO_CLOSE = 3

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_PATTERNS = [
    r"\b\d{4}-\d{1,2}-\d{1,2}(?:[t ][\d:.]+z?)?\b",            # 2026-10-19, ISO timestamps
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",                            # 10/19/2026
    rf"\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",  # October 19, 2026
    rf"\b\d{{1,2}}\s+{_MONTH}\s+\d{{4}}\b",                    # 19 October 2026
    r"\b(?:posted\s+)?\d+\+?\s+(?:minute|hour|day|week|month)s?\s+ago\b",
    r"\bposted\s+(?:today|yesterday)\b",
]
_DATE_RE = re.compile("|".join(_DATE_PATTERNS), re.I)


def clean_for_hash(text):
    text = re.sub(r"<[^>]+>", " ", text or "")       # fetchers strip HTML already; be safe
    text = _DATE_RE.sub(" ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def content_hash(job):
    parts = (clean_for_hash(job["title"]), clean_for_hash(job["location"]),
             clean_for_hash(job["desc"]))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def pick_sources(cur, args):
    base = ("SELECT s.ats_source_id, s.company_id, s.ats_type, s.slug, c.company_name "
            "FROM ats_sources s JOIN companies c USING (company_id) ")
    if args.source_ids:
        cur.execute(base + "WHERE s.ats_source_id IN (%s)" % ",".join(["%s"] * len(args.source_ids)),
                    args.source_ids)
        return cur.fetchall()
    if args.all_active:
        cur.execute(base + "WHERE s.is_active ORDER BY s.ats_source_id")
        return cur.fetchall()
    out = []
    for ats in args.ats:
        cur.execute(
            base + "LEFT JOIN company_role_evidence e ON e.company_id = s.company_id "
            "AND e.track IN ('DATA_SCIENCE', 'ML_AI') "
            "WHERE s.is_active AND s.ats_type = %s "
            "GROUP BY s.ats_source_id ORDER BY COALESCE(SUM(e.non_senior_filing_count), 0) DESC, "
            "s.ats_source_id LIMIT %s", (ats, args.per_ats))
        out.extend(cur.fetchall())
    return out


def ingest_source(conn, src):
    source_id, company_id, ats, slug, name = src
    cur = conn.cursor()
    cur.execute("SELECT NOW()")
    now = cur.fetchone()[0]
    cur.execute("INSERT INTO ingestion_runs (ats_source_id, started_at, status) "
                "VALUES (%s, %s, 'running')", (source_id, now))
    run_id = cur.lastrowid
    conn.commit()

    try:
        fetched = FETCHERS[ats](slug)
    except Exception as e:
        cur.execute("UPDATE ingestion_runs SET finished_at = NOW(), status = 'failed', "
                    "error_message = %s WHERE run_id = %s",
                    (f"{type(e).__name__}: {e}"[:2000], run_id))
        conn.commit()
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"[:120]}

    try:
        return _apply(conn, cur, run_id, now, source_id, company_id, fetched)
    except Exception as e:
        # Never leave a run stuck in 'running': undo this source's writes and log the failure
        conn.rollback()
        cur.execute("UPDATE ingestion_runs SET finished_at = NOW(), status = 'failed', "
                    "error_message = %s WHERE run_id = %s",
                    (f"db error, rolled back: {type(e).__name__}: {e}"[:2000], run_id))
        conn.commit()
        raise


def _apply(conn, cur, run_id, now, source_id, company_id, fetched):
    # One row per external ID (some APIs repeat postings across pages)
    jobs = {}
    for j in fetched:
        if j["external_id"] and j["external_id"] != "None":
            jobs.setdefault(j["external_id"][:255], j)

    cur.execute("SELECT job_id, external_job_id, content_hash, status FROM jobs "
                "WHERE ats_source_id = %s", (source_id,))
    existing = {ext: (jid, h, st) for jid, ext, h, st in cur.fetchall()}

    n_new = n_updated = n_reopened = n_closed = 0
    for ext, j in jobs.items():
        h = content_hash(j)
        title, loc, url = j["title"][:500], (j["location"] or "")[:500], (j["url"] or "")[:1000]
        if ext not in existing:
            cur.execute(
                "INSERT INTO jobs (company_id, ats_source_id, external_job_id, title, "
                "description_text, location_text, job_url, content_hash, status, first_seen_at, "
                "last_seen_at, content_updated_at, missing_run_count, processing_status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, 0, 'pending')",
                (company_id, source_id, ext, title, j["desc"], loc, url, h, now, now, now))
            n_new += 1
            continue
        job_id, old_hash, status = existing[ext]
        cur.execute("UPDATE jobs SET last_seen_at = %s, missing_run_count = 0, "
                    "status = 'active', closed_at = NULL WHERE job_id = %s", (now, job_id))
        n_reopened += status == "closed"
        if h != old_hash:
            cur.execute("UPDATE jobs SET title = %s, description_text = %s, location_text = %s, "
                        "job_url = %s, content_hash = %s, content_updated_at = %s, "
                        "processing_status = 'pending' WHERE job_id = %s",
                        (title, j["desc"], loc, url, h, now, job_id))
            n_updated += 1

    note = None
    if jobs:
        missing = [jid for ext, (jid, _, st) in existing.items()
                   if st == "active" and ext not in jobs]
        for jid in missing:
            cur.execute("UPDATE jobs SET missing_run_count = missing_run_count + 1 "
                        "WHERE job_id = %s", (jid,))
        if missing:
            cur.execute("UPDATE jobs SET status = 'closed', closed_at = %s "
                        "WHERE ats_source_id = %s AND status = 'active' "
                        "AND missing_run_count >= %s", (now, source_id, MISSING_TO_CLOSE))
            n_closed = cur.rowcount
    else:
        note = "empty response: missing counts not updated"

    cur.execute("UPDATE ats_sources SET last_success_at = %s WHERE ats_source_id = %s",
                (now, source_id))
    cur.execute("UPDATE ingestion_runs SET finished_at = NOW(), status = 'success', "
                "jobs_returned = %s, jobs_new = %s, jobs_updated = %s, jobs_closed = %s, "
                "error_message = %s WHERE run_id = %s",
                (len(jobs), n_new, n_updated, n_closed, note, run_id))
    conn.commit()
    return {"status": "success", "returned": len(jobs), "new": n_new, "updated": n_updated,
            "reopened": n_reopened, "closed": n_closed, "note": note}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--ats", nargs="+", default=["greenhouse", "lever"])
    ap.add_argument("--per-ats", type=int, default=8)
    ap.add_argument("--source-ids", nargs="+", type=int)
    ap.add_argument("--all-active", action="store_true")
    args = ap.parse_args()

    conn = get_conn()
    try:
        cur = conn.cursor()
        sources = pick_sources(cur, args)
        print(f"{len(sources)} sources\n")
        totals = {"new": 0, "updated": 0, "reopened": 0, "closed": 0, "failed": 0}
        for i, src in enumerate(sources, 1):
            t0 = time.perf_counter()
            res = ingest_source(conn, src)
            label = f"[{i}/{len(sources)}] {src[4][:32]:<34} {src[2]}:{src[3]}"
            if res["status"] == "failed":
                totals["failed"] += 1
                print(f"{label}  FAILED {res['error']}")
                continue
            for k in ("new", "updated", "reopened", "closed"):
                totals[k] += res[k]
            print(f"{label}  {res['returned']:>4} returned  {res['new']:>4} new  "
                  f"{res['updated']:>3} updated  {res['reopened']:>2} reopened  "
                  f"{res['closed']:>3} closed  ({time.perf_counter() - t0:.1f}s)"
                  + (f"  [{res['note']}]" if res["note"] else ""))
        print(f"\nTotal: {totals['new']} new, {totals['updated']} updated, "
              f"{totals['reopened']} reopened, {totals['closed']} closed, "
              f"{totals['failed']} failed sources")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
