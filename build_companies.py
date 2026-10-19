#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_companies.py - build the target-company list from DOL H-1B disclosure data

Idea: a company that has actually sponsored Data Scientist / MLE visas goes on the list.
No guessing: it comes from government data.

Usage:
    pip install pandas openpyxl
    python build_companies.py LCA_Disclosure_Data_FY2025.xlsx
    python build_companies.py FY2025.xlsx FY2026_Q2.xlsx      # several files at once

Output:
    sponsors.csv        - full list, sorted by sponsorship count
    companies_new.csv   - in the format job_feed.py reads
"""


import re
import sys
import pandas as pd

# Target titles: a title containing any of these counts as a match
TITLES = [
    "data scientist", "applied scientist", "decision scientist",
    "machine learning", "ml engineer", "deep learning",
    "artificial intelligence", "ai engineer",
    "research scientist", "research engineer",
    "quantitative research", "quantitative analyst", "quantitative developer",
    "computer vision", "remote sensing", "geospatial",
]

# Excluded titles: senior roles, outside the target pool
TITLE_SKIP = ["senior", "sr ", "sr.", "staff", "principal", "lead ", "manager",
              "director", "architect", "vice president", " iii", " iv"]

# Staffing/consulting firms: they top the H-1B data but are not target employers
BODYSHOPS = [
    "infosys", "tata consultancy", "cognizant", "wipro", "accenture", "hcl",
    "capgemini", "tech mahindra", "larsen", "lti mindtree", "ltimindtree",
    "deloitte", "ernst", "kpmg", "pricewaterhouse", "pwc", "mphasis",
    "syntel", "ust global", "virtusa", "birlasoft", "hexaware", "zensar",
    "iflex", "polaris", "igate", "ntt data", "dxc", "unisys", "compunnel",
    "sonsoft", "cybertech", "diverse lynx", "photon", "collabera", "artech",
    "randstad", "kforce", "insight global", "apex systems", "robert half",
    "staffing", "consultancy", "consulting services", "solutions inc",
    "technologies inc", "infotech", "softech", "systems inc","university", "college", "institute of technology", "school of",
    "hospital", "medical center", "health system", "national laborator",
    "research institute", "academy", "foundation", "trustees of",
    # National-lab operators: the company name often lacks "national laborator" (e.g.
    # "UChicago Argonne LLC"), so the rule above misses them; list the operators by name.
    "argonne",
]

# Only these states (set to where you would work; empty = all of the US)
STATES = []          # e.g. ["CA", "NY", "WA", "MA", "IL", "TX"]

MIN_COUNT = 1        # minimum number of sponsorships to make the list


def find_col(df, *keys):
    """Column names differ between fiscal years; match them fuzzily."""
    for c in df.columns:
        norm = re.sub(r"[^a-z]", "", str(c).lower())
        if all(re.sub(r"[^a-z]", "", k) in norm for k in keys):
            return c
    return None


def clean_name(name):
    n = str(name).strip().upper()
    n = re.sub(r"[.,]", "", n)
    n = re.sub(r"\b(INC|LLC|LTD|CORP|CORPORATION|COMPANY|CO|LP|LLP|PLC|HOLDINGS|USA|US|AMERICA|NA)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def load(path):
    print(f"Reading {path} ...")
    if path.lower().endswith((".csv", ".txt")):
        df = pd.read_csv(path, low_memory=False)
    else:
        df = pd.read_excel(path)
    print(f"  {len(df):,} rows")
    return df


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)

    frames = []
    for path in sys.argv[1:]:
        df = load(path)
        emp = find_col(df, "employer", "name")
        job = find_col(df, "job", "title")
        st = find_col(df, "worksite", "state") or find_col(df, "employer", "state")
        status = find_col(df, "case", "status")
        case = find_col(df, "case", "number")
        if not (emp and job):
            print(f"  Skipped: no employer-name or job-title column. Actual columns: {list(df.columns)[:12]}")
            continue
        sub = pd.DataFrame({
            "employer": df[emp],
            "title": df[job],
            "state": df[st] if st else "",
            "status": df[status] if status else "CERTIFIED",
            "case": df[case] if case else pd.NA,
        })
        frames.append(sub)

    if not frames:
        sys.exit("No valid data was read")

    df = pd.concat(frames, ignore_index=True)
    print(f"\nTotal {len(df):,} application records")

    # Some DOL quarterly files are cumulative (e.g. FY2021_Q3 covers Q1-Q3), so the same
    # case shows up in several files. Keep the last occurrence: later files carry the
    # latest status (e.g. Certified -> Certified - Withdrawn).
    has_case = df["case"].notna()
    df = pd.concat([
        df[has_case].drop_duplicates("case", keep="last"),
        df[~has_case],
    ], ignore_index=True)
    print(f"After de-duplicating by case number: {len(df):,}")

    # Certified petitions only
    df = df[df["status"].astype(str).str.upper().str.contains("CERTIFIED", na=False)]
    df = df[~df["status"].astype(str).str.upper().str.contains("WITHDRAWN", na=False)]
    print(f"Certified: {len(df):,}")

    t = df["title"].fillna("").astype(str).str.lower()
    df = df[t.apply(lambda x: any(k in x for k in TITLES))]
    print(f"Matching titles: {len(df):,}")

    t = df["title"].fillna("").astype(str).str.lower()
    df = df[~t.apply(lambda x: any(k in x for k in TITLE_SKIP))]
    print(f"After removing senior roles: {len(df):,}")

    if STATES:
        df = df[df["state"].astype(str).str.upper().str[:2].isin(STATES)]
        print(f"After state filter: {len(df):,}")

    df["clean"] = df["employer"].apply(clean_name)
    low = df["clean"].str.lower()
    df = df[~low.apply(lambda x: any(b in x for b in BODYSHOPS))]
    print(f"After removing staffing/consulting: {len(df):,}")

    g = df.groupby("clean").agg(
        sponsor_count=("title", "size"),
        titles=("title", lambda s: " | ".join(sorted(set(s))[:4])),
        states=("state", lambda s: ", ".join(sorted(set(str(x)[:2] for x in s.dropna()))[:5])),
        raw_name=("employer", "first"),
    ).reset_index()

    g = g[g["sponsor_count"] >= MIN_COUNT].sort_values("sponsor_count", ascending=False)

    g[["raw_name", "sponsor_count", "titles", "states"]].to_csv(
        "sponsors.csv", index=False, encoding="utf-8-sig")

    out = pd.DataFrame({
        "company": g["raw_name"],
        "ats": "",
        "slug": g["clean"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True),
        "priority": "",
    })
    out.to_csv("companies_new.csv", index=False, encoding="utf-8-sig")

    print(f"\n{'='*50}")
    print(f"Target companies: {len(g):,}")
    print(f"  sponsors.csv       full list (with titles and states)")
    print(f"  companies_new.csv  input for job_feed.py")
    print("=" * 50)
    print("\nTop 25 companies by sponsorship count:\n")
    for _, r in g.head(25).iterrows():
        print(f"  {r['sponsor_count']:>4}  {str(r['raw_name'])[:38]:<40} {str(r['titles'])[:44]}")


if __name__ == "__main__":
    main()