#!/usr/bin/env python3
"""
mapping/load_ats_sources.py - load the resolved ATS slugs into MySQL ats_sources

Reads data/companies.csv (company, ats, slug, priority; produced by resolve_slugs.py /
resolve_workday.py) and applies mapping/ats_slug_review.csv, a hand-checked list of slugs:
  reject     - the slug belongs to a different company; not loaded
  duplicate  - another row for the same company keeps this slug; not loaded
  inactive   - mapping is right but the board is gone or empty; loaded with is_active = FALSE
  verified   - checked by hand; loaded normally (rows not in the review file load the same way)
Each company is matched to companies.company_id by common.names.normalize(); rows with no
match are listed and not loaded (ats_sources.company_id is NOT NULL).

Idempotent: reruns update company_id / is_active of existing (ats_type, slug) rows.

Usage (from the project root): python3 mapping/load_ats_sources.py
"""

import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common.db import get_conn           # noqa: E402
from common.names import normalize       # noqa: E402

COMPANIES_CSV = ROOT / "data" / "companies.csv"
REVIEW_CSV = Path(__file__).resolve().parent / "ats_slug_review.csv"
SKIP = {"reject", "duplicate"}


def main():
    rows = list(csv.DictReader(open(COMPANIES_CSV, encoding="utf-8-sig")))
    review = {(r["ats"], r["slug"], r["company"]): r["decision"]
              for r in csv.DictReader(open(REVIEW_CSV, encoding="utf-8"))}
    unused = set(review) - {(r["ats"], r["slug"], r["company"]) for r in rows}
    if unused:
        sys.exit(f"Review entries with no matching row in {COMPANIES_CSV.name}: {sorted(unused)}")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT normalized_name, company_id FROM companies")
        ids = dict(cur.fetchall())

        load, unmatched, counts = [], [], Counter()
        for r in rows:
            decision = review.get((r["ats"], r["slug"], r["company"]), "unreviewed")
            counts[decision] += 1
            if decision in SKIP:
                continue
            cid = ids.get(normalize(r["company"]))
            if cid is None:
                unmatched.append(r)
                continue
            load.append((cid, r["ats"], r["slug"], decision != "inactive"))

        dup = [k for k, n in Counter((a, s) for _, a, s, _ in load).items() if n > 1]
        if dup:
            sys.exit(f"Same (ats, slug) on several companies; add them to {REVIEW_CSV.name}: {dup}")

        cur.executemany(
            "INSERT INTO ats_sources (company_id, ats_type, slug, is_active) "
            "VALUES (%s, %s, %s, %s) AS new "
            "ON DUPLICATE KEY UPDATE company_id = new.company_id, is_active = new.is_active",
            load)
        conn.commit()

        cur.execute("SELECT ats_type, SUM(is_active), SUM(NOT is_active), COUNT(*) "
                    "FROM ats_sources GROUP BY ats_type ORDER BY COUNT(*) DESC")
        by_ats = cur.fetchall()
    finally:
        conn.close()

    print(f"{COMPANIES_CSV.name}: {len(rows)} rows")
    for k in ("unreviewed", "verified", "inactive", "reject", "duplicate"):
        print(f"  {k:<11} {counts[k]:>4}")
    print(f"Not in companies table (not loaded): {len(unmatched)}")
    for r in unmatched:
        print(f"  {r['company']:<40} {r['ats']}:{r['slug']}  normalized={normalize(r['company'])}")
    print(f"Loaded/updated: {len(load)}\n\nats_sources now:")
    print(f"  {'ats_type':<16}{'active':>7}{'inactive':>9}{'total':>7}")
    for ats, a, i, n in by_ats:
        print(f"  {ats:<16}{int(a):>7}{int(i):>9}{n:>7}")


if __name__ == "__main__":
    main()
