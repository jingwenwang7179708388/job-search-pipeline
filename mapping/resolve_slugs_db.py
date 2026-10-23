#!/usr/bin/env python3
"""
mapping/resolve_slugs_db.py - guess ATS slugs for companies in MySQL that have no ats_sources row

Candidates: companies not in ats_sources, ranked by the sum of non_senior_filing_count over their
non-SOFTWARE_ENGINEERING Tracks (companies that only sponsor SWE are not added; see docs/PLAN.md),
minus IT staffing / outsourcing names matched by STAFFING (here only: common/names.py BODYSHOPS
is not changed, since that would change the LCA evidence and the frozen Track experiments).
Ranks are counted after that exclusion.
Slug guesses (candidates()) and the job-count probes for Greenhouse, Lever, Ashby,
SmartRecruiters, Recruitee and Workable are reused from mapping/resolve_slugs.py (Workday is not
probed). The first slug x platform with enough open postings is the hit (SmartRecruiters and
Recruitee need >= 5, others >= 1).

Every hit is then checked, since guessed slugs often belong to another company:
  - the board's own company name is fetched (Greenhouse /v1/boards/{slug}, Workable account,
    SmartRecruiters posting company, Recruitee offer company_name, Lever / Ashby page <title>)
    and compared with ours after removing legal suffixes (and "&" / "and"): the names must then
    be identical - a match that needs any other word dropped ("Savvy IT Group" vs "Savvy",
    "Microsoft" vs "Microsoft AI") is not a match;
  - generic-word or very short slugs (general, national, apex, insurance, ...) are suspect;
  - slugs rejected before (mapping/ats_slug_review.csv) or already in ats_sources are flagged.
confidence = high only when the names match and none of the flags apply, else low (reason given).

Read-only for MySQL. Writes hits to a CSV (default experiments/slug_candidates.csv).

Usage (from the project root):
  python3 mapping/resolve_slugs_db.py --limit 200 [--offset 0] [--out experiments/....csv]
  python3 mapping/resolve_slugs_db.py --offset 999 --limit 4001 --sample 200 --seed 551 --out ...
      (a fixed-seed random sample of 200 from candidate ranks 1,000-5,000)
  python3 mapping/resolve_slugs_db.py --limit 5000 --skip-ids probed.txt --merge a.csv b.csv --out ...
      (companies listed in probed.txt are not probed again; their hits are taken from the merged
      CSVs, re-judged and re-ranked)
  python3 mapping/resolve_slugs_db.py --load-csv experiments/slug_candidates_top5000.csv
      load a hits CSV into ats_sources: high -> is_active TRUE, low -> FALSE, discovered_by
      'resolve_slugs_db'; skipped: slugs already in ats_sources, slugs rejected before, and a
      repeated (ats, slug) within the CSV (the better-ranked company keeps it)
"""

import argparse
import csv
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from common.db import get_conn                                   # noqa: E402
from resolve_slugs import HEADERS, PROBES, TIMEOUT, WORKERS, candidates   # noqa: E402

REVIEW_CSV = HERE / "ats_slug_review.csv"
# IT staffing / outsourcing firms: substrings of the lowercased normalized_name. " it ..." keeps
# its leading space so "credit services" / "benefit solutions" do not match.
STAFFING = ["people tech", "iris software", "alpha net consulting", "first tek", "aspire systems",
            "savvy it", " it solutions", " it services", "tech solutions", "info systems",
            "technosoft", "infotek", "softtech", "softsol"]
MIN_JOBS = {"smartrecruiters": 5, "recruitee": 5}
GENERIC = {
    "general", "national", "apex", "public", "insurance", "human", "global", "united", "american",
    "first", "advanced", "applied", "digital", "data", "smart", "central", "pacific", "atlantic",
    "summit", "premier", "prime", "alpha", "beta", "delta", "sigma", "omega", "vertex", "nova",
    "core", "blue", "red", "green", "one", "next", "open", "clear", "simple", "rapid", "quantum",
    "fusion", "horizon", "liberty", "eagle", "phoenix", "titan", "atlas", "matrix", "health",
    "medical", "energy", "financial", "capital", "solutions", "systems", "technologies", "tech",
    "labs", "analytics", "consulting", "partners", "group", "network", "bank", "trust", "mutual",
    "life", "care", "home", "world", "union", "standard", "state", "city", "metro", "point",
    "bridge", "anchor", "harbor", "pinnacle", "keystone", "frontier", "legacy", "integral",
}
SUFFIX = re.compile(r"\b(?:inc|incorporated|llc|ltd|limited|corp|corporation|company|co|lp|llp|"
                    r"plc|pbc|the|and|usa|us|na|holdings?|group|jobs|careers|job board|at)\b")


def core(name):
    s = (name or "").lower().replace("&", " ")
    s = re.sub(r"\bd\s*/?\s*b\s*/?\s*a\b.*$", " ", s)      # drop "d/b/a ..." tails
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return "".join(SUFFIX.sub(" ", s).split())


def name_match(ours, board):
    """-> (same name after removing legal suffixes?, similarity ratio shown for review)"""
    a, b = core(ours), core(board)
    if not a or not b:
        return False, 0.0
    return a == b, round(SequenceMatcher(None, a, b).ratio(), 2)


# ------------------------------------------------ board names (one extra request per hit)
def _title(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    m = re.search(r"<title>(.*?)</title>", r.text, re.S | re.I) if r.ok else None
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def board_name(ats, slug):
    if ats == "greenhouse":
        r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", headers=HEADERS,
                         timeout=TIMEOUT)
        return r.json().get("name") if r.ok else None
    if ats == "lever":
        return _title(f"https://jobs.lever.co/{slug}")
    if ats == "ashby":
        t = _title(f"https://jobs.ashbyhq.com/{slug}")
        return re.sub(r"\s*(?:jobs|careers)\s*$", "", t, flags=re.I) if t else None
    if ats == "smartrecruiters":
        r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
                         headers=HEADERS, timeout=TIMEOUT)
        content = r.json().get("content") if r.ok else None
        return content[0].get("company", {}).get("name") if content else None
    if ats == "recruitee":
        r = requests.get(f"https://{slug}.recruitee.com/api/offers/", headers=HEADERS, timeout=TIMEOUT)
        offers = r.json().get("offers") if r.ok else None
        return offers[0].get("company_name") if offers else None
    if ats == "workable":
        r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=false",
                         headers=HEADERS, timeout=TIMEOUT)
        return r.json().get("name") if r.ok else None
    return None


def probe(company):
    for slug in candidates(company["company_name"]):
        for ats, fn in PROBES:
            try:
                n = fn(slug)
            except Exception:
                continue
            if n >= MIN_JOBS.get(ats, 1):
                try:
                    name = board_name(ats, slug)
                except Exception:
                    name = None
                return {**company, "ats": ats, "slug": slug, "n_jobs": n, "board_name": name or ""}
    return None


def judge(hit, rejected, existing):
    strong, ratio = name_match(hit["company_name"], hit["board_name"])
    reasons = []
    if not hit["board_name"]:
        reasons.append("no board name")
    elif not strong:
        reasons.append("name mismatch")
    if hit["slug"] in GENERIC or len(hit["slug"]) <= 3:
        reasons.append("generic or short slug")
    if (hit["ats"], hit["slug"]) in rejected:
        reasons.append("rejected before (ats_slug_review.csv)")
    if (hit["ats"], hit["slug"]) in existing:
        reasons.append("slug already in ats_sources")
    return {**hit, "name_ratio": ratio, "confidence": "low" if reasons else "high",
            "reason": "; ".join(reasons)}


def load_companies(cur, offset, limit):
    cur.execute(
        "SELECT c.company_id, c.company_name, c.normalized_name, "
        "SUM(e.non_senior_filing_count) AS evidence "
        "FROM company_role_evidence e JOIN companies c USING (company_id) "
        "WHERE e.track <> 'SOFTWARE_ENGINEERING' "
        "AND e.company_id NOT IN (SELECT company_id FROM ats_sources) "
        "GROUP BY c.company_id, c.company_name, c.normalized_name HAVING evidence > 0 "
        "ORDER BY evidence DESC, c.company_id")
    rows = [r for r in cur.fetchall() if not any(s in r[2].lower() for s in STAFFING)]
    return [{"rank": offset + i + 1, "company_id": cid, "company_name": name, "evidence": int(ev)}
            for i, (cid, name, _, ev) in enumerate(rows[offset:offset + limit])]


def load_csv(path):
    """Insert the hits of a CSV written by this script into ats_sources (see the docstring)."""
    with open(path, encoding="utf-8") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: int(r["rank"]))
    with open(REVIEW_CSV, encoding="utf-8") as f:
        rejected = {(r["ats"], r["slug"]) for r in csv.DictReader(f) if r["decision"] == "reject"}
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ats_type, slug FROM ats_sources")
        taken = set(cur.fetchall())
        skipped = {"already in ats_sources": 0, "rejected before": 0, "repeated in the CSV": 0}
        seen, n_in = set(), {"high": 0, "low": 0}
        for r in rows:
            key = (r["ats"], r["slug"])
            reason = ("already in ats_sources" if key in taken else
                      "rejected before" if key in rejected else
                      "repeated in the CSV" if key in seen else None)
            if reason:
                skipped[reason] += 1
                continue
            seen.add(key)
            cur.execute("INSERT INTO ats_sources (company_id, ats_type, slug, board_name, confidence, "
                        "discovered_by, is_active) VALUES (%s, %s, %s, %s, %s, 'resolve_slugs_db', %s)",
                        (int(r["company_id"]), r["ats"], r["slug"], r["board_name"][:255] or None,
                         r["confidence"], r["confidence"] == "high"))
            n_in[r["confidence"]] += 1
        conn.commit()
    finally:
        conn.close()
    print(f"Inserted {n_in['high']} high (active) + {n_in['low']} low (inactive); skipped {skipped}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sample", type=int, help="probe a random sample of this size from the ranks")
    ap.add_argument("--seed", type=int, default=551)
    ap.add_argument("--skip-ids", help="file with company_ids already probed, one per line")
    ap.add_argument("--merge", nargs="*", default=[], help="earlier hit CSVs for the skipped ids")
    ap.add_argument("--out", default=str(ROOT / "experiments" / "slug_candidates.csv"))
    ap.add_argument("--load-csv", help="insert this hits CSV into ats_sources instead of probing")
    args = ap.parse_args()
    if args.load_csv:
        load_csv(args.load_csv)
        return

    conn = get_conn()
    try:
        cur = conn.cursor()
        companies = load_companies(cur, args.offset, args.limit)
        cur.execute("SELECT ats_type, slug FROM ats_sources")
        existing = set(cur.fetchall())
    finally:
        conn.close()
    if args.sample:
        companies = sorted(random.Random(args.seed).sample(companies, min(args.sample, len(companies))),
                           key=lambda c: c["rank"])
    with open(REVIEW_CSV, encoding="utf-8") as f:
        rejected = {(r["ats"], r["slug"]) for r in csv.DictReader(f) if r["decision"] == "reject"}
    hits = []
    if args.skip_ids:
        skip = set(int(x) for x in Path(args.skip_ids).read_text().split())
        by_id = {c["company_id"]: c for c in companies}
        for path in args.merge:
            with open(path, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    c = by_id.get(int(r["company_id"]))
                    if c and int(r["company_id"]) in skip:
                        hits.append(judge({**c, "ats": r["ats"], "slug": r["slug"],
                                           "n_jobs": int(r["n_jobs"]), "board_name": r["board_name"]},
                                          rejected, existing))
        n_before = len(companies)
        companies = [c for c in companies if c["company_id"] not in skip]
        print(f"{n_before - len(companies)} companies already probed: {len(hits)} earlier hits merged")

    print(f"{len(companies)} companies (ranks {args.offset + 1}-{args.offset + args.limit}"
          + (f", random sample, seed {args.seed}" if args.sample else "") + f"), {WORKERS} workers",
          flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, res in enumerate(ex.map(probe, companies), 1):
            if res:
                hits.append(judge(res, rejected, existing))
            if i % 50 == 0:
                print(f"  ...{i} done, {len(hits)} hits, {time.time() - t0:.0f}s", flush=True)
    secs = time.time() - t0

    cols = ["rank", "company_id", "company_name", "evidence", "ats", "slug", "board_name",
            "name_ratio", "n_jobs", "confidence", "reason"]
    out = Path(args.out).resolve()
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(hits, key=lambda h: h["rank"]))
    print(f"\n{len(hits)} hits / {len(companies)} companies in {secs:.0f}s "
          f"({secs / max(len(companies), 1):.2f}s per company) -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
