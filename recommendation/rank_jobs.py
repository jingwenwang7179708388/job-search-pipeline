#!/usr/bin/env python3
"""
recommendation/rank_jobs.py - rule-based Top 20 for one user, written to recommendations

Hard filters (a job failing any of them is never recommended):
  status = active; missing_run_count = 0 (the job appeared in its source's latest successful
  fetch: a job missing from it is probably taken down even before it is closed);
  role_family in the user's target_tracks; global_eligible and
  visa_evidence != Blocked; seniority != senior; seniority != intern unless --include-interns;
  not a foreign-only location; date = COALESCE(posted_at, first_seen_at) inside the window:
  48 hours first, 30 days when fewer than TOP_N jobs can be picked from the 48-hour pool.

Score (0-100):
  track       25  the Track is one of the user's (always true after the filters)
  skills      25  formula B': J = job skills without "machine learning" (a domain, not a tool),
                  25 * |user skills & J| / |J|; 12.5 when J is empty
  experience  15  min_years <= user years -> 15, one more -> 10, two more -> 5, more -> 0;
                  no min_years: entry 15, unknown / intern 8, mid 5
  location    10  matches a preferred location 10; only "United States" / "N/A" / empty 5;
                  elsewhere in the US 0
  freshness   10  age of the date <= 48 hours 10, then linear down to 0 at 30 days
  visa        15  Likely 15, Unclear 7, Unlikely 0
At most MAX_PER_COMPANY jobs per company. Ties: higher visa score, newer date, lower job_id.
posted_at is a date and is taken as midnight. needs_sponsorship is not used beyond the visa
filters and score (the test user needs sponsorship).

Writes up to TOP_N rows (fewer when there are fewer candidates, never padded) to
recommendations with one new batch_id (uuid4), rank 1-N, total_score and score_breakdown (each
component, the window used, matched skills, location class, date used). Old batches are kept.
--dry-run prints the funnel, the Top N and the next N and writes nothing.

Usage (from the project root):
  python3 recommendation/rank_jobs.py --user-id 1 [--include-interns] [--dry-run]
"""

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common.db import get_conn          # noqa: E402

TOP_N = 20                  # recommendations.rank_position CHECK allows 1-20
MAX_PER_COMPANY = 2
WINDOWS = [("48h", timedelta(hours=48)), ("30d", timedelta(days=30))]
FRESH_FULL = timedelta(hours=48)
FRESH_ZERO = timedelta(days=30)
W_TRACK, W_SKILLS, W_EXP, W_LOC, W_FRESH, W_VISA = 25, 25, 15, 10, 10, 15
VISA_SCORE = {"Likely": 15, "Unclear": 7, "Unlikely": 0}
NOT_A_TOOL = {"machine learning"}

# ------------------------------------------------ location
FOREIGN_CODES = re.compile(r"\b(?:ON|BC|QC|UK|IE|CH)\b")      # case-sensitive: not "on"
FOREIGN = re.compile(
    r"\b(?:canada|ontario|toronto|vancouver|montreal|quebec|united kingdom|england|"
    r"london|oxford|scotland|ireland|dublin|zurich|switzerland|germany|berlin|"
    r"munich|france|paris|netherlands|amsterdam|spain|madrid|poland|warsaw|sweden|stockholm|"
    r"denmark|norway|finland|italy|portugal|lisbon|israel|tel aviv|india|bengaluru|bangalore|"
    r"hyderabad|pune|mumbai|delhi|china|beijing|shanghai|hong kong|taiwan|taipei|hsinchu|"
    r"singapore|japan|tokyo|korea|seoul|australia|sydney|melbourne|new zealand|brazil|"
    r"sao paulo|mexico|argentina|colombia|chile|philippines|vietnam|indonesia|uae|dubai)\b", re.I)
CA_PLACES = re.compile(r"california|san francisco|bay area|mountain ?view|palo alto|foster city|"
                       r"menlo park|sunnyvale|san jose|santa clara|redwood city|oakland|"
                       r"los angeles|san diego|irvine|\bUS-SF\b", re.I)
PLACE_RE = {   # preferred location token -> segment test
    "CA": lambda s: bool(re.search(r"\bCA\b", s) or CA_PLACES.search(s)),
    "NY": lambda s: bool(re.search(r"\bNY\b|\bNYC\b|new york", s, re.I)),
    "REMOTE": lambda s: bool(re.search(r"remote", s, re.I)),
}
US_ONLY = {"", "N/A", "NA", "US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}


def segments(loc):
    return [s.strip() for s in re.split(r";|\bOR\b|:|(?<=\w), (?=US-)", loc or "") if s.strip()]


def location_class(loc, preferred):
    """-> match / us_unknown / us_other / foreign"""
    segs = segments(loc)
    if all(s.upper().rstrip(".") in US_ONLY for s in segs):
        return "us_unknown"
    tests = [PLACE_RE.get(p.upper(), lambda s, p=p: bool(re.search(rf"\b{re.escape(p)}\b", s)))
             for p in preferred]
    us_other = False
    for s in segs:
        if FOREIGN.search(s) or FOREIGN_CODES.search(s):
            continue
        if any(t(s) for t in tests):
            return "match"
        if s.upper().rstrip(".") not in US_ONLY:
            us_other = True
    if us_other:
        return "us_other"
    return "us_unknown" if any(s.upper().rstrip(".") in US_ONLY for s in segs) else "foreign"


LOC_SCORE = {"match": W_LOC, "us_unknown": W_LOC / 2, "us_other": 0}


# ------------------------------------------------ scores
def skills_score(user_skills, job_skills):
    j = set(job_skills) - NOT_A_TOOL
    matched = sorted(set(user_skills) & j)
    return (W_SKILLS * len(matched) / len(j) if j else W_SKILLS / 2), matched


def experience_score(user_years, min_years, seniority):
    if min_years is not None:
        gap = min_years - user_years
        return W_EXP if gap <= 0 else 10 if gap == 1 else 5 if gap == 2 else 0
    return {"entry": W_EXP, "mid": 5}.get(seniority, 8)        # unknown / intern -> 8


def freshness_score(age):
    if age <= FRESH_FULL:
        return W_FRESH
    if age >= FRESH_ZERO:
        return 0.0
    return W_FRESH * (FRESH_ZERO - age) / (FRESH_ZERO - FRESH_FULL)


def load_user(cur, user_id):
    cur.execute("SELECT name, target_tracks, skills, years_experience, preferred_locations "
                "FROM users WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        sys.exit(f"No user with user_id {user_id}")
    name, tracks, skills, years, locs = row
    return {"name": name, "tracks": json.loads(tracks), "skills": [s.lower() for s in json.loads(skills)],
            "years": int(years), "locations": json.loads(locs)}


def load_jobs(cur):
    cur.execute(
        "SELECT j.job_id, j.company_id, c.company_name, j.title, j.location_text, j.status, "
        "j.posted_at, j.first_seen_at, j.missing_run_count, f.role_family, f.global_eligible, "
        "f.visa_evidence, f.seniority, f.min_years_experience, f.skills "
        "FROM jobs j JOIN job_features f USING (job_id) JOIN companies c ON c.company_id = j.company_id")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def pick(scored):
    """Greedy by score with the per-company cap -> (picked in order, skipped by the cap)."""
    order = sorted(scored, key=lambda x: (-x["total"], -x["visa"], -x["date"].timestamp(), x["job_id"]))
    picked, skipped, per_co = [], [], {}
    for x in order:
        if per_co.get(x["company_id"], 0) >= MAX_PER_COMPANY:
            skipped.append(x)
            continue
        per_co[x["company_id"]] = per_co.get(x["company_id"], 0) + 1
        picked.append(x)
    return picked, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--user-id", type=int, required=True)
    ap.add_argument("--include-interns", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = get_conn()
    try:
        cur = conn.cursor()
        user = load_user(cur, args.user_id)
        jobs = load_jobs(cur)
        cur.execute("SELECT NOW()")
        now = cur.fetchone()[0]

        for j in jobs:
            j["date"] = (datetime.combine(j["posted_at"], time()) if j["posted_at"]
                         else j["first_seen_at"])
            j["date_source"] = "posted_at" if j["posted_at"] else "first_seen_at"
            j["loc_class"] = location_class(j["location_text"], user["locations"])
        filters = [
            ("status = active", lambda j: j["status"] == "active"),
            ("missing_run_count = 0 (in the latest fetch)", lambda j: j["missing_run_count"] == 0),
            ("Track in the user's Tracks", lambda j: j["role_family"] in user["tracks"]),
            ("global_eligible and not Blocked",
             lambda j: j["global_eligible"] and j["visa_evidence"] != "Blocked"),
            ("not senior", lambda j: j["seniority"] != "senior"),
            ("not intern" + (" (skipped: --include-interns)" if args.include_interns else ""),
             lambda j: args.include_interns or j["seniority"] != "intern"),
            ("not a foreign location", lambda j: j["loc_class"] != "foreign"),
        ]
        pool, funnel = jobs, [("jobs with job_features", len(jobs))]
        for name, f in filters:
            pool = [j for j in pool if f(j)]
            funnel.append((name, len(pool)))

        window = picked = skipped = None
        for wname, span in WINDOWS:
            in_window = [j for j in pool if now - j["date"] <= span]
            funnel.append((f"date within {wname}", len(in_window)))
            scored = []
            for j in in_window:
                sk, matched = skills_score(user["skills"], json.loads(j["skills"] or "[]"))
                x = dict(j, track=W_TRACK, skills_pts=sk, matched=matched,
                         exp=experience_score(user["years"], j["min_years_experience"], j["seniority"]),
                         loc=LOC_SCORE[j["loc_class"]], fresh=freshness_score(now - j["date"]),
                         visa=VISA_SCORE[j["visa_evidence"]])
                x["total"] = round(x["track"] + x["skills_pts"] + x["exp"] + x["loc"] + x["fresh"]
                                   + x["visa"], 2)
                scored.append(x)
            window = wname
            picked, skipped = pick(scored)
            if len(picked) >= TOP_N:
                break

        print(f"User {args.user_id}: {user['name']}  Tracks {user['tracks']}  skills {user['skills']}  "
              f"{user['years']} yr  locations {user['locations']}")
        print("\nHard filters:")
        for name, n in funnel:
            print(f"  {name:<46}{n:>6,}")
        print(f"Window used: {window}  ({len(picked)} pickable after the company cap, "
              f"{len(skipped)} skipped by the cap)")

        top = picked[:TOP_N]
        print(f"\nTop {len(top)}:")
        for i, x in enumerate(top, 1):
            print(f"{i:>2}. {x['total']:6.2f}  {x['company_name'][:22]:<22} {x['title'].strip()[:60]}")
            print(f"      {(x['location_text'] or '').strip()[:60]} [{x['loc_class']}]  "
                  f"{x['date_source']} {x['date']:%Y-%m-%d}  {x['seniority']}  "
                  f"years={x['min_years_experience']}  {x['visa_evidence']}")
            print(f"      track {x['track']}  skills {x['skills_pts']:.1f} {x['matched']}  exp {x['exp']}  "
                  f"loc {x['loc']:g}  fresh {x['fresh']:.1f}  visa {x['visa']}")
        print(f"\nRanks {TOP_N + 1}-{2 * TOP_N} (same cap):")
        for i, x in enumerate(picked[TOP_N:2 * TOP_N], TOP_N + 1):
            print(f"{i:>2}. {x['total']:6.2f}  {x['company_name'][:22]:<22} {x['title'].strip()[:55]:<55} "
                  f"{x['seniority']:<7} y={x['min_years_experience']}  {x['visa_evidence']}")
        if skipped:
            print("\nHighest scores skipped by the company cap:")
            for x in skipped[:5]:
                print(f"    {x['total']:6.2f}  {x['company_name'][:22]:<22} {x['title'].strip()[:60]}")

        if args.dry_run or not top:
            print("\nDry run: nothing written." if args.dry_run else "\nNo candidates: nothing written.")
            return
        batch = str(uuid.uuid4())
        for i, x in enumerate(top, 1):
            breakdown = {"track": x["track"], "skills": round(x["skills_pts"], 2), "experience": x["exp"],
                         "location": x["loc"], "freshness": round(x["fresh"], 2), "visa": x["visa"],
                         "window": window, "matched_skills": x["matched"],
                         "location_class": x["loc_class"], "date_used": x["date_source"],
                         "date": f"{x['date']:%Y-%m-%d}"}
            cur.execute("INSERT INTO recommendations (user_id, job_id, batch_id, rank_position, "
                        "total_score, score_breakdown) VALUES (%s, %s, %s, %s, %s, %s)",
                        (args.user_id, x["job_id"], batch, i, x["total"], json.dumps(breakdown)))
        conn.commit()
        print(f"\nWrote {len(top)} rows to recommendations, batch_id {batch}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
