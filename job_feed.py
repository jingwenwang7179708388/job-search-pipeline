#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job_feed.py —— 每日岗位聚合器
用法: python3 job_feed.py          只看今天新出现的
      python3 job_feed.py --all    看全部在招
      python3 job_feed.py --limit 20   只送前 20 个命中岗位去做 LLM 打分（验证效果/耗时用）
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

# ------------------------------------------------ 你的画像，改这里就改了筛选逻辑

# New Grad 分类用的候选人信息, 换人/换学期改这两行就行
GRAD_TERM = "Spring 2027"
GRAD_DEGREE = "Master's (MS)"

TITLE_INCLUDE = [
    "data scientist", "applied scientist", "decision scientist",
    "machine learning engineer", "ml engineer", "machine learning scientist",
    "ai engineer", "artificial intelligence engineer", "deep learning",
    "research engineer", "research scientist",
    "quantitative researcher", "quantitative analyst", "quantitative developer",
    "computer vision", "remote sensing", "geospatial", "perception engineer",
]

TITLE_EXCLUDE = [
    "senior", "sr.", "staff", "principal", "lead", "manager", "director",
    "head of", "vp ", "vice president", "architect", "ii ", "iii", " iv",
    "distinguished", "fellow",
]

BOOST = {
    "new grad": 30, "university grad": 30, "early career": 25, "entry level": 25,
    "2027": 25, "campus": 20, "graduate": 10, "intern": 15, "co-op": 15,
    "llm": 12, "nlp": 8, "recommendation": 8, "forecasting": 8,
    "remote sensing": 20, "satellite": 20, "geospatial": 20, "computer vision": 12,
    "quantitative": 12, "pytorch": 8,
}

VISA_RED_FLAGS = [
    "will not sponsor", "unable to sponsor", "do not sponsor",
    "not able to sponsor", "no visa sponsorship", "without sponsorship",
    "not provide sponsorship", "without the need for sponsorship",
    "us citizen", "u.s. citizen", "security clearance", "green card holder only",
    "must be authorized to work in the united states without",
]

US_HINTS = [
    "united states", "usa", "u.s.", "remote - us", "remote (us",
    "ca", "california", "new york", "ny", "san francisco", "sf", "los angeles",
    "seattle", "boston", "chicago", "austin", "texas", "denver", "atlanta",
    "washington", "oregon", "illinois", "massachusetts", "pennsylvania",
    "philadelphia", "houston", "san jose", "palo alto", "mountain view",
    "santa monica", "culver city", "pasadena", "glendale", "irvine", "san diego",
    "menlo park", "oakland", "sunnyvale", "bellevue", "remote",
    # 州全名兜底: 原来只覆盖了几个大都市/加州湾区, workday 岗位地点经常是
    # "Charlotte-NC" 这种非热门城市, 州名不全就会被当成非美国漏掉。
    "alabama", "alaska", "arizona", "arkansas", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "north carolina",
    "north dakota", "ohio", "oklahoma", "rhode island", "south carolina",
    "south dakota", "tennessee", "utah", "vermont", "virginia",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
    # workday 常见格式是两位州代码而不是全名（"Charlotte-NC"), 全名兜不住,
    # 补上代码。跳过 or/hi/in/ok/co/ma/mo/oh —— 分别对应俄勒冈/夏威夷/印第
    # 安纳/俄克拉荷马/科罗拉多/马萨诸塞/密苏里/俄亥俄, 太容易撞到英文常用词
    # (or/hi/in/ok 等), 这几个州改用城市名兜底（波士顿/丹佛已经在上面了）。
    "al", "ak", "az", "ar", "ct", "de", "fl", "ga", "ia", "id", "il",
    "ks", "ky", "la", "md", "me", "mi", "mn", "ms", "mt", "nc", "nd",
    "ne", "nh", "nj", "nm", "nv", "pa", "ri", "sc", "sd", "tn", "tx",
    "ut", "va", "vt", "wi", "wv", "wy",
    "portland", "indianapolis", "columbus", "cleveland", "cincinnati",
    "oklahoma city", "tulsa", "kansas city", "st. louis", "honolulu",
]

# ca/ny/sf 这种短代码用裸子串匹配会跟 "location" 里的 "ca" 这类词撞车,
# 短代码（不含空格/标点）一律走单词边界匹配, 长短语走原来的子串匹配。
def _kw_hit(loc, kw):
    if re.fullmatch(r"[a-z]+", kw):
        return re.search(rf"\b{kw}\b", loc) is not None
    return kw in loc

# ------------------------------------------------

HEADERS = {"User-Agent": "Mozilla/5.0 (job-feed personal script)"}
TIMEOUT = 20
SEEN_FILE = "seen_jobs.json"
NG_CACHE_FILE = "ng_cache.json"  # job_id -> {status, reason, evidence}, 打过分的不再重新调用
NG_DESC_CHARS = 1500  # JD 只截前这么多字符喂给 LLM, 省 token
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"  # 换模型/调速度就改这行
NG_RATE_LIMIT_RPM = 20  # Groq 免费层限额因模型而异, 429 打得多就调低这个值
NG_MIN_INTERVAL = 60.0 / NG_RATE_LIMIT_RPM
_last_ng_call_at = [0.0]


def _throttle_ng_call():
    wait = NG_MIN_INTERVAL - (time.monotonic() - _last_ng_call_at[0])
    if wait > 0:
        time.sleep(wait)
    _last_ng_call_at[0] = time.monotonic()


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


def fetch_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "title": j.get("title", ""),
        "location": (j.get("location") or {}).get("name", ""),
        "url": j.get("absolute_url", ""),
        "posted": (j.get("updated_at") or "")[:10],
        "desc": strip_html(j.get("content", "")),
        "job_id": f"gh:{slug}:{j.get('id')}",
    } for j in r.json().get("jobs", [])]


def fetch_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        ts = j.get("createdAt")
        posted = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else ""
        out.append({
            "title": j.get("text", ""),
            "location": cat.get("location", ""),
            "url": j.get("hostedUrl", ""),
            "posted": posted,
            "desc": strip_html(j.get("descriptionPlain") or j.get("description", "")),
            "job_id": f"lv:{slug}:{j.get('id')}",
        })
    return out


def fetch_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "title": j.get("title", ""),
        "location": j.get("location", ""),
        "url": j.get("jobUrl", ""),
        "posted": (j.get("publishedAt") or "")[:10],
        "desc": strip_html(j.get("descriptionPlain") or j.get("descriptionHtml", "")),
        "job_id": f"ab:{slug}:{j.get('id')}",
    } for j in r.json().get("jobs", [])]

def fetch_smartrecruiters(slug):
    out, offset = [], 0
    while True:
        r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                         params={"limit": 100, "offset": offset},
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        items = d.get("content", [])
        if not items:
            break
        for j in items:
            
            title = j.get("name", "")
            loc = j.get("location") or {}
            rec = {
                "title": title,
                "location": ", ".join(x for x in [loc.get("city"), loc.get("region"),
                                                  loc.get("country")] if x),
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                "posted": (j.get("releasedDate") or "")[:10],
                "desc": "",
                "job_id": f"sr:{slug}:{j.get('id')}",
            }
            if title_ok(title):
                try:
                    d2 = requests.get(
                        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j['id']}",
                        headers=HEADERS, timeout=TIMEOUT).json()
                    secs = (d2.get("jobAd") or {}).get("sections") or {}
                    rec["desc"] = strip_html(" ".join(
                        str(v.get("text", "")) for v in secs.values() if isinstance(v, dict)))
                except Exception:
                    pass
            out.append(rec)
        offset += len(items)
        if offset >= d.get("totalFound", 0) or offset > 2000:
            break
    return out


def fetch_recruitee(slug):
    r = requests.get(f"https://{slug}.recruitee.com/api/offers/",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "title": j.get("title", ""),
        "location": j.get("location") or j.get("city", ""),
        "url": j.get("careers_url") or j.get("careers_apply_url", ""),
        "posted": (j.get("published_at") or "")[:10],
        "desc": strip_html(j.get("description", "")),
        "job_id": f"rc:{slug}:{j.get('id')}",
    } for j in r.json().get("offers", [])]


def fetch_workable(slug):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
        out.append({
            "title": j.get("title", ""),
            "location": loc or ("Remote" if j.get("telecommuting") else ""),
            "url": j.get("url") or j.get("shortlink", ""),
            "posted": (j.get("published_on") or "")[:10],
            "desc": strip_html(j.get("description", "")),
            "job_id": f"wk:{slug}:{j.get('shortcode')}",
        })
    return out


_WD_N_LOCATIONS = re.compile(r"^\d+\s+locations?$", re.I)


def _workday_path_location(path):
    """从岗位详情页 URL 里解析真实地点。

    列表接口对多地点岗位只给 "2 Locations" 这种占位符, 看不出到底是不是美国
    （实测藏着新加坡/中国/台湾等海外岗）; 但详情页 URL 里编码了这条岗位具体
    对应的那一个地点（如 /job/Taichung---Fab-16-Taiwan/...), 比列表接口准。
    """
    m = re.match(r"^/job/([^/]+)/", path)
    return re.sub(r"-+", " ", m.group(1)).strip() if m else ""


def fetch_workday(slug):
    tenant, wd, site = slug.split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    list_url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out, seen_ids, offset, page, total = [], set(), 0, 0, None
    while True:
        # 同一 tenant 连翻页翻太快会被限流: HTTP 200 但空 body, 不是真的没数据了。
        # 实测加个提前的 pause（而不是失败了再退避）才稳: 事后重试时冷却期明显更长,
        # 提前按节奏来基本不会触发。第一页不用等, 之后每页先停一下再请求。
        if page > 0:
            time.sleep(1.5)
        d = None
        for attempt in range(3):
            try:
                r = requests.post(list_url, json={"appliedFacets": {}, "limit": 20,
                                                  "offset": offset, "searchText": ""},
                                  headers=HEADERS, timeout=TIMEOUT)
                r.raise_for_status()
                d = r.json()
                break
            except (requests.exceptions.RequestException, ValueError):
                time.sleep(2.0 * (attempt + 1))
        if d is None:
            break
        page += 1
        if total is None:
            total = d.get("total", 0)
        postings = d.get("jobPostings", [])
        if not postings:
            break
        for j in postings:
            title = j.get("title", "")
            path = j.get("externalPath", "")
            req_id = (j.get("bulletFields") or [None])[0] or path
            job_id = f"wd:{tenant}:{req_id}"
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            loc = j.get("locationsText", "")
            if not loc or _WD_N_LOCATIONS.match(loc.strip()):
                loc = _workday_path_location(path) or loc
            rec = {
                "title": title,
                "location": loc,
                "url": f"{base}/{site}{path}",
                "posted": "",
                "desc": "",
                "job_id": job_id,
            }
            if title_ok(title):
                try:
                    d2 = requests.get(f"{base}/wday/cxs/{tenant}/{site}{path}",
                                      headers=HEADERS, timeout=TIMEOUT).json()
                    info = d2.get("jobPostingInfo") or {}
                    rec["desc"] = strip_html(info.get("jobDescription", ""))
                    rec["posted"] = (info.get("startDate") or "")[:10]
                except Exception:
                    pass
            out.append(rec)
        offset += len(postings)
        # 实测有的 tenant（如 phreesia）翻过真实总数之后不会返回空 jobPostings,
        # 而是把最后一页原样再吐一次——不能只拿"这页是不是空的"当翻页终点,
        # 会陷入重复翻页直到硬顶。改用第一页读到的 total（唯一准的一次）做终点;
        # job_id 去重是防这种重复页混进结果的兜底。total 拿不到时退回硬顶兜底。
        if total and offset >= total:
            break
        if offset > 2000:
            break
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "smartrecruiters": fetch_smartrecruiters, "recruitee": fetch_recruitee,
            "workday": fetch_workday, "workable": fetch_workable}

def title_ok(title):
    t = title.lower()
    return (any(k in t for k in TITLE_INCLUDE)
            and not any(k in t for k in TITLE_EXCLUDE))


def is_us(location):
    if not location:
        return True
    loc = location.lower()
    non_us = ["london", "berlin", "amsterdam", "toronto", "vancouver", "india",
              "bangalore", "singapore", "sydney", "tokyo", "dublin", "paris",
              "tel aviv", "shanghai", "beijing", "hong kong", "munich", "zurich",
              "warsaw", "mexico", "brazil", "canada", "united kingdom", "emea",
              "taiwan", "australia", "china", "japan", "korea", "vietnam",
              "philippines", "thailand", "indonesia", "malaysia", "ireland",
              "israel", "new zealand", "argentina", "colombia", "chile",
              "south africa", "italy", "spain", "portugal", "sweden",
              "switzerland", "austria", "belgium", "poland", "romania",
              "russia", "ukraine", "turkey", "germany", "canberra", "melbourne",
              "brisbane", "perth",
              # workday 的 remote 岗位常用 ISO 三字码标国家（如 "Remote-IND"),
              # 短代码走 _kw_hit 的单词边界匹配, 不会跟 "Indianapolis" 这类词撞车
              "ind", "deu", "gbr", "twn", "chn", "sgp", "aus", "jpn", "kor",
              "fra", "esp", "ita", "nld", "pol", "bra", "irl", "isr",
              "phl", "vnm", "tha", "idn", "mys", "nzl", "che", "arg", "col",
              "chl", "zaf", "rus", "ukr"]
    if any(_kw_hit(loc, k) for k in non_us):
        return False
    return any(_kw_hit(loc, k) for k in US_HINTS)


def visa_flag(desc):
    d = desc.lower()
    hits = [k for k in VISA_RED_FLAGS if k in d]
    return "; ".join(hits[:2]) if hits else ""


def score(job):
    blob = (job["title"] + " " + job["desc"][:3000]).lower()
    s = sum(pts for kw, pts in BOOST.items() if kw in blob)
    if job["posted"]:
        try:
            days = (datetime.utcnow() - datetime.strptime(job["posted"], "%Y-%m-%d")).days
            s += 20 if days <= 3 else 12 if days <= 7 else 5 if days <= 14 else 0
        except Exception:
            pass
    return s


NG_SYSTEM_PROMPT = f"""你在帮一个求职者判断职位是否对应届生（New Grad）友好。

候选人背景: {GRAD_TERM} 毕业, {GRAD_DEGREE} 在读, 没有全职工作经验（实习/co-op 除外）。

排除(excluded), 满足任一条即排除:
1. JD 里出现 "X+ years of experience" 或 "X-Y years" 且 X>=1, 并且没有说明实习/co-op
   时长可以折算进去
2. JD 同时要求年限经验（"X+ years"）和 "Master's/PhD required" —— 这个组合基本永远
   不是校招岗, 不管 title 写的是什么
3. title 或职级词带 Senior/Staff/Principal/Lead/II/III/Manager/Director

New Grad 友好(new_grad_friendly), 满足任一条:
1. title 或分类带 "New Grad"/"University"/"Graduate"/"Early Career"/"Rotational"
2. JD 明确写了毕业时间窗口, 且跟候选人的 {GRAD_TERM} 匹配（有重叠就算匹配）
3. 要求里写 "0-2 years"/"no prior professional experience required"/
   "currently pursuing a degree" 这类表述

排除规则和 New Grad 规则同时命中时（比如 title 是 New Grad 但正文写了 3+ years）,
以排除规则为准。

如果 JD 没写清楚经验要求, 或者写得很模糊（比如 "some experience preferred" 但没给
年限), 不要排除, 判 needs_review, 并在 evidence 里给出触发这个判断的原文句子。

注意: 你只能看到职位标题和 JD 正文, 看不出这个岗位是不是来自公司专门的
University/Campus 招聘页面——这条判断依据你判断不了, 不用考虑。

只输出这样的 JSON, 不要有任何其他文字:
{{"status": "new_grad_friendly" 或 "excluded" 或 "needs_review", "reason": "一句话中文理由", "evidence": "支撑判断的 JD 原文句子, 没有就留空字符串"}}
"""


def load_ng_cache():
    if os.path.exists(NG_CACHE_FILE):
        return json.load(open(NG_CACHE_FILE, encoding="utf-8"))
    return {}


def save_ng_cache(cache):
    json.dump(cache, open(NG_CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)


def classify_new_grad(groq_headers, job_id, title, company, desc, cache):
    if job_id in cache:
        c = cache[job_id]
        return c["status"], c["reason"], c["evidence"], True

    # Groq 免费额度 RPM 卡得比较紧, 光靠命中了再退避不够——每次调用前用全局节流器
    # 卡在 NG_RATE_LIMIT_RPM 以内（跟 fetch_workday 里对付 workday 限流是同一个思路),
    # 429 再在节流之上额外退避重试。
    user_msg = f"职位: {title}\n公司: {company}\nJD:\n{desc[:NG_DESC_CHARS]}"
    status, reason, evidence = "needs_review", "分类失败 (重试耗尽)", ""
    for attempt in range(4):
        _throttle_ng_call()
        if attempt > 0:
            time.sleep(5.0 * attempt)
        try:
            r = requests.post(GROQ_API_URL, headers=groq_headers, timeout=TIMEOUT, json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": NG_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            })
            if r.status_code == 429:
                if attempt < 3:
                    continue
                break
            r.raise_for_status()
            data = json.loads(r.json()["choices"][0]["message"]["content"])
            status = data.get("status", "needs_review")
            if status not in ("new_grad_friendly", "excluded", "needs_review"):
                status = "needs_review"
            reason, evidence = data.get("reason", ""), data.get("evidence", "")
            break
        except Exception as e:
            status, reason, evidence = "needs_review", f"分类失败 ({type(e).__name__})", ""
            break

    cache[job_id] = {"status": status, "reason": reason, "evidence": evidence}
    save_ng_cache(cache)
    return status, reason, evidence, False


NG_ORDER = {"new_grad_friendly": 0, "needs_review": 1, "excluded": 2}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--companies", default="companies.csv")
    ap.add_argument("--limit", type=int, default=None,
                     help="最多送几个命中岗位去做 LLM 打分, 用于验证效果/耗时")
    args = ap.parse_args()

    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        sys.exit("没有设置 GROQ_API_KEY——New Grad 分类要调 Groq API, "
                  "去 console.groq.com/keys 免费申请一个, 然后 export GROQ_API_KEY=...")
    groq_headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
    ng_cache = load_ng_cache()

    if not os.path.exists(args.companies):
        sys.exit(f"找不到 {args.companies}")
    with open(args.companies, encoding="utf-8-sig") as f:
        companies = [r for r in csv.DictReader(f) if r.get("slug", "").strip()]

    seen = set()
    if os.path.exists(SEEN_FILE) and not args.all:
        seen = set(json.load(open(SEEN_FILE)))

    results, unresolved = [], []
    n_classified, n_cache_hit, ng_elapsed = 0, 0, 0.0
    limit_hit = False
    for i, c in enumerate(companies, 1):
        if limit_hit:
            break
        name, ats, slug = c["company"].strip(), c.get("ats", "").strip(), c["slug"].strip()
        print(f"[{i}/{len(companies)}] {name} ...", end=" ", flush=True)
        try:
            jobs = FETCHERS[ats](slug) if ats in FETCHERS else []
        except Exception as e:
            print(f"失败 ({type(e).__name__})")
            unresolved.append(f"{name}\t{slug}\t{type(e).__name__}")
            continue

        kept = 0
        for j in jobs:
            if not title_ok(j["title"]) or not is_us(j["location"]):
                continue
            if j["job_id"] in seen:
                continue
            if args.limit is not None and n_classified >= args.limit:
                limit_hit = True
                break
            t0 = time.perf_counter()
            ng_status, ng_reason, ng_evidence, was_cached = classify_new_grad(
                groq_headers, j["job_id"], j["title"], name, j["desc"], ng_cache)
            ng_elapsed += time.perf_counter() - t0
            n_classified += 1
            n_cache_hit += was_cached
            j.update({"company": name, "priority": c.get("priority", ""),
                      "visa_flag": visa_flag(j["desc"]), "score": score(j),
                      "ng_status": ng_status, "ng_reason": ng_reason,
                      "ng_evidence": ng_evidence})
            results.append(j)
            kept += 1
        print(f"{len(jobs)} 个岗位 → {kept} 个命中")
        time.sleep(0.3)

    results.sort(key=lambda x: (NG_ORDER.get(x["ng_status"], 1), -x["score"], x["company"]))

    stamp = datetime.now().strftime("%Y-%m-%d")
    out_csv = f"jobs_{stamp}.csv"
    cols = ["score", "ng_status", "priority", "company", "title", "location", "posted",
            "visa_flag", "ng_reason", "ng_evidence", "url"]
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    if not args.all:
        seen.update(j["job_id"] for j in results)
        json.dump(sorted(seen), open(SEEN_FILE, "w"))

    if unresolved:
        with open("unresolved.txt", "w", encoding="utf-8") as f:
            f.write("公司\tslug\t原因\n" + "\n".join(unresolved))

    clean = [j for j in results if not j["visa_flag"]]
    ng_friendly = [j for j in results if j["ng_status"] == "new_grad_friendly"]
    ng_review = [j for j in results if j["ng_status"] == "needs_review"]
    ng_excluded = [j for j in results if j["ng_status"] == "excluded"]
    print(f"\n{'='*54}")
    print(f"新岗位         {len(results)}")
    print(f"无签证红旗     {len(clean)}")
    print(f"New Grad 友好  {len(ng_friendly)}   <- 优先看这些")
    print(f"需要人工确认   {len(ng_review)}")
    print(f"已排除         {len(ng_excluded)}")
    print(f"抓取失败       {len(unresolved)}")
    print(f"输出           {out_csv}")
    if args.limit is not None:
        print(f"--limit {args.limit}: LLM 打分 {n_classified} 条（缓存命中 {n_cache_hit}, "
              f"新调用 {n_classified - n_cache_hit}）, 耗时 {ng_elapsed:.1f} 秒")
    print("=" * 54)
    for j in (ng_friendly + ng_review)[:20]:
        tag = "NG" if j["ng_status"] == "new_grad_friendly" else "REVIEW"
        print(f"  {j['score']:>4}  [{tag:6}] {j['company'][:18]:<18} {j['title'][:40]}")
        print(f"          理由: {j['ng_reason']}")


if __name__ == "__main__":
    main()