#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job_feed.py - daily job posting aggregator
Usage: python3 job_feed.py          only postings that are new since the last run
       python3 job_feed.py --all    all currently open postings
       python3 job_feed.py --limit 20   send only the first 20 matches to the LLM (to check quality/latency)
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

# ------------------------------------------------ Candidate profile: edit here to change the filtering

# Candidate info used by the new-grad classifier; change these two lines for a new person or term
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

# Matched as whole words (see title_ok), so there is no need for padded entries like "ii "/"vp "
# to fake a word boundary. Those missed "Research Scientist II" (II at the end),
# "Data Scientist II," and "Sr Data Scientist" (no period).
TITLE_EXCLUDE = [
    "senior", "sr", "staff", "principal", "lead", "manager", "director",
    "head of", "vp", "vice president", "architect", "ii", "iii", "iv",
    "2", "3", "4", "5", "mid level", "mid-level",
    "distinguished", "fellow",
    # Full-time new-grad roles only: no internships or co-ops
    "intern", "interns", "internship", "internships", "co-op", "coop", "werkstudent", "praktikum",
    # "research scientist/engineer" and "geospatial" are broad and pull in non-DS/ML roles
    "linguist", "chemistry", "clinical", "biology",
]

BOOST = {
    "new grad": 30, "new college grad": 30, "university grad": 30,
    "early career": 25, "entry level": 25,
    "2027": 25, "campus": 20, "graduate": 10,
    "llm": 12, "nlp": 8, "recommendation": 8, "forecasting": 8,
    "remote sensing": 20, "satellite": 20, "geospatial": 20, "computer vision": 12,
    "quantitative": 12, "pytorch": 8,
}

VISA_RED_FLAGS = [
    "will not sponsor", "unable to sponsor", "do not sponsor",
    "not able to sponsor", "no visa sponsorship", "without sponsorship",
    "not provide sponsorship", "without the need for sponsorship",
    "us citizen", "u.s. citizen", "security clearance", "green card holder only",
    "ts/sci", "top secret", "secret clearance", "active clearance",
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
    # Full state names as a fallback: the original list only covered a few big metros and the
    # Bay Area, but Workday locations are often smaller cities ("Charlotte-NC") that got dropped.
    "alabama", "alaska", "arizona", "arkansas", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "north carolina",
    "north dakota", "ohio", "oklahoma", "rhode island", "south carolina",
    "south dakota", "tennessee", "utah", "vermont", "virginia",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
    # Workday usually gives a two-letter state code rather than the full name ("Charlotte-NC"),
    # so add the codes. Skip or/hi/in/ok/co/ma/mo/oh (Oregon, Hawaii, Indiana, Oklahoma,
    # Colorado, Massachusetts, Missouri, Ohio): they collide with common English words.
    # Those states are covered by city names instead (Boston and Denver are already above).
    "al", "ak", "az", "ar", "ct", "de", "fl", "ga", "ia", "id", "il",
    "ks", "ky", "la", "md", "me", "mi", "mn", "ms", "mt", "nc", "nd",
    "ne", "nh", "nj", "nm", "nv", "pa", "ri", "sc", "sd", "tn", "tx",
    "ut", "va", "vt", "wi", "wv", "wy",
    "portland", "indianapolis", "columbus", "cleveland", "cincinnati",
    "oklahoma city", "tulsa", "kansas city", "st. louis", "honolulu",
]

# Short codes like ca/ny/sf collide with substrings (the "ca" in "location") under plain
# substring matching, so bare codes use word-boundary matching and longer phrases use substrings.
def _kw_hit(loc, kw):
    if re.fullmatch(r"[a-z]+", kw):
        return re.search(rf"\b{kw}\b", loc) is not None
    return kw in loc

# ------------------------------------------------

HEADERS = {"User-Agent": "Mozilla/5.0 (job-feed personal script)"}
TIMEOUT = 20
SEEN_FILE = "seen_jobs.json"
NG_CACHE_FILE = "ng_cache.json"  # job_id -> {status, reason, evidence}; classified jobs are not re-sent
NG_DESC_CHARS = 1500  # max length of the JD excerpt sent to the LLM, to save tokens (see ng_excerpt)
NG_HEAD_CHARS = 300  # opening of the JD always kept in the excerpt, for role context
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"  # change this line to switch models or speed
# Groq free-tier limits vary by model; lower this value if 429s are frequent. gpt-oss-120b is
# capped at 8,000 tokens per minute and one call uses ~1,200, so tokens bind before requests do.
NG_RATE_LIMIT_RPM = 5
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
    # Greenhouse "content" is HTML-escaped (&lt;p&gt;...), so unescape before stripping tags;
    # otherwise the tags stay in the text and waste the LLM's character budget.
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def fetch_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "title": j.get("title", ""),
        "location": (j.get("location") or {}).get("name", ""),
        "url": j.get("absolute_url", ""),
        # updated_at is the last edit time, so an edited old posting would wrongly get the freshness boost
        "posted": (j.get("first_published") or j.get("updated_at") or "")[:10],
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
            # country is a lowercase ISO-2 code; appended to location, "de" (Germany) would be
            # read by is_us as Delaware, so filter on the explicit country code here instead.
            if (loc.get("country") or "us").lower() != "us":
                continue
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
    """Parse the real location from the posting's detail-page URL.

    For multi-location postings the list API only returns a placeholder like "2 Locations",
    which hides whether the role is in the US (in practice: Singapore, China, Taiwan...). The
    detail URL encodes the specific location (e.g. /job/Taichung---Fab-16-Taiwan/...).
    """
    m = re.match(r"^/job/([^/]+)/", path)
    return re.sub(r"-+", " ", m.group(1)).strip() if m else ""


def fetch_workday(slug):
    tenant, wd, site = slug.split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    list_url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out, seen_ids, offset, page, total = [], set(), 0, 0, None
    while True:
        # Paging one tenant too fast triggers rate limiting: HTTP 200 with an empty body, not a
        # real end of data. Pausing up front is more reliable than backing off after a failure
        # (the cooldown after a failure is much longer). No wait before the first page.
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
        # Some tenants (e.g. phreesia) do not return an empty jobPostings past the real total;
        # they repeat the last page, so "is this page empty" cannot be the only stop condition
        # (it would loop until the hard cap). Stop on the total read from the first page;
        # job_id dedup is the backstop for repeated pages, and the hard cap covers a missing total.
        if total and offset >= total:
            break
        if offset > 2000:
            break
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "smartrecruiters": fetch_smartrecruiters, "recruitee": fetch_recruitee,
            "workday": fetch_workday, "workable": fetch_workable}

def _word_hit(text, kw):
    return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text) is not None


def title_ok(title):
    t = title.lower()
    return (any(k in t for k in TITLE_INCLUDE)
            and not any(_word_hit(t, k) for k in TITLE_EXCLUDE))


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
              # Workday remote postings often mark the country with an ISO-3 code ("Remote-IND");
              # short codes use _kw_hit word-boundary matching, so they do not hit "Indianapolis"
              "ind", "deu", "gbr", "twn", "chn", "sgp", "aus", "jpn", "kor",
              "fra", "esp", "ita", "nld", "pol", "bra", "irl", "isr",
              "phl", "vnm", "tha", "idn", "mys", "nzl", "che", "arg", "col",
              "chl", "zaf", "rus", "ukr"]
    if any(_kw_hit(loc, k) for k in non_us):
        return False
    return any(_kw_hit(loc, k) for k in US_HINTS)


def visa_flag(title, desc):
    d = (title + " " + desc).lower()
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


NG_SYSTEM_PROMPT = f"""You are helping a job seeker decide whether a posting is new-grad friendly.

Candidate background: graduating {GRAD_TERM}, currently pursuing a {GRAD_DEGREE}, no full-time work experience (internships/co-ops aside).

Exclude (excluded) if ANY of the following holds:
1. The JD says "X+ years of experience" or "X-Y years" with X>=1, and does not say that
   internship/co-op time can count toward it
2. The JD requires both years of experience ("X+ years") and "Master's/PhD required"; this
   combination is almost never a campus role, whatever the title says
3. The title or level includes Senior/Staff/Principal/Lead/II/III/Manager/Director
4. Internship/co-op/part-time student roles; the candidate only wants full-time roles after graduation
5. A degree requirement the candidate cannot meet: PhD only (the title or body says PhD required /
   PhD New Grad, and a Master's is not also accepted)

New-grad friendly (new_grad_friendly) if ANY of the following holds:
1. The title or category includes "New Grad"/"University"/"Graduate"/"Early Career"/"Rotational"
2. The JD states a graduation window that matches the candidate's {GRAD_TERM} (any overlap counts)
3. The requirements say "0-2 years"/"no prior professional experience required"/
   "currently pursuing a degree" or similar

When an exclusion rule and a new-grad rule both apply (e.g. the title says New Grad but the body
says 3+ years), the exclusion rule wins.

If the JD does not state experience requirements clearly, or is vague (e.g. "some experience preferred"
with no number of years), do not exclude: return needs_review and quote the triggering sentence in evidence.

Note: you only see the title and the JD text, so you cannot tell whether the posting comes from a
dedicated University/Campus careers page. Do not consider that signal.

Note: the JD you receive is an excerpt, not the full text: a short opening plus every passage that
mentions years of experience, degrees, graduation dates or new grads, joined by " … ". If the excerpt
has no years requirement, the full JD has none either; do not return needs_review because "the JD is truncated".

Output only JSON in this form, with no other text:
{{"status": "new_grad_friendly" or "excluded" or "needs_review", "reason": "one-sentence reason in English", "evidence": "the JD sentence supporting the decision, or an empty string"}}
"""


# A JD usually opens with company boilerplate and puts years/degree requirements later; with only
# the first N characters the LLM sees no evidence and returns needs_review for everything.
# Extract the relevant passages by keyword instead.
_NG_SIGNAL = re.compile(
    r"\byears?\b|\byrs?\b|experience|degree|bachelor|master|\bm\.?s\b|\bb\.?s\b|ph\.?d|"
    r"doctora|graduat|new grad|recent grad|university|college|student|enrolled|"
    r"pursuing|early.career|entry.level|\\binterns?(?:hips?)?\\b|co-op|20(?:2[5-9]|30)", re.I)
NG_WINDOW = 140  # characters kept on each side of every match


def ng_excerpt(desc):
    if len(desc) <= NG_DESC_CHARS:
        return desc
    spans = []
    for m in _NG_SIGNAL.finditer(desc, NG_HEAD_CHARS):
        a, b = max(NG_HEAD_CHARS, m.start() - NG_WINDOW), min(len(desc), m.end() + NG_WINDOW)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    out = desc[:NG_HEAD_CHARS]
    for a, b in spans:
        if len(out) + (b - a) > NG_DESC_CHARS:
            out += " … " + desc[a:a + max(0, NG_DESC_CHARS - len(out))]
            break
        out += " … " + desc[a:b]
    return out


def load_ng_cache():
    if os.path.exists(NG_CACHE_FILE):
        return json.load(open(NG_CACHE_FILE, encoding="utf-8"))
    return {}


def save_ng_cache(cache):
    json.dump(cache, open(NG_CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)


NG_FAILED_PREFIX = "classification failed"


def _groq_error(r):
    try:
        return r.json()["error"]["message"]
    except Exception:
        return r.text[:200]


def check_groq(groq_headers):
    """Probe the API once before the run. A bad key or a retired model would fail identically for
    every posting, so stop with a clear message instead of writing a CSV full of failures."""
    try:
        r = requests.post(GROQ_API_URL, headers=groq_headers, timeout=TIMEOUT, json={
            "model": GROQ_MODEL, "max_completion_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        })
    except requests.exceptions.RequestException as e:
        sys.exit(f"Cannot reach the Groq API ({type(e).__name__}); check the network and retry")
    if r.status_code == 401:
        sys.exit(f"Invalid GROQ_API_KEY (401: {_groq_error(r)}): check that the key was pasted "
                  "correctly at console.groq.com/keys")
    if r.status_code == 404:
        sys.exit(f"Model {GROQ_MODEL} is unavailable (404: {_groq_error(r)}): "
                  "change GROQ_MODEL at the top of this file; see console.groq.com/docs/models")
    if r.status_code not in (200, 429):
        sys.exit(f"Groq API self-check failed ({r.status_code}: {_groq_error(r)})")


def classify_new_grad(groq_headers, job_id, title, company, desc, cache):
    if job_id in cache:
        c = cache[job_id]
        return c["status"], c["reason"], c["evidence"], True

    # The Groq free tier is tight and backing off after a 429 is not enough, so a global throttle
    # keeps calls within NG_RATE_LIMIT_RPM (same idea as the Workday pacing in fetch_workday);
    # on a 429 we additionally wait for the retry-after given in the response headers.
    user_msg = f"Title: {title}\nCompany: {company}\nJD excerpt:\n{ng_excerpt(desc)}"
    status, reason, evidence = "needs_review", f"{NG_FAILED_PREFIX} (retries exhausted)", ""
    for attempt in range(4):
        _throttle_ng_call()
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
                reason = f"{NG_FAILED_PREFIX} (429 rate limited, retries exhausted)"
                try:
                    wait = float(r.headers.get("retry-after", ""))
                except ValueError:
                    wait = 10.0 * (attempt + 1)
                time.sleep(min(wait + 1, 90))
                continue
            if not r.ok:
                reason = f"{NG_FAILED_PREFIX} (HTTP {r.status_code}: {_groq_error(r)[:80]})"
                break
            data = json.loads(r.json()["choices"][0]["message"]["content"])
            status = data.get("status", "needs_review")
            if status not in ("new_grad_friendly", "excluded", "needs_review"):
                status = "needs_review"
            reason, evidence = data.get("reason", ""), data.get("evidence", "")
            break
        except Exception as e:
            reason = f"{NG_FAILED_PREFIX} ({type(e).__name__})"
            break

    # Failures are not cached. They used to be, and one misconfigured key pinned every posting
    # to "classification failed" permanently, even after the key was fixed.
    if not reason.startswith(NG_FAILED_PREFIX):
        cache[job_id] = {"status": status, "reason": reason, "evidence": evidence}
        save_ng_cache(cache)
    return status, reason, evidence, False


NG_ORDER = {"new_grad_friendly": 0, "needs_review": 1, "excluded": 2}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--companies", default="companies.csv")
    ap.add_argument("--limit", type=int, default=None,
                     help="max number of matched postings to send to the LLM, to check quality/latency")
    args = ap.parse_args()

    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        sys.exit("GROQ_API_KEY is not set. New-grad classification calls the Groq API: "
                  "get a free key at console.groq.com/keys, then export GROQ_API_KEY=...")
    groq_headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
    ng_cache = load_ng_cache()

    if not os.path.exists(args.companies):
        sys.exit(f"Cannot find {args.companies}")
    check_groq(groq_headers)
    with open(args.companies, encoding="utf-8-sig") as f:
        companies = [r for r in csv.DictReader(f) if r.get("slug", "").strip()]

    seen = set()
    if os.path.exists(SEEN_FILE) and not args.all:
        seen = set(json.load(open(SEEN_FILE)))

    results, unresolved = [], []
    emitted = set()  # when one slug is listed under several company names, emit each posting once
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
            print(f"failed ({type(e).__name__})")
            unresolved.append(f"{name}\t{slug}\t{type(e).__name__}")
            continue

        kept = 0
        for j in jobs:
            if not title_ok(j["title"]) or not is_us(j["location"]):
                continue
            if j["job_id"] in seen or j["job_id"] in emitted:
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
                      "visa_flag": visa_flag(j["title"], j["desc"]), "score": score(j),
                      "ng_status": ng_status, "ng_reason": ng_reason,
                      "ng_evidence": ng_evidence})
            results.append(j)
            emitted.add(j["job_id"])
            kept += 1
        print(f"{len(jobs)} postings -> {kept} matched")
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
        # Failed classifications are not added to seen, so they come back for a retry on the next run
        seen.update(j["job_id"] for j in results
                    if not j["ng_reason"].startswith(NG_FAILED_PREFIX))
        json.dump(sorted(seen), open(SEEN_FILE, "w"))

    if unresolved:
        with open("unresolved.txt", "w", encoding="utf-8") as f:
            f.write("company\tslug\treason\n" + "\n".join(unresolved))

    clean = [j for j in results if not j["visa_flag"]]
    ng_friendly = [j for j in results if j["ng_status"] == "new_grad_friendly"]
    ng_review = [j for j in results if j["ng_status"] == "needs_review"]
    ng_excluded = [j for j in results if j["ng_status"] == "excluded"]
    ng_failed = [j for j in results if j["ng_reason"].startswith(NG_FAILED_PREFIX)]
    print(f"\n{'='*54}")
    print(f"New postings       {len(results)}")
    print(f"No visa red flag   {len(clean)}")
    print(f"New-grad friendly  {len(ng_friendly)}   <- look at these first")
    print(f"Needs review       {len(ng_review)}")
    print(f"Excluded           {len(ng_excluded)}")
    if ng_failed:
        print(f"Classify failed    {len(ng_failed)}   <- retried on the next run")
    print(f"Fetch failed       {len(unresolved)}")
    print(f"Output             {out_csv}")
    if args.limit is not None:
        print(f"--limit {args.limit}: {n_classified} sent to the LLM ({n_cache_hit} cache hits, "
              f"{n_classified - n_cache_hit} new calls), {ng_elapsed:.1f} s")
    print("=" * 54)
    for j in (ng_friendly + ng_review)[:20]:
        tag = "NG" if j["ng_status"] == "new_grad_friendly" else "REVIEW"
        print(f"  {j['score']:>4}  [{tag:6}] {j['company'][:18]:<18} {j['title'][:40]}")
        print(f"          reason: {j['ng_reason']}")


if __name__ == "__main__":
    main()