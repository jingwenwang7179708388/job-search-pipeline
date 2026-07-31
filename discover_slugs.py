#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discover_slugs.py —— 从公司官网发现真实 ATS slug, 而不是靠猜名字变体

背景: resolve_slugs.py 靠猜公司名拼写变体去试 ATS API, 命中率只有 ~20%——瓶颈是
猜不中公司名对应的 slug 拼法, 不是平台没覆盖到（Greenhouse 市占 49%、Workday
22%, 这两个 resolve_slugs.py/job_feed.py 都已经支持)。这个脚本换个路子:

    公司名 -> Clearbit autocomplete 查官网域名
           -> 抓官网 /careers、/jobs 等页面（都没有就退到首页, 再退到首页里找的
              career/jobs 链接）
           -> 直接从页面源码里正则抠出 ATS 域名和 slug
              (boards.greenhouse.io/xxx、jobs.lever.co/xxx、
               xxx.wd5.myworkdayjobs.com/yyy 等, 含 script 标签里的 embed 链接)
           -> 用 resolve_slugs.py / resolve_workday.py 里现成的探测函数验证一遍

先用 --test（默认 30 家, 按 sponsor_count 从高到低取 sponsors.csv 里还没进
companies.csv/companies_workday.csv 的公司）看命中率, 过得去再决定要不要跑全量、
要不要接进 resolve_slugs.py 主流程替换掉猜名字那一套。

用法:
    python3 discover_slugs.py            # 默认测前 30 家
    python3 discover_slugs.py --test 50  # 测前 50 家
"""

import argparse
import csv
import os
import random
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

from resolve_slugs import (try_greenhouse, try_lever, try_ashby,
                            try_smartrecruiters, try_recruitee, try_workable)
from resolve_workday import probe as wd_probe

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TIMEOUT = 8
WORKERS = 10

LEGAL = (r"\b(INC|LLC|LTD|CORP|CORPORATION|COMPANY|CO|LP|LLP|PLC|PBC|HOLDINGS|"
         r"GROUP|USA|US|AMERICA|AMERICAS|NA)\b")

# 常见 careers 页面路径, 按出现频率排序; 每个都试 裸域名 + www 两种host
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
    """Clearbit 对 3 个词以上的查询经常直接返回 []（"Meta Platforms"、"Two Sigma
    Investments" 都是空), 所以从完整清洗名开始, 依次退到前两个词、第一个词。
    命中了空结果就退一级再试; 拿到结果后优先选 name 跟查询词精确匹配（大小写不敏感）
    的候选, 而不是无脑取第一条——Clearbit 排序有时把无关公司排在前面（比如查
    "Meta" 第一条是 Metacritic）。"""
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
    """返回 (页面url, ats, slug)。找不到时 ats/slug 是 None。"""
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
        break  # 首页都试过一次就够了, 不用裸域名/www各来一遍
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
        return {"company": name, "stage": "没查到域名"}

    url, ats, slug = find_ats(domain)
    if not ats:
        return {"company": name, "domain": domain, "matched_name": matched_name,
                "careers_url": url, "stage": "页面没提取到 ATS 链接"}

    n = validate(ats, slug)
    stage = "验证通过" if n > 0 else "提取到但验证失败"
    return {"company": name, "domain": domain, "matched_name": matched_name,
            "careers_url": url, "ats": ats, "slug": slug, "n_jobs": n, "stage": stage}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", type=int, default=30,
                     help="测试多少家未解析公司")
    ap.add_argument("--sample", choices=["top", "random"], default="top",
                     help="top=按sponsor_count从高到低取（贴近真实优先级）; "
                          "random=全量随机抽样（sponsor_count最高的那批几乎全是"
                          "自建招聘系统的大厂, 不代表长尾公司的命中率）")
    ap.add_argument("--seed", type=int, default=42, help="--sample random 用的随机种子")
    args = ap.parse_args()

    done = set()
    for path in ("companies.csv", "companies_workday.csv"):
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as f:
                done |= {r["company"].strip().lower() for r in csv.DictReader(f)}

    if not os.path.exists("sponsors.csv"):
        sys.exit("找不到 sponsors.csv")
    with open("sponsors.csv", encoding="utf-8-sig") as f:
        rows = [{"company": r["raw_name"].strip(), "count": int(r["sponsor_count"])}
                for r in csv.DictReader(f)
                if r["raw_name"].strip().lower() not in done]

    if args.sample == "random":
        random.Random(args.seed).shuffle(rows)
        rows = rows[:args.test]
        print(f"测试 {len(rows)} 家（随机抽样, seed={args.seed}, 目前还没解析出 ATS 的公司）\n")
    else:
        rows.sort(key=lambda r: -r["count"])
        rows = rows[:args.test]
        print(f"测试 {len(rows)} 家（sponsor_count 最高、目前还没解析出 ATS 的公司）\n")

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, (row, res) in enumerate(zip(rows, ex.map(discover, rows)), 1):
            results.append(res)
            extra = (f" {res['ats']}:{res['slug']} ({res.get('n_jobs', 0)} 个在招)"
                     if res.get("ats") else "")
            domain_info = f" [{res['domain']}]" if res.get("domain") else ""
            print(f"[{i}/{len(rows)}] {row['company'][:36]:<38} {res['stage']}{domain_info}{extra}")

    hit = [r for r in results if r["stage"] == "验证通过"]
    print(f"\n{'='*54}")
    print(f"命中: {len(hit)}/{len(results)} ({100 * len(hit) / len(results):.0f}%)")
    for stage, n in Counter(r["stage"] for r in results).most_common():
        print(f"  {stage:<20} {n}")
    if hit:
        print(f"\n命中的岗位合计: {sum(r['n_jobs'] for r in hit):,}")
    print("=" * 54)


if __name__ == "__main__":
    main()
