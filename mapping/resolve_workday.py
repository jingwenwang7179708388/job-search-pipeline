#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resolve_workday.py (v4) - find Workday career sites for companies not yet resolved

v3: dropped the "hit the root path to get the site name" step (the root path returns 406 with no
redirect, and a nonexistent tenant also returns 406, so it tells nothing). Brute-force the site name
by POST instead, and record failure reasons rather than swallowing them.

v4: large runs were returning 0 hits. The cause was not the root path: Workday rate-limits rapid
requests to one tenant and returns HTTP 200 with an empty body, which was being counted as a wrong
guess and wiped out real hits. probe() now backs off and retries on "200 but not JSON" instead of
giving up.

Usage: python3 resolve_workday.py 200
"""

import csv
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

# Paths are relative to this file so the script runs from any working directory
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Content-Type": "application/json"}
TIMEOUT = 12
WORKERS = 8
PAUSE = 0.15

# Originally only wd1/wd3/wd5 were tried, but Verizon is on wd12 and Nordstrom on wd501, so the
# data-center number range is wider than expected. One- and two-digit numbers are common; a
# three-digit one has been seen once, so only that known value is added (no open-ended search).
WD_NUMBERS = ["wd1", "wd2", "wd3", "wd4", "wd5", "wd10", "wd11", "wd12", "wd501"]

LEGAL = (r"\b(INC|LLC|LTD|CORP|CORPORATION|COMPANY|CO|LP|LLP|PLC|PBC|HOLDINGS|"
         r"USA|US|AMERICA|AMERICAS|NA|SERVICES|SERVICE|OPERATIONS|INTERNATIONAL)\b")

TOO_GENERIC = {"one", "new", "general", "national", "applied", "public", "insurance",
               "human", "technology", "technologies", "systems", "solutions", "group",
               "capital", "global", "management", "partners", "first", "united",
               "american", "advanced", "digital", "data", "health", "medical",
               "the", "and", "for", "inc", "corp", "company"}

STATS = Counter()


def tenants(name):
    n = re.sub(r"[^A-Za-z0-9 ]", " ", str(name).upper())
    n = re.sub(r"\s+", " ", n).strip()
    core = re.sub(LEGAL, " ", n)
    words = re.sub(r"\s+", " ", core).strip().split()
    out = []
    if words:
        out.append("".join(words))
        if len(words) == 2:
            out.append(words[0])
    out.append("".join(n.split()))
    seen, res = set(), []
    for c in out:
        c = c.lower()
        if c and c not in seen and 2 < len(c) < 30 and c not in TOO_GENERIC:
            seen.add(c)
            res.append(c)
    return res[:2]


def sites_for(tenant):
    """Common site-name spellings, ordered by frequency."""
    t = tenant
    return ["External", "ExternalCareerSite", t, "External_Career_Site",
            f"{t}careers", "Careers", f"{t.upper()}ExternalCareerSite",
            f"{t}_careers"]


def probe(tenant, wd, site, attempt=1):
    """Return the total number of postings; 0 means the combination is wrong. Also counts status codes.

    HTTP 200 with a body that is not JSON is Workday rate-limiting rapid requests to one tenant
    (empty body); it does not mean the tenant/site guess is wrong. Counting it as 0 used to wipe
    out real hits on large runs, so back off and retry a few times instead.
    """
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    try:
        r = requests.post(url, json={"appliedFacets": {}, "limit": 20,
                                     "offset": 0, "searchText": ""},
                          headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        STATS[f"exception:{type(e).__name__}"] += 1
        return 0
    STATS[f"HTTP {r.status_code}"] += 1
    if not r.ok:
        return 0
    try:
        return int(r.json().get("total", 0))
    except Exception:
        if attempt < 3:
            STATS["json_parse_failed_retry"] += 1
            time.sleep(1.0 * attempt)
            return probe(tenant, wd, site, attempt + 1)
        STATS["json_parse_failed_gave_up"] += 1
        return 0


def resolve(row):
    for tenant in tenants(row["company"]):
        for wd in WD_NUMBERS:
            for site in sites_for(tenant):
                time.sleep(PAUSE)
                n = probe(tenant, wd, site)
                if n > 0:
                    return {"company": row["company"], "ats": "workday",
                            "slug": f"{tenant}|{wd}|{site}", "priority": "",
                            "n_jobs": n}
    return None


def main():
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 200

    with open(DATA_DIR / "companies.csv", encoding="utf-8-sig") as f:
        done = {r["company"].strip().lower() for r in csv.DictReader(f)}

    with open(DATA_DIR / "sponsors.csv", encoding="utf-8-sig") as f:
        rows = [{"company": r["raw_name"].strip(), "count": int(r["sponsor_count"])}
                for r in csv.DictReader(f)
                if r["raw_name"].strip().lower() not in done]

    rows.sort(key=lambda r: -r["count"])
    rows = rows[:top_n]

    print(f"First {len(rows)} companies, up to 2 tenants x {len(WD_NUMBERS)} data centers x "
          f"{len(sites_for('x'))} site names each\n")

    found, n_done = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for row, res in zip(rows, ex.map(resolve, rows)):
            n_done += 1
            if res:
                found.append(res)
                print(f"[{n_done}/{len(rows)}] OK {row['company'][:30]:<32} "
                      f"{res['slug']} ({res['n_jobs']} open)")
            if n_done % 25 == 0:
                print(f"    ...done {n_done}, hits {len(found)}")

    with open(DATA_DIR / "companies_workday.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["company", "ats", "slug", "priority"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(found)

    print(f"\n{'='*52}")
    print(f"Workday sites found: {len(found)} -> data/companies_workday.csv")
    print(f"Open postings: {sum(r['n_jobs'] for r in found):,}")
    print(f"\nRequest outcome breakdown (to tell blocking from wrong guesses):")
    for k, v in STATS.most_common(8):
        print(f"  {k:<24} {v:,}")
    print("=" * 52)


if __name__ == "__main__":
    main()