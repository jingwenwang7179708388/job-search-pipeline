#!/usr/bin/env python3
"""
processing/process_jobs.py - extract structured features from each posting into job_features

Per job (title + JD text), keywords and regex only:
  role_family    common.tracks.classify_title(title, None) (ATS postings have no SOC).
                 OTHER jobs: role_family = OTHER, visa_evidence = Unclear, every other field at
                 its empty / default value, marked processed; nothing else is extracted.
  seniority      intern / entry / mid / senior / unknown. From the title's level words first
                 (common.tracks.normalize_title levels; "member of technical staff" is not a
                 level): intern / internship / co-op / student worker / contract student
                 -> intern; senior, sr, lead, staff,
                 principal, manager, director, head, vp, iii, iv, v -> senior; ii -> mid;
                 associate, junior, jr, i, new grad, entry level, early career, university
                 graduate -> entry. No level word: by min_years_experience (0-1 entry, 2-4 mid,
                 5+ senior), else unknown.
  min_years_experience  smallest "N(+)/N-M/N to M years ... experience" in the JD; values over
                 MAX_YEARS are dropped; NULL when none is found.
  requires_citizenship / requires_clearance / no_sponsorship
                 each match is judged on its local context only: up to CONTEXT_BEFORE chars
                 before it and up to the next sentence end after it, both cut at sentence
                 ends, line breaks and list-item breaks (Lever list items are glued together
                 with no punctuation). Skipped when the context is EEO boilerplate, card-network
                 "Visa", an executive / employer / bank sponsor, or (citizenship / clearance)
                 optional: preferred, a plus, nice to have, beneficial, not necessary, ...
                 Foreign clearances (UK, NATO, ...) count too: those roles are foreign-government
                 work an H-1B candidate cannot take either. The context is kept for --dry-run
                 sampling (not stored).
  skills         fixed list SKILLS, whole words; "R" is case-sensitive and never "R&D". JSON array.
  global_eligible  FALSE only when requires_citizenship or requires_clearance.
  visa_evidence  from company_role_evidence for the job's company and the SAME Track
                 (n = non_senior_filing_count, recent = latest_year >= RECENT_YEAR), first match:
                   Blocked   any JD restriction (citizenship, clearance, no sponsorship)
                   Unlikely  the company has no recent filing in any Track (or no evidence rows)
                   Likely    n >= 5 and recent
                   Unclear   everything else (n 1-4; n >= 5 but not recent; n = 0 but a recent
                             filing in another Track)
                 OTHER jobs are Unclear.

Writes: INSERT ... ON DUPLICATE KEY UPDATE job_features and jobs.processing_status = processed
in the same transaction, committed every BATCH jobs. A job that fails is rolled back to its own
savepoint, marked failed and the error is printed; the run continues.

Which jobs: processing_status = pending (default); --retry-failed adds failed; --all takes
every job (after a rule change or an LCA rerun, since visa_evidence depends on
company_role_evidence). --limit N caps the count. --dry-run writes nothing and prints counts
plus every restriction hit with its sentence, and samples of years / seniority.

Usage (from the project root):
  python3 processing/process_jobs.py --dry-run
  python3 processing/process_jobs.py [--retry-failed | --all] [--limit N]
"""

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common.db import get_conn                          # noqa: E402
from common.tracks import classify_title, normalize_title   # noqa: E402

BATCH = 500
MAX_YEARS = 20
RECENT_YEAR = 2024          # LCA fiscal year; "filed in the last 3 years" = latest_year >= this
LIKELY_MIN = 5              # same-Track non-senior filings for Likely
SAMPLE = 15                 # rows per sample list in --dry-run
MAX_HITS = 30               # restriction hits listed per type in --dry-run

# ------------------------------------------------ seniority
MTS_RE = re.compile(r"member of (?:the )?technical staff", re.I)
INTERN_RE = re.compile(r"\b(?:intern|interns|internship|internships|co ?op|student workers?|"
                       r"contract students?)\b")
ENTRY_TITLE_RE = re.compile(r"\b(?:new grad\w*|recent grad\w*|entry level|early career|"
                            r"university grad\w*|college grad\w*)\b")
SENIOR_LEVELS = {"senior", "sr", "lead", "staff", "principal", "manager", "managers", "director",
                 "head", "vp", "iii", "iv", "v"}
ENTRY_LEVELS = {"associate", "junior", "jr", "i"}

# ------------------------------------------------ years of experience
YEARS_RE = re.compile(
    r"\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b['’]?"
    r"(?:\W+\w+){0,5}?\W+experience", re.I)

# ------------------------------------------------ restrictions (matched per sentence)
US = r"\b(?:u\.?\s?s\.?|united states)"
CITIZENSHIP_RE = re.compile(
    rf"(?:must|should|need to|required to) (?:be|hold|have) (?:a |an )?{US} citizen"
    rf"|{US} citizenship (?:is )?(?:required|a requirement|mandatory)"
    rf"|requires? (?:{US} )?citizenship"
    rf"|{US} citizens? only|only {US} citizens"
    rf"|{US} persons?\b", re.I)
CLEARANCE_RE = re.compile(
    r"security clearance|\bts ?/ ?sci\b|top secret|secret clearance|public trust (?:clearance|position)"
    rf"|(?:active|current|obtain|maintain|eligib\w+ (?:for|to obtain)) (?:a |an )?(?:{US} )?"
    r"(?:government |security |dod |federal )?clearance"
    r"|clearance (?:is )?required", re.I)
NO_SPONSOR_RE = re.compile(
    r"(?:not|unable to|cannot|can ?not|can't|won't|will not|do not|does not|don't|doesn't|"
    r"are not able to|is not able to)\s+(?:\w+\s+){0,4}?(?:sponsor|sponsorship)\b"
    r"|(?:visa|immigration|employment|work) sponsorship (?:is )?(?:not|unavailable)"
    r"|\bno (?:visa |immigration |employment )?sponsorship"
    r"|without (?:the need for |requiring )?(?:current or future |future )?"
    r"(?:visa |immigration |employer |employment )?sponsorship", re.I)
VISA_CONTEXT_RE = re.compile(r"visa|immigration|sponsorship|work authori[sz]|authori[sz]ed to work|"
                             r"employment authori[sz]|h-?1b", re.I)
# Sentences that only look like restrictions
EEO_RE = re.compile(r"without regard to|regardless of|discriminat|equal (?:employment )?opportunity|"
                    r"protected (?:veteran|characteristic|status)|national origin|e-verify", re.I)
FALSE_SPONSOR_RE = re.compile(r"executive sponsor|technical sponsor|employer[- ]sponsored|"
                              r"sponsor banks?|sponsored (?:placements?|media|content|ads?)|"
                              r"mastercard|amex|american express|card networks?", re.I)
PREFERRED_RE = re.compile(r"preferred|a plus|nice to have|bonus|desired|ideally|beneficial|"
                          r"not necessary|not required|optional", re.I)
# Sentence end, line break, or a list-item break: Lever list items arrive glued together
# ("... with users Active US Security clearance ..."), so a lowercase word followed by a
# capitalized word starts a new item
BOUNDARY_RE = re.compile(r"[.!?;](?=\s|$)|\n|(?<=[a-z0-9%)])\s+(?=[A-Z][a-z])")
CONTEXT_BEFORE = 100        # max chars of context before a restriction match
CONTEXT_AFTER = 300         # max chars after it when no boundary comes sooner
US_DOTS = re.compile(r"\bU\.\s?S\.(?:\s?A\.)?", re.I)

# ------------------------------------------------ skills
SKILLS = [  # (stored name, regex)
    ("python", re.compile(r"\bpython\b", re.I)),
    ("sql", re.compile(r"\bsql\b", re.I)),
    ("r", re.compile(r"(?<![\w&/.-])R(?![\w&]|\s*&\s*D\b|\s*\+\+)")),   # case-sensitive; not R&D
    ("tableau", re.compile(r"\btableau\b", re.I)),
    ("power bi", re.compile(r"\bpower ?bi\b", re.I)),
    ("spark", re.compile(r"\b(?:py)?spark\b", re.I)),
    ("aws", re.compile(r"\baws\b|amazon web services", re.I)),
    ("pytorch", re.compile(r"\bpytorch\b", re.I)),
    ("tensorflow", re.compile(r"\btensorflow\b", re.I)),
    ("machine learning", re.compile(r"\bmachine learning\b", re.I)),
]


def min_years(text):
    years = [int(m.group(1)) for m in YEARS_RE.finditer(text)]
    years = [y for y in years if y <= MAX_YEARS]
    return min(years) if years else None


def seniority(title, years):
    pre, _, levels = normalize_title(MTS_RE.sub(" ", title))   # MTS "staff" is not a level
    lv = set(levels.split())
    if INTERN_RE.search(pre):
        return "intern"
    if lv & SENIOR_LEVELS:
        return "senior"
    if "ii" in lv:
        return "mid"
    if lv & ENTRY_LEVELS or ENTRY_TITLE_RE.search(pre):
        return "entry"
    if years is None:
        return "unknown"
    return "entry" if years <= 1 else "mid" if years <= 4 else "senior"


def context(text, start, end):
    """Local context of a match: the item / sentence around it (see BOUNDARY_RE)."""
    before = text[max(0, start - CONTEXT_BEFORE):start]
    cuts = list(BOUNDARY_RE.finditer(before))
    if cuts:
        before = before[cuts[-1].end():]
    nxt = BOUNDARY_RE.search(text, end, end + CONTEXT_AFTER)
    stop = (nxt.end() if text[nxt.start()] in ".!?;" else nxt.start()) if nxt else end + CONTEXT_AFTER
    return (before + text[start:stop]).strip()


def restrictions(text):
    """-> {"citizenship": [contexts], "clearance": [...], "no_sponsorship": [...]}"""
    text = US_DOTS.sub("US", text)            # "U.S." would otherwise end a sentence
    hits = {}
    for key, rx in (("citizenship", CITIZENSHIP_RE), ("clearance", CLEARANCE_RE),
                    ("no_sponsorship", NO_SPONSOR_RE)):
        found = []
        for m in rx.finditer(text):
            ctx = context(text, m.start(), m.end())
            if ctx in found or EEO_RE.search(ctx):
                continue
            if key == "no_sponsorship":
                if not VISA_CONTEXT_RE.search(ctx) or FALSE_SPONSOR_RE.search(ctx):
                    continue
            elif PREFERRED_RE.search(ctx):
                continue
            found.append(ctx)
        hits[key] = found
    return hits


def skills(text):
    text = text.replace("(R)", " ").replace("®", " ")      # trademark marks are not the R language
    return [name for name, rx in SKILLS if rx.search(text)]


def visa_evidence(blocked, company_ev, track):
    """company_ev: {track: (non_senior_filing_count, latest_year)} for the job's company."""
    if blocked:
        return "Blocked"
    if not any(y >= RECENT_YEAR for _, y in company_ev.values()):
        return "Unlikely"            # no recent filing in any Track, or no evidence at all
    n, latest = company_ev.get(track, (0, 0))
    if n >= LIKELY_MIN and latest >= RECENT_YEAR:
        return "Likely"
    return "Unclear"


def extract(title, desc, company_ev):
    """-> (row dict for job_features, restriction hits)"""
    track = classify_title(title, None)[0]
    if track == "OTHER":
        return {"role_family": "OTHER", "seniority": "unknown", "min_years_experience": None,
                "requires_citizenship": False, "requires_clearance": False,
                "no_sponsorship": False, "visa_evidence": "Unclear", "skills": None,
                "global_eligible": True}, None
    text = desc or ""
    years = min_years(text)
    hits = restrictions(text)
    cit, clr, nos = (bool(hits[k]) for k in ("citizenship", "clearance", "no_sponsorship"))
    return {"role_family": track, "seniority": seniority(title, years),
            "min_years_experience": years, "requires_citizenship": cit,
            "requires_clearance": clr, "no_sponsorship": nos,
            "visa_evidence": visa_evidence(cit or clr or nos, company_ev, track),
            "skills": json.dumps(skills(f"{title}\n{text}")),
            "global_eligible": not (cit or clr)}, hits


UPSERT = (
    "INSERT INTO job_features (job_id, role_family, seniority, min_years_experience, "
    "requires_citizenship, requires_clearance, no_sponsorship, visa_evidence, skills, "
    "global_eligible, processed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()) AS new "
    "ON DUPLICATE KEY UPDATE role_family = new.role_family, seniority = new.seniority, "
    "min_years_experience = new.min_years_experience, "
    "requires_citizenship = new.requires_citizenship, "
    "requires_clearance = new.requires_clearance, no_sponsorship = new.no_sponsorship, "
    "visa_evidence = new.visa_evidence, skills = new.skills, "
    "global_eligible = new.global_eligible, processed_at = NOW()")
COLS = ["role_family", "seniority", "min_years_experience", "requires_citizenship",
        "requires_clearance", "no_sponsorship", "visa_evidence", "skills", "global_eligible"]


def load_jobs(cur, args):
    where = ("" if args.all else
             "WHERE j.processing_status IN ('pending', 'failed') " if args.retry_failed else
             "WHERE j.processing_status = 'pending' ")
    sql = ("SELECT j.job_id, j.company_id, j.title, j.description_text, c.company_name "
           "FROM jobs j JOIN companies c USING (company_id) " + where + "ORDER BY j.job_id")
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    cur.execute(sql)
    return cur.fetchall()


def load_evidence(cur, company_ids):
    ev = {}
    if not company_ids:
        return ev
    ids = sorted(company_ids)
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        cur.execute("SELECT company_id, track, non_senior_filing_count, latest_year "
                    "FROM company_role_evidence WHERE company_id IN (%s)" % ",".join(["%s"] * len(chunk)),
                    chunk)
        for cid, track, n, y in cur.fetchall():
            ev.setdefault(cid, {})[track] = (int(n), int(y))
    return ev


def report(results, n_failed, dry_run):
    rows = [r for r, _, _ in results]
    print(f"\n{len(rows):,} jobs {'checked (dry run, nothing written)' if dry_run else 'processed'}, "
          f"{n_failed} failed")
    for col in ("role_family", "seniority", "visa_evidence"):
        print(f"\n{col}:")
        for k, n in Counter(r[col] for r in rows).most_common():
            print(f"  {k:<22}{n:>6,}")
    target = [r for r in rows if r["role_family"] != "OTHER"]
    print(f"\nNon-OTHER jobs: {len(target):,}")
    print(f"  min_years_experience found: {sum(r['min_years_experience'] is not None for r in target):,}")
    for col in ("requires_citizenship", "requires_clearance", "no_sponsorship"):
        print(f"  {col:<22}{sum(r[col] for r in target):>6,}")
    print(f"  global_eligible FALSE   {sum(not r['global_eligible'] for r in target):>6,}")
    sk = Counter(s for r in target for s in json.loads(r["skills"]))
    print("  skills: " + ", ".join(f"{k} {n:,}" for k, n in sk.most_common()))
    print("\nvisa_evidence by Track (non-OTHER):")
    by = Counter((r["role_family"], r["visa_evidence"]) for r in target)
    levels = ["Likely", "Unclear", "Unlikely", "Blocked"]
    print(f"  {'Track':<22}" + "".join(f"{v:>9}" for v in levels))
    for t in sorted({r["role_family"] for r in target}):
        print(f"  {t:<22}" + "".join(f"{by[(t, v)]:>9,}" for v in levels))
    if not dry_run:
        return

    print("\n---- every restriction hit (non-OTHER jobs), with the matched sentence")
    for key in ("citizenship", "clearance", "no_sponsorship"):
        hits = [(job, h) for _, h, job in results if h and h[key]]
        print(f"\n[{key}] {len(hits)} jobs" + (f" (first {MAX_HITS} listed)" if len(hits) > MAX_HITS else ""))
        for (jid, company, title), h in hits[:MAX_HITS]:
            print(f"  job {jid} | {company[:30]} | {title.strip()[:70]}")
            for s in h[key][:2]:
                print(f"      \"{s[:220]}\"")
    rnd = random.Random(551)
    print(f"\n---- sample: min_years_experience + seniority ({SAMPLE} random non-OTHER jobs)")
    tj = [(r, job) for r, _, job in results if r["role_family"] != "OTHER"]
    for r, (jid, company, title) in rnd.sample(tj, min(SAMPLE, len(tj))):
        print(f"  {r['seniority']:<8} years={str(r['min_years_experience']):<4} "
              f"{r['visa_evidence']:<9} {title.strip()[:70]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--retry-failed", action="store_true")
    g.add_argument("--all", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    conn = get_conn()
    try:
        cur = conn.cursor()
        jobs = load_jobs(cur, args)
        ev = load_evidence(cur, {j[1] for j in jobs})
        print(f"{len(jobs):,} jobs to process, evidence for {len(ev):,} of their companies", flush=True)

        results, n_failed, n_since_commit = [], 0, 0
        for job_id, company_id, title, desc, company in jobs:
            if not args.dry_run:
                cur.execute("SAVEPOINT job")
            try:
                row, hits = extract(title, desc, ev.get(company_id, {}))
                if not args.dry_run:
                    cur.execute(UPSERT, [job_id] + [row[c] for c in COLS])
                    cur.execute("UPDATE jobs SET processing_status = 'processed' WHERE job_id = %s",
                                (job_id,))
                results.append((row, hits, (job_id, company, title)))
            except Exception as e:
                n_failed += 1
                print(f"  job {job_id} FAILED {type(e).__name__}: {str(e)[:150]}", flush=True)
                if not args.dry_run:
                    cur.execute("ROLLBACK TO SAVEPOINT job")
                    cur.execute("UPDATE jobs SET processing_status = 'failed' WHERE job_id = %s",
                                (job_id,))
            n_since_commit += 1
            if not args.dry_run and n_since_commit >= BATCH:
                conn.commit()
                n_since_commit = 0
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    report(results, n_failed, args.dry_run)
    print(f"\ndone in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
