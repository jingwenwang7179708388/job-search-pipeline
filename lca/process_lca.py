#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lca/process_lca.py - raw LCA data -> aggregate by company + Track -> write to MySQL

Flow:
  data/raw/lca/*.xlsx -> keep only the needed columns per file, cached as
                         data/processed/lca_parquet/*.parquet
             -> classify JOB_TITLE into a Track, drop OTHER
             -> de-duplicate by CASE_NUMBER (keep the row from the latest quarter)
             -> keep Certified only (not Certified - Withdrawn)
             -> drop staffing firms / universities / hospitals (common/names.py BODYSHOPS)
             -> flag senior titles (common/names.py TITLE_SKIP) for non_senior_filing_count
             -> convert wages to annual salary
             -> write companies and company_role_evidence

The first run reads all 25 xlsx files and is slow (tens of minutes); later runs read the
parquet cache and are fast. Changing the Track rules does not require re-reading the xlsx
files - just rerun.

Usage (from the project root): python3 lca/process_lca.py
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common.db import get_conn                  # noqa: E402
from common.names import BODYSHOPS, TITLE_SKIP, normalize    # noqa: E402

RAW_DIR = ROOT / "data" / "raw" / "lca"
CACHE_DIR = ROOT / "data" / "processed" / "lca_parquet"
ALL_PARQUET = ROOT / "data" / "processed" / "lca_all.parquet"

# ------------------------------------------------ Track rules - edit here to change classification
# Matched in order; the first hit wins (e.g. "ML Data Scientist" counts as ML_AI)
TRACK_RULES = [
    ("ML_AI", [
        r"machine learning", r"\bml\b", r"deep learning", r"artificial intelligence",
        r"\bai\b", r"computer vision", r"\bnlp\b", r"natural language",
        r"applied scientist", r"\bperception\b",
    ]),
    ("DATA_SCIENCE", [
        r"data scien", r"decision scien", r"statistician", r"data mining",
        r"quantitative (?:research|analyst|developer|scientist|strateg)", r"\bquant\b",
    ]),
    ("DATA_ANALYTICS", [
        r"data analy", r"analytics", r"business intelligence",
        r"\bbi (?:engineer|developer|analyst)",
    ]),
]

# Research titles are ambiguous (ML research vs. biology/chemistry research), so the title alone
# does not decide: a title still classified OTHER that matches RESEARCH_TITLES becomes ML_AI only
# when its SOC code is one of RESEARCH_ML_SOCS. Anything else (e.g. SOC 19-xxxx life/physical
# sciences) stays OTHER.
RESEARCH_TITLES = r"research scientist|research engineer|member of (?:the )?technical staff"
RESEARCH_ML_SOCS = ("15-1221",   # Computer and Information Research Scientists
                    "15-2051")   # Data Scientists

# Phrases removed from a title before the senior check: their "staff" is not a level word.
# A level word elsewhere in the title still counts ("Senior Member of Technical Staff").
NOT_SENIOR_PHRASES = r"member of (?:the )?technical staff"

# Wage unit -> multiplier to annual salary
WAGE_MULT = {"year": 1, "hour": 2080, "week": 52, "bi-weekly": 26, "month": 12}
# Annual wages outside this range are treated as data-entry errors and left out of the average
WAGE_MIN, WAGE_MAX = 20_000, 1_000_000

# Our column -> LCA column name (some FY2020 Q1 columns carry a _1 suffix; pick_col handles that)
COLS = {
    "case_number": "CASE_NUMBER",
    "status": "CASE_STATUS",
    "employer": "EMPLOYER_NAME",
    "title": "JOB_TITLE",
    "state": "WORKSITE_STATE",
    "wage": "WAGE_RATE_OF_PAY_FROM",
    "wage_unit": "WAGE_UNIT_OF_PAY",
    "soc_code": "SOC_CODE",
}
REQUIRED = ["case_number", "status", "employer", "title"]
# ------------------------------------------------


def file_fy_q(path):
    """Fiscal year and quarter from the file name, e.g. LCA_Disclosure_Data_FY2025_Q4.xlsx -> (2025, 4)"""
    m = re.search(r"FY(\d{4})\D*Q(\d)", path.name, re.I)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def pick_col(columns, base):
    for c in (base, base + "_1"):
        if c in columns:
            return c
    return None


def load_one(path):
    """Read one LCA file, keeping only the needed columns. Uses the cache when present and it
    has every column in COLS; a cache written before a column was added is rebuilt."""
    cache = CACHE_DIR / (path.stem + ".parquet")
    if cache.exists():
        df = pd.read_parquet(cache)
        if set(COLS) <= set(df.columns):
            return df

    fy, q = file_fy_q(path)
    if fy is None:
        print(f"  Skipped {path.name}: no FYxxxx_Qx in the file name")
        return None

    print(f"  Reading {path.name} (first xlsx read, slow) ...", flush=True)
    raw = pd.read_excel(path, engine="calamine", dtype=str)
    raw.columns = [str(c).strip().upper() for c in raw.columns]

    out = {}
    for key, base in COLS.items():
        col = pick_col(raw.columns, base)
        if col is None and key in REQUIRED:
            print(f"  Skipped {path.name}: column {base} not found. First 15 columns: {list(raw.columns)[:15]}")
            return None
        out[key] = (raw[col] if col else pd.Series(pd.NA, index=raw.index)).astype("string")

    df = pd.DataFrame(out)
    df["fy"], df["q"] = fy, q
    df.to_parquet(cache, index=False)
    return df


def classify(titles, socs):
    t = titles.fillna("").str.lower()
    conds = [t.str.contains("|".join(pats), regex=True, na=False).to_numpy(dtype=bool)
             for _, pats in TRACK_RULES]
    track = np.select(conds, [name for name, _ in TRACK_RULES], default="OTHER")
    research = (t.str.contains(RESEARCH_TITLES, regex=True, na=False)
                & socs.fillna("").str.strip().str.startswith(RESEARCH_ML_SOCS)).to_numpy(dtype=bool)
    return np.where((track == "OTHER") & research, "ML_AI", track), research & (track == "OTHER")


def annual_wage(df):
    w = pd.to_numeric(df["wage"].str.replace(r"[$,\s]", "", regex=True), errors="coerce")
    mult = df["wage_unit"].str.strip().str.lower().map(WAGE_MULT).astype(float)
    a = w.astype(float) * mult
    return a.where((a >= WAGE_MIN) & (a <= WAGE_MAX))


def main():
    files = sorted(RAW_DIR.glob("*.xlsx"))
    if not files:
        sys.exit(f"No xlsx files in {RAW_DIR}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Found {len(files)} LCA files\n")

    frames, n_raw, n_research = [], 0, 0
    for i, f in enumerate(files, 1):
        df = load_one(f)
        if df is None:
            continue
        n_raw += len(df)
        df["track"], promoted = classify(df["title"], df["soc_code"])
        n_research += int(promoted.sum())
        df = df[df["track"] != "OTHER"]
        frames.append(df)
        print(f"[{i}/{len(files)}] {f.name:<45} Track hits {len(df):,}", flush=True)

    lca = pd.concat(frames, ignore_index=True)
    n_track = len(lca)

    # The same CASE_NUMBER can appear in several quarterly files (some DOL files are
    # cumulative, and the status can change); keep the row from the latest quarter
    lca["case_number"] = lca["case_number"].str.strip()
    lca = lca[lca["case_number"].notna() & (lca["case_number"] != "")]
    lca = lca.sort_values(["fy", "q"], kind="stable").drop_duplicates("case_number", keep="last")
    n_dedup = len(lca)

    lca = lca[lca["status"].str.strip().str.lower() == "certified"]
    n_cert = len(lca)

    uniq = lca["employer"].dropna().unique()
    name_map = {u: normalize(u) for u in uniq}
    lca["norm"] = lca["employer"].map(name_map)
    lca = lca[lca["norm"].notna() & (lca["norm"] != "")]

    # Match BODYSHOPS against the cleaned name, same as legacy/build_companies.py does
    bodyshop_re = "|".join(re.escape(b) for b in BODYSHOPS)
    lca = lca[~lca["norm"].str.lower().str.contains(bodyshop_re, regex=True, na=False)]
    n_clean = len(lca)

    # Same substring rule as legacy/build_companies.py: a title containing any TITLE_SKIP
    # marker is a senior filing and is left out of non_senior_filing_count (NOT_SENIOR_PHRASES
    # are removed first)
    senior_re = "|".join(re.escape(k) for k in TITLE_SKIP)
    lca["non_senior"] = ~(lca["title"].fillna("").str.lower()
                          .str.replace(NOT_SENIOR_PHRASES, "", regex=True)
                          .str.contains(senior_re, regex=True))
    lca["annual_wage"] = annual_wage(lca)

    lca.to_parquet(ALL_PARQUET, index=False)

    # companies: under one normalized name, the most frequent raw name is the display name
    companies = (lca.groupby("norm")["employer"]
                    .agg(lambda s: s.value_counts().index[0].strip()[:255])
                    .reset_index())

    evidence = lca.groupby(["norm", "track"]).agg(
        filing_count=("case_number", "size"),
        non_senior_filing_count=("non_senior", "sum"),
        latest_year=("fy", "max"),
        states=("state", lambda s: ",".join(sorted(
            {x.strip().upper() for x in s.dropna() if x.strip()}))[:500]),
        average_wage=("annual_wage", "mean"),
    ).reset_index()

    print(f"\n{'='*54}")
    print(f"Raw records                  {n_raw:,}")
    print(f"Track hits                   {n_track:,}  (incl. {n_research:,} research titles via SOC)")
    print(f"After CASE de-dup            {n_dedup:,}")
    print(f"Certified                    {n_cert:,}")
    print(f"After staffing/univ/hospital {n_clean:,}")
    print(f"Companies                    {len(companies):,}")
    print(f"Company x Track              {len(evidence):,}")
    print("=" * 54)

    # ---------------- write to the database
    conn = get_conn()
    try:
        cur = conn.cursor()
        rows = [(r.employer, r.norm) for r in companies.itertuples()]
        for i in range(0, len(rows), 5000):
            cur.executemany(
                "INSERT INTO companies (company_name, normalized_name) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE company_name = company_name",
                rows[i:i + 5000])

        cur.execute("SELECT company_id, normalized_name FROM companies")
        id_map = {name: cid for cid, name in cur.fetchall()}

        # evidence is derived entirely from LCA; rebuild it every run so reruns are consistent
        cur.execute("DELETE FROM company_role_evidence")
        ev_rows, n_missing = [], 0
        for r in evidence.itertuples():
            cid = id_map.get(r.norm)
            if cid is None:
                n_missing += 1
                continue
            wage = None if pd.isna(r.average_wage) else round(float(r.average_wage), 2)
            ev_rows.append((cid, r.track, int(r.filing_count), int(r.non_senior_filing_count),
                            int(r.latest_year), r.states or None, wage))
        for i in range(0, len(ev_rows), 5000):
            cur.executemany(
                "INSERT INTO company_role_evidence "
                "(company_id, track, filing_count, non_senior_filing_count, latest_year, "
                "states, average_wage) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ev_rows[i:i + 5000])
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM companies")
        n_co = cur.fetchone()[0]
        cur.execute("SELECT track, COUNT(*), SUM(filing_count), SUM(non_senior_filing_count) "
                    "FROM company_role_evidence GROUP BY track ORDER BY track")
        by_track = cur.fetchall()
    finally:
        conn.close()

    print(f"\nWrite complete: companies table has {n_co:,} rows")
    if n_missing:
        print(f"  ({n_missing} evidence rows had no matching company ID and were skipped)")
    print("company_role_evidence by Track:")
    for track, n, s, ns in by_track:
        print(f"  {track:<16} {n:>6,} companies   {int(s):>8,} filings   {int(ns):>8,} non-senior")

    print("\nTop 15 by filing count:")
    top = (evidence.groupby("norm")["filing_count"].sum()
                   .sort_values(ascending=False).head(15))
    for name, n in top.items():
        print(f"  {n:>6,}  {name}")


if __name__ == "__main__":
    main()
