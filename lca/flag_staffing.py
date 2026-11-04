#!/usr/bin/env python3
"""
lca/flag_staffing.py - flag likely IT staffing / outsourcing companies from raw LCA data

Two LCA employer-level fields, per filing:
  H-1B dependent    the employer's H-1B workers are a large share of its workforce
                    (column H-1B_DEPENDENT / H1B_DEPENDENT / H_1B_DEPENDENT, Yes/No or Y/N)
  SECONDARY_ENTITY  the worker is placed at another company's site (a client)

companies.likely_staffing = TRUE when, over the company's certified filings (CASE_NUMBER
de-duplicated, latest quarter kept, as in lca/process_lca.py; all Tracks, before the BODYSHOPS
and Track filters), more than SHARE of the filings with a known value are H-1B dependent AND
more than SHARE place the worker at a secondary entity. Either one alone is not enough:
H-1B dependent alone flags Meta, NVIDIA, ByteDance, eBay, Visa, Qualcomm; secondary entity
alone flags IBM. Every other company is set FALSE.

Only companies.likely_staffing is written; company_role_evidence is not touched. The needed
columns of every xlsx are cached in data/processed/lca_staffing.parquet (the first run reads
all xlsx files, ~10 min); --refresh rebuilds the cache.

Usage (from the project root):
  python3 lca/flag_staffing.py [--refresh] [--dry-run]
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common.db import get_conn                  # noqa: E402
from common.names import normalize              # noqa: E402
from lca.process_lca import RAW_DIR, file_fy_q  # noqa: E402

CACHE = ROOT / "data" / "processed" / "lca_staffing.parquet"
SHARE = 0.5
# Our column -> LCA column names (the H-1B dependent column is spelled three ways)
COLS = {
    "case_number": ["CASE_NUMBER"],
    "status": ["CASE_STATUS"],
    "employer": ["EMPLOYER_NAME"],
    "h1b_dependent": ["H-1B_DEPENDENT", "H1B_DEPENDENT", "H_1B_DEPENDENT"],
    "secondary_entity": ["SECONDARY_ENTITY"],
}
# Large direct employers that must never be flagged (normalized_name)
CHECK_NAMES = ["AMAZONCOM SERVICES", "AMAZON WEB SERVICES", "GOOGLE", "MICROSOFT", "META PLATFORMS",
               "FACEBOOK", "APPLE", "UBER TECHNOLOGIES", "AIRBNB", "STRIPE", "IBM",
               "INTERNATIONAL BUSINESS MACHINES"]


def read_raw():
    frames = []
    for path in sorted(RAW_DIR.glob("*.xlsx")):
        fy, q = file_fy_q(path)
        if fy is None:
            continue
        print(f"  Reading {path.name} ...", flush=True)
        raw = pd.read_excel(path, engine="calamine", dtype=str)
        raw.columns = [str(c).strip().upper() for c in raw.columns]
        out = {}
        for key, names in COLS.items():
            col = next((c for n in names for c in (n, n + "_1") if c in raw.columns), None)
            if col is None:
                sys.exit(f"{path.name}: none of {names} found")
            out[key] = raw[col].astype("string")
        df = pd.DataFrame(out)
        df["fy"], df["q"] = fy, q
        frames.append(df)
    lca = pd.concat(frames, ignore_index=True)
    lca.to_parquet(CACHE, index=False)
    return lca


def yes_no(s):
    """Yes/Y -> 1, No/N -> 0, anything else -> NaN (not counted in the share)."""
    return s.fillna("").str.strip().str.upper().str[:1].map({"Y": 1, "N": 0})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--refresh", action="store_true", help="re-read the xlsx files")
    ap.add_argument("--dry-run", action="store_true", help="print counts, write nothing")
    args = ap.parse_args()

    lca = pd.read_parquet(CACHE) if CACHE.exists() and not args.refresh else read_raw()
    lca["case_number"] = lca["case_number"].str.strip()
    lca = lca[lca["case_number"].notna() & (lca["case_number"] != "")]
    lca = lca.sort_values(["fy", "q"], kind="stable").drop_duplicates("case_number", keep="last")
    lca = lca[lca["status"].str.strip().str.lower() == "certified"].copy()
    lca["dep"], lca["sec"] = yes_no(lca["h1b_dependent"]), yes_no(lca["secondary_entity"])
    name_map = {u: normalize(u) for u in lca["employer"].dropna().unique()}
    lca["norm"] = lca["employer"].map(name_map)

    by_co = lca.groupby("norm").agg(dep_share=("dep", "mean"), sec_share=("sec", "mean"))
    flagged = set(by_co.index[(by_co["dep_share"] > SHARE) & (by_co["sec_share"] > SHARE)])
    print(f"\n{len(lca):,} certified filings, {len(by_co):,} employer names, "
          f"{len(flagged):,} likely staffing")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT company_id, normalized_name FROM companies")
        ids = [cid for cid, name in cur.fetchall() if name in flagged]
        print(f"companies rows flagged: {len(ids):,}")
        if args.dry_run:
            print("Dry run: nothing written.")
            return
        cur.execute("UPDATE companies SET likely_staffing = FALSE")
        for i in range(0, len(ids), 5000):
            chunk = ids[i:i + 5000]
            cur.execute("UPDATE companies SET likely_staffing = TRUE WHERE company_id IN (%s)"
                        % ",".join(["%s"] * len(chunk)), chunk)
        conn.commit()
        cur.execute("SELECT company_name, likely_staffing FROM companies WHERE normalized_name IN (%s)"
                    % ",".join(["%s"] * len(CHECK_NAMES)), CHECK_NAMES)
        print("Check (should all be 0):", cur.fetchall())
    finally:
        conn.close()


if __name__ == "__main__":
    main()
