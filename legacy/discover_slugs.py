#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discover_slugs.py - discover the real ATS slug from a company's website instead of guessing name variants

Background: resolve_slugs.py probes ATS APIs with guessed spellings of the company name and only
hits ~20%. The bottleneck is guessing the slug spelling, not platform coverage (Greenhouse has
49% market share and Workday 22%; resolve_slugs.py/job_feed.py support both). This script instead does:

    company name -> Clearbit autocomplete to find the website domain
           -> fetch /careers, /jobs and similar pages (falling back to the homepage, then to
              career/jobs links found on the homepage)
           -> regex the ATS domain and slug straight out of the page source
              (boards.greenhouse.io/xxx, jobs.lever.co/xxx,
               xxx.wd5.myworkdayjobs.com/yyy etc., including embed links inside script tags)
           -> verify with the existing probe functions in resolve_slugs.py / resolve_workday.py

Start with --test (default 30 companies: the highest sponsor_count rows in sponsors.csv that are
not yet in companies.csv/companies_workday.csv) to check the hit rate, then decide whether to run
everything and whether to replace the name-guessing step in resolve_slugs.py.

Usage:
    python3 discover_slugs.py            # test the first 30 by default
    python3 discover_slugs.py --test 50  # test the first 50
"""

import argparse
import csv
import os
import random
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

# Paths are relative to this file so the script runs from any working directory;
# the probe functions it reuses now live in mapping/
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
sys.path.insert(0, str(ROOT / "mapping"))

from resolve_slugs import (try_greenhouse, try_lever, try_ashby,
                            try_smartrecruiters, try_recruitee, try_workable)
from resolve_workday import probe as wd_probe

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TIMEOUT = 8
WORKERS = 10

LEGAL = (r"\b(INC|LLC|LTD|CORP|CORPORATION|COMPANY|CO|LP|LLP|PLC|PBC|HOLDINGS|"
         r"GROUP|USA|US|AMERICA|AMERICAS|NA)\b")

# Common careers-page paths, ordered by frequency; each is tried on the bare domain and on www
CAREERS_PATHS = ["careers", "jobs", "join-us", "about/careers",
                  "company/careers", "careers/open-positions"]

ATS_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)")),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([a-zA-Z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)")),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([a-zA-Z0-9_-]+)")),
    ("recruitee", re.compile(r"([a-zA-Z0-9_-]+)\.recruitee\.com")),
    ("workable", re.compile(r"apply\.workable\.com/([a-zA-Z0-9_-]+)")),
    ("workday", re.compile(r"([a-zA-Z0-9.-]+)\.(wd\d+)\.myworkdayjobs\.com/"
                           r"(?:[a-zA-Z]{2}-[A-Z]{2}/)?([a-zA-Z0-9_-]+)")),
]

VALIDATORS = {"greenhouse": try_greenhouse, "lever": try_lever, "ashby": try_ashby,
              "smartrecruiters": try_smartrecruiters, "recruitee": try_recruitee,
              "workable": try_workable}


def clean_query(name):
    n = re.sub(r"[^A-Za-z0-9 ]", " ", str(name).upper())
    n = re.sub(LEGAL, " ", n)
    return re.sub(r"\s+", " ", n).strip().title()


def find_domain(name):
    """Clearbit often returns [] for queries of 3+ words ("Meta Platforms", "Two Sigma
    Investments" are both empty), so start from the full cleaned name and fall back to the first
    two words, then the first word. On an empty result, step down one level and retry. From the
    results, prefer a candidate whose name matches the query exactly (case-insensitive) over the
    first row: Clearbit sometimes ranks unrelated companies first ("Meta" returns Metacritic)."""
    full = clean_query(name)
    words = full.split()
    queries = [full]
    if len(words) > 2:
        queries.append(" ".join(words[:2]))
    if len(words) > 1:
        queries.append(words[0])

    tried = set()
    for q in queries:
        if not q or q in tried:
            continue
        tried.add(q)
        try:
            r = requests.get("https://autocomplete.clearbit.com/v1/companies/suggest",
                             params={"query": q}, headers=HEADERS, timeout=TIMEOUT)
            results = r.json() if r.ok else []
        except Exception:
            results = []
        if not results:
            continue
        for res in results:
            if res.get("name", "").strip().lower() == q.strip().lower():
                return res.get("domain"), res.get("name")
        return results[0].get("domain"), results[0].get("name")
    return None, None


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.ok and "html" in r.headers.get("Content-Type", "").lower():
            return r.text
    except Exception:
        pass
    return None


def extract_ats(html):
    for ats, pat in ATS_PATTERNS:
        m = pat.search(html)
        if m:
            if ats == "workday":
                return ats, f"{m.group(1)}|{m.group(2)}|{m.group(3)}"
            return ats, m.group(1)
    return None, None


def find_ats(domain):
    """Return (page url, ats, slug). ats/slug are None when nothing is found."""
    hosts = [f"https://{domain}", f"https://www.{domain}"]
    last_url = None
    for path in CAREERS_PATHS:
        for host in hosts:
            url = f"{host}/{path}"
            html = fetch(url)
            if not html:
                continue
            last_url = url
            ats, slug = extract_ats(html)
            if ats:
                return url, ats, slug

    for host in hosts:
        url = f"{host}/"
        home = fetch(url)
        if not home:
            continue
        last_url = url
        ats, slug = extract_ats(home)
        if ats:
            return url, ats, slug
        m = re.search(r'href=["\']([^"\']*(?:career|jobs)[^"\']*)["\']', home, re.I)
        if m:
            link = m.group(1)
            link_url = link if link.startswith("http") else host + (link if link.startswith("/") else f"/{link}")
            html = fetch(link_url)
            if html:
                ats, slug = extract_ats(html)
                if ats:
                    return link_url, ats, slug
        break  # trying the homepage once is enough; no need for both the bare domain and www
    return last_url, None, None


def validate(ats, slug):
    if ats == "workday":
        tenant, wd, site = slug.split("|")
        try:
            return wd_probe(tenant, wd, site)
        except Exception:
            return 0
    try:
        return VALIDATORS[ats](slug)
    except Exception:
        return 0


def discover(row):
    name = row["company"]
    domain, matched_name = find_domain(name)
    if not domain:
        return {"company": name, "stage": "no domain found"}

    url, ats, slug = find_ats(domain)
    if not ats:
        return {"company": name, "domain": domain, "matched_name": matched_name,
                "careers_url": url, "stage": "no ATS link on page"}

    n = validate(ats, slug)
    stage = "verified" if n > 0 else "extracted but not verified"
    return {"company": name, "domain": domain, "matched_name": matched_name,
            "careers_url": url, "ats": ats, "slug": slug, "n_jobs": n, "stage": stage}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=int, default=30,
                     help="how many unresolved companies to test")
    ap.add_argument("--sample", choices=["top", "random"], default="top",
                     help="top=highest sponsor_count first (closest to real priority); "
                          "random=random sample of all (the highest sponsor_count rows are mostly "
                          "big companies with in-house career systems, not representative of the long tail)")
    ap.add_argument("--seed", type=int, default=42, help="random seed for --sample random")
    args = ap.parse_args()

    done = set()
    for path in (DATA_DIR / "companies.csv", DATA_DIR / "companies_workday.csv"):
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as f:
                done |= {r["company"].strip().lower() for r in csv.DictReader(f)}

    if not os.path.exists(DATA_DIR / "sponsors.csv"):
        sys.exit("Cannot find data/sponsors.csv")
    with open(DATA_DIR / "sponsors.csv", encoding="utf-8-sig") as f:
        rows = [{"company": r["raw_name"].strip(), "count": int(r["sponsor_count"])}
                for r in csv.DictReader(f)
                if r["raw_name"].strip().lower() not in done]

    if args.sample == "random":
        random.Random(args.seed).shuffle(rows)
        rows = rows[:args.test]
        print(f"Testing {len(rows)} companies (random sample, seed={args.seed}, no ATS resolved yet)\n")
    else:
        rows.sort(key=lambda r: -r["count"])
        rows = rows[:args.test]
        print(f"Testing {len(rows)} companies (highest sponsor_count, no ATS resolved yet)\n")

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, (row, res) in enumerate(zip(rows, ex.map(discover, rows)), 1):
            results.append(res)
            extra = (f" {res['ats']}:{res['slug']} ({res.get('n_jobs', 0)} open)"
                     if res.get("ats") else "")
            domain_info = f" [{res['domain']}]" if res.get("domain") else ""
            print(f"[{i}/{len(rows)}] {row['company'][:36]:<38} {res['stage']}{domain_info}{extra}")

    hit = [r for r in results if r["stage"] == "verified"]
    print(f"\n{'='*54}")
    print(f"Hits: {len(hit)}/{len(results)} ({100 * len(hit) / len(results):.0f}%)")
    for stage, n in Counter(r["stage"] for r in results).most_common():
        print(f"  {stage:<20} {n}")
    if hit:
        print(f"\nTotal postings at hit companies: {sum(r['n_jobs'] for r in hit):,}")
    print("=" * 54)


if __name__ == "__main__":
    main()
