#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resolve_slugs.py —— 把公司名解析成招聘系统(ATS)里的真实 slug
用法: python3 resolve_slugs.py companies_new.csv
"""

import csv
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 12
WORKERS = 16

LEGAL = (r"\b(INC|LLC|LTD|CORP|CORPORATION|COMPANY|CO|LP|LLP|PLC|PBC|HOLDINGS|"
         r"GROUP|USA|US|AMERICA|AMERICAS|NA|SERVICES|SERVICE|OPERATIONS|"
         r"SOLUTIONS|GLOBAL|INTERNATIONAL|PLATFORMS|DEVELOPMENT|CENTER|"
         r"INVESTMENTS|MANAGEMENT|PARTNERS|CAPITAL|SECURITIES)\b")


def candidates(name):
    n = re.sub(r"[^A-Za-z0-9 ]", " ", str(name).upper())
    n = re.sub(r"\s+", " ", n).strip()
    core = re.sub(LEGAL, " ", n)
    core = re.sub(r"\s+", " ", core).strip()
    words = core.split()

    out = []
    if words:
        out.append("".join(words))
        if len(words) >= 2:
            out.append("".join(words[:2]))
        if len(words) <= 2:
            out.append(words[0])
        out.append("-".join(w.lower() for w in words))
    out.append("".join(n.split()))

    seen, res = set(), []
    for c in out:
        c = c.lower()
        if c and c not in seen and len(c) > 2:
            seen.add(c)
            res.append(c)
    return res[:5]


def try_greenhouse(slug):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                     headers=HEADERS, timeout=TIMEOUT)
    return len(r.json().get("jobs", [])) if r.ok else 0


def try_lever(slug):
    r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json",
                     headers=HEADERS, timeout=TIMEOUT)
    return len(r.json()) if r.ok else 0


def try_ashby(slug):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                     headers=HEADERS, timeout=TIMEOUT)
    return len(r.json().get("jobs", [])) if r.ok else 0

def try_smartrecruiters(slug):
    r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=10",
                     headers=HEADERS, timeout=TIMEOUT)
    if not r.ok:
        return 0
    d = r.json()
    return int(d.get("totalFound", 0))      # 必须查 totalFound，否则它对谁都说 OK


def try_recruitee(slug):
    r = requests.get(f"https://{slug}.recruitee.com/api/offers/",
                     headers=HEADERS, timeout=TIMEOUT)
    return len(r.json().get("offers", [])) if r.ok else 0


def try_workable(slug):
    r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
                     headers=HEADERS, timeout=TIMEOUT)
    return len(r.json().get("jobs", [])) if r.ok else 0


PROBES = [("greenhouse", try_greenhouse), ("lever", try_lever), ("ashby", try_ashby),
          ("smartrecruiters", try_smartrecruiters), ("recruitee", try_recruitee),
          ("workable", try_workable)]

def resolve(row):
    for slug in candidates(row["company"]):
        for ats, fn in PROBES:
            try:
                n = fn(slug)
            except Exception:
                continue
            floor = 5 if ats in ("smartrecruiters", "recruitee") else 1
            if n >= floor:
                return {**row, "ats": ats, "slug": slug, "n_jobs": n}
    return None


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)

    # 增量合并, 不覆盖: companies.csv 里已经手动并进去过 workday 那批,
    # 之前这里是直接覆盖写, 再跑一次会把那些冲掉。
    existing = []
    already = set()
    if os.path.exists("companies.csv"):
        with open("companies.csv", encoding="utf-8-sig") as f:
            existing = list(csv.DictReader(f))
        already = {r["company"].strip().lower() for r in existing}

    with open(sys.argv[1], encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f)
                if r.get("company", "").strip()
                and r["company"].strip().lower() not in already]

    print(f"{len(rows)} 家公司待解析（已跳过 companies.csv 里 {len(already)} 家）, "
          f"开始解析（每家最多试 5 个名字 x 6 个平台）...\n")

    found, missing, n_done = [], [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for row, res in zip(rows, ex.map(resolve, rows)):
            n_done += 1
            if res:
                found.append(res)
                print(f"[{n_done}/{len(rows)}] OK {row['company'][:34]:<36} "
                      f"{res['ats']}:{res['slug']} ({res['n_jobs']} 个在招)")
            else:
                missing.append(row["company"])
            if n_done % 25 == 0:
                print(f"    ...已完成 {n_done}, 命中 {len(found)}")

    with open("companies.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["company", "ats", "slug", "priority"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(existing)
        w.writerows(found)

    with open("unresolved.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(missing))

    print(f"\n{'='*50}")
    print(f"解析成功: {len(found)} 家（新增）, companies.csv 现在共 {len(existing) + len(found)} 家")
    print(f"没找到:   {len(missing)} 家  -> unresolved.txt")
    print(f"这些公司当前在招岗位合计: {sum(r['n_jobs'] for r in found):,}")
    print("=" * 50)


if __name__ == "__main__":
    main()