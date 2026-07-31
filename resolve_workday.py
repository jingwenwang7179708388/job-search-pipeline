#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resolve_workday.py (v4) —— 给还没解析的公司找 Workday 招聘站

v3 改动: 去掉「访问根路径拿站点名」那一步（实测根路径返回 406, 不跳转,
且不存在的租户也返回 406, 完全无法用于判断）。改为直接 POST 穷举站点名。
并且记录失败原因, 不再静默吞掉。

v4 改动: 量大了以后发现大批量跑会命中 0——不是根路径的问题, 是 Workday 对同一
tenant 短时间内连续请求会限流, 返回 HTTP 200 但空 body, 之前这种情况被当成
"组合猜错了"直接判 0, 把真命中也一起误杀了。probe() 现在对这种"200 但解析
不出 JSON"的情况做退避重试, 而不是直接认输。

用法: python3 resolve_workday.py 200
"""

import csv
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Content-Type": "application/json"}
TIMEOUT = 12
WORKERS = 8
PAUSE = 0.15

# 原来只试 wd1/wd3/wd5, 实测 Verizon 是 wd12、Nordstrom 是 wd501, 说明数据中心
# 编号的范围比想象中宽——单双位数字比较常见, 501 这种三位数目前只见过一例,
# 就不做开放式穷举了（组合数会爆炸), 只加这一个已知命中的当兜底。
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
    """站点名的常见写法, 按出现频率排序。"""
    t = tenant
    return ["External", "ExternalCareerSite", t, "External_Career_Site",
            f"{t}careers", "Careers", f"{t.upper()}ExternalCareerSite",
            f"{t}_careers"]


def probe(tenant, wd, site, attempt=1):
    """返回岗位总数; 0 表示这个组合不对。顺便统计状态码。

    HTTP 200 但 body 解析不出 JSON, 实测是 Workday 对同一 tenant 短时间内连续
    请求做的限流(空 body), 不代表 tenant/site 猜错了 —— 之前把这种情况直接判 0,
    量一大就会把真命中也误杀掉。这里退避重试几次, 而不是当场认输。
    """
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    try:
        r = requests.post(url, json={"appliedFacets": {}, "limit": 20,
                                     "offset": 0, "searchText": ""},
                          headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        STATS[f"异常:{type(e).__name__}"] += 1
        return 0
    STATS[f"HTTP {r.status_code}"] += 1
    if not r.ok:
        return 0
    try:
        return int(r.json().get("total", 0))
    except Exception:
        if attempt < 3:
            STATS["JSON解析失败_重试"] += 1
            time.sleep(1.0 * attempt)
            return probe(tenant, wd, site, attempt + 1)
        STATS["JSON解析失败_放弃"] += 1
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

    with open("companies.csv", encoding="utf-8-sig") as f:
        done = {r["company"].strip().lower() for r in csv.DictReader(f)}

    with open("sponsors.csv", encoding="utf-8-sig") as f:
        rows = [{"company": r["raw_name"].strip(), "count": int(r["sponsor_count"])}
                for r in csv.DictReader(f)
                if r["raw_name"].strip().lower() not in done]

    rows.sort(key=lambda r: -r["count"])
    rows = rows[:top_n]

    print(f"前 {len(rows)} 家, 每家最多 2 租户 x {len(WD_NUMBERS)} 机房 x "
          f"{len(sites_for('x'))} 站点名\n")

    found, n_done = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for row, res in zip(rows, ex.map(resolve, rows)):
            n_done += 1
            if res:
                found.append(res)
                print(f"[{n_done}/{len(rows)}] OK {row['company'][:30]:<32} "
                      f"{res['slug']} ({res['n_jobs']} 个在招)")
            if n_done % 25 == 0:
                print(f"    ...已完成 {n_done}, 命中 {len(found)}")

    with open("companies_workday.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["company", "ats", "slug", "priority"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(found)

    print(f"\n{'='*52}")
    print(f"找到 Workday 站点: {len(found)} 家 -> companies_workday.csv")
    print(f"在招岗位合计: {sum(r['n_jobs'] for r in found):,}")
    print(f"\n请求结果分布（用来判断是被挡了还是名字猜错了）:")
    for k, v in STATS.most_common(8):
        print(f"  {k:<24} {v:,}")
    print("=" * 52)


if __name__ == "__main__":
    main()