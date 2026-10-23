#!/usr/bin/env python3
"""
experiments/check_common_tracks.py - check that common/tracks.py matches the frozen track_rules_v6

1. For every distinct (title, SOC) pair in the LCA data (same input and filters as v3-v6),
   compares common.tracks.classify_title(title, soc) with track_rules_v6.classify().
2. Over every distinct LCA title, checks that soc_code=None gives exactly the same result as an
   empty SOC "" (the LCA data has no row without a SOC).
3. For every distinct title in the jobs table (ATS postings, no SOC), compares
   classify_title(title, None) with track_rules_v6.classify(..., None), and None with "".
Read-only (SELECT from jobs only); prints the result and exits with status 1 if anything differs.

Usage (from the project root): python3 experiments/check_common_tracks.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from common.db import get_conn                    # noqa: E402
from common.tracks import classify_title          # noqa: E402
from track_rules_v3 import load                   # noqa: E402
import track_rules_v6 as v6                       # noqa: E402

MAX_EXAMPLES = 20


def compare(items):
    """items: (title, soc) -> (Track mismatches as (title, soc, v6, common), rule-path mismatch count)."""
    track_diff, path_diff = [], 0
    for title, soc in items:
        want = v6.classify(*v6.normalize_title(title)[:2], soc)
        got = classify_title(title, soc)
        if got[0] != want[0]:
            track_diff.append((title, soc, want[0], got[0]))
        elif got[1] != want[1]:
            path_diff += 1
    return track_diff, path_diff


def main():
    df, *_ = load()
    pairs = df[["title", "soc_code"]].drop_duplicates()
    print(f"LCA: {len(df):,} filings, {len(pairs):,} distinct (title, SOC) pairs", flush=True)
    lca_track, lca_path = compare(pairs.itertuples(index=False))
    titles = df["title"].unique()
    lca_none = [t for t in titles if classify_title(t, None) != classify_title(t, "")]

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT title FROM jobs")
        job_titles = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    job_track, job_path = compare((t, None) for t in job_titles)
    job_none = [t for t in job_titles if classify_title(t, None) != classify_title(t, "")]

    print(f"LCA  Track mismatches vs v6:            {len(lca_track):,}")
    print(f"LCA  rule-path mismatches (same Track): {lca_path:,}")
    print(f"LCA  soc_code=None vs '' mismatches:    {len(lca_none):,} (over {len(titles):,} distinct titles)")
    print(f"jobs Track mismatches vs v6 (no SOC):   {len(job_track):,} (over {len(job_titles):,} distinct titles)")
    print(f"jobs rule-path mismatches (same Track): {job_path:,}")
    print(f"jobs soc_code=None vs '' mismatches:    {len(job_none):,}")
    for title, soc, want, got in (lca_track + job_track)[:MAX_EXAMPLES]:
        print(f"  {title!r} SOC={soc!r}: v6={want} common={got}")
    for title in (lca_none + job_none)[:MAX_EXAMPLES]:
        print(f"  {title!r}: None -> {classify_title(title, None)}, '' -> {classify_title(title, '')}")
    bad = lca_track or lca_path or lca_none or job_track or job_path or job_none
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
