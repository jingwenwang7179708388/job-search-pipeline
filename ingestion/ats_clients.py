"""
ingestion/ats_clients.py - one fetcher per ATS, returning every currently open posting

Copied from legacy/job_feed.py (new code imports fetchers only from here). Differences from
the legacy copies:
  - Every posting carries "external_id" (the ATS's own posting ID, no prefix).
  - No title/location filtering: classification happens later in processing/.
  - A fetch either returns the complete list or raises. In particular Workday's rate limiting
    (HTTP 200 with an empty/non-JSON body) raises FetchIncomplete instead of silently
    returning a partial list, because ingestion counts postings missing from a successful
    fetch toward closing them.
  - SmartRecruiters and Workday list APIs have no description; a per-posting detail request
    is made only for titles matching DESC_TITLES (data/ML/AI/analytics/research), not for the
    legacy new-grad title filter.

Each fetcher returns a list of dicts: external_id, title, location, url, posted, desc.
"""

import html
import re
import time
from datetime import datetime, timezone

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (job-agg DSCI 551 project)"}
TIMEOUT = 20
MAX_POSTINGS = 2000      # hard cap per company for paginated APIs

# Titles worth a per-posting detail request on APIs whose list has no description
DESC_TITLES = re.compile(
    r"data|machine learning|\bml\b|\bai\b|artificial intelligence|deep learning|"
    r"analy|scien|research|statistic|quant|computer vision|\bnlp\b|intelligence",
    re.I)


class FetchIncomplete(Exception):
    """The ATS stopped returning data part-way (e.g. Workday rate limiting)."""


def strip_html(text):
    if not text:
        return ""
    # Greenhouse "content" is HTML-escaped (&lt;p&gt;...), so unescape before stripping tags
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def fetch_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "external_id": str(j.get("id")),
        "title": j.get("title", ""),
        "location": (j.get("location") or {}).get("name", ""),
        "url": j.get("absolute_url", ""),
        # first_published is the first publication date; updated_at is only the last edit
        # time, so it is never used as a posting date
        "posted": (j.get("first_published") or "")[:10],
        "desc": strip_html(j.get("content", "")),
    } for j in r.json().get("jobs", [])]


def fetch_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        ts = j.get("createdAt")
        # createdAt is when the posting was created (the public API has no separate
        # publish date); it is the closest available first-posted date
        posted = (datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")
                  if ts else "")
        out.append({
            "external_id": str(j.get("id")),
            "title": j.get("text", ""),
            "location": cat.get("location", ""),
            "url": j.get("hostedUrl", ""),
            "posted": posted,
            "desc": _lever_desc(j),
        })
    return out


def _lever_desc(j):
    """Lever splits a posting into the description, lists (responsibilities, requirements -
    usually where years of experience are) and additional text (often work authorization /
    clearance). Join them so the JD is complete; parts are separated by newlines."""
    parts = [strip_html(j.get("descriptionPlain") or j.get("description", ""))]
    for lst in j.get("lists") or []:
        parts.append(strip_html(lst.get("text", "")))
        parts.append(strip_html(lst.get("content", "")))
    parts.append(strip_html(j.get("additionalPlain") or j.get("additional", "")))
    return "\n".join(p for p in parts if p)


def fetch_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "external_id": str(j.get("id")),
        "title": j.get("title", ""),
        "location": j.get("location", ""),
        "url": j.get("jobUrl", ""),
        "posted": (j.get("publishedAt") or "")[:10],
        "desc": strip_html(j.get("descriptionPlain") or j.get("descriptionHtml", "")),
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
                "external_id": str(j.get("id")),
                "title": title,
                "location": ", ".join(x for x in [loc.get("city"), loc.get("region"),
                                                  loc.get("country")] if x),
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                "posted": (j.get("releasedDate") or "")[:10],
                "desc": "",
            }
            if DESC_TITLES.search(title):
                try:
                    d2 = requests.get(
                        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j['id']}",
                        headers=HEADERS, timeout=TIMEOUT).json()
                    secs = (d2.get("jobAd") or {}).get("sections") or {}
                    rec["desc"] = strip_html(" ".join(
                        str(v.get("text", "")) for v in secs.values() if isinstance(v, dict)))
                except (requests.exceptions.RequestException, ValueError):
                    pass   # keep the posting; only its description is missing
            out.append(rec)
        offset += len(items)
        if offset >= d.get("totalFound", 0) or offset >= MAX_POSTINGS:
            break
    return out


def fetch_recruitee(slug):
    r = requests.get(f"https://{slug}.recruitee.com/api/offers/",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "external_id": str(j.get("id")),
        "title": j.get("title", ""),
        "location": j.get("location") or j.get("city", ""),
        "url": j.get("careers_url") or j.get("careers_apply_url", ""),
        "posted": (j.get("published_at") or "")[:10],
        "desc": strip_html(j.get("description", "")),
    } for j in r.json().get("offers", [])]


def fetch_workable(slug):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
        out.append({
            "external_id": str(j.get("shortcode")),
            "title": j.get("title", ""),
            "location": loc or ("Remote" if j.get("telecommuting") else ""),
            "url": j.get("url") or j.get("shortlink", ""),
            "posted": (j.get("published_on") or "")[:10],
            "desc": strip_html(j.get("description", "")),
        })
    return out


_WD_N_LOCATIONS = re.compile(r"^\d+\s+locations?$", re.I)


def _workday_path_location(path):
    """The list API shows "N Locations" for multi-location postings; the detail-page URL
    encodes a real location (e.g. /job/Taichung---Fab-16-Taiwan/...)."""
    m = re.match(r"^/job/([^/]+)/", path)
    return re.sub(r"-+", " ", m.group(1)).strip() if m else ""


def fetch_workday(slug):
    tenant, wd, site = slug.split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    list_url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out, seen_ids, offset, page, total = [], set(), 0, 0, None
    while True:
        # Paging one tenant too fast triggers rate limiting (HTTP 200 with an empty body), so
        # pause between pages; no wait before the first page.
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
            raise FetchIncomplete(f"workday page at offset {offset} failed after 3 attempts")
        page += 1
        if total is None:
            total = d.get("total", 0)
        postings = d.get("jobPostings", [])
        if not postings:
            if total and offset < total:
                raise FetchIncomplete(f"workday returned no postings at offset {offset} of {total}")
            break
        for j in postings:
            title = j.get("title", "")
            path = j.get("externalPath", "")
            req_id = (j.get("bulletFields") or [None])[0] or path
            if req_id in seen_ids:
                continue
            seen_ids.add(req_id)
            loc = j.get("locationsText", "")
            if not loc or _WD_N_LOCATIONS.match(loc.strip()):
                loc = _workday_path_location(path) or loc
            rec = {
                "external_id": str(req_id),
                "title": title,
                "location": loc,
                "url": f"{base}/{site}{path}",
                "posted": "",
                "desc": "",
            }
            if DESC_TITLES.search(title):
                try:
                    d2 = requests.get(f"{base}/wday/cxs/{tenant}/{site}{path}",
                                      headers=HEADERS, timeout=TIMEOUT).json()
                    info = d2.get("jobPostingInfo") or {}
                    rec["desc"] = strip_html(info.get("jobDescription", ""))
                    rec["posted"] = (info.get("startDate") or "")[:10]
                except (requests.exceptions.RequestException, ValueError):
                    pass   # keep the posting; only its description is missing
            out.append(rec)
        offset += len(postings)
        # Some tenants repeat the last page instead of returning an empty one past the total,
        # so stop on the total from the first page; req_id dedup backstops repeated pages.
        if (total and offset >= total) or offset >= MAX_POSTINGS:
            break
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "smartrecruiters": fetch_smartrecruiters, "recruitee": fetch_recruitee,
            "workday": fetch_workday, "workable": fetch_workable}
