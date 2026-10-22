#!/usr/bin/env python3
"""
experiments/check_common_tracks.py - check that common/tracks.py matches the frozen track_rules_v5

For every distinct (title, SOC) pair in the LCA data (same input and filters as v3/v4/v5),
compares common.tracks.classify_title(title, soc) with track_rules_v5.classify(). Also checks,
over every distinct title, that soc_code=None gives exactly the same result as an empty SOC ""
(ATS postings have no SOC; the LCA data has none without one). Read-only; prints the result and
exits with status 1 if anything differs.

Usage (from the project root): python3 experiments/check_common_tracks.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from common.tracks import classify_title          # noqa: E402
from track_rules_v3 import load                   # noqa: E402
import track_rules_v5 as v5                       # noqa: E402

MAX_EXAMPLES = 20


def main():
    df, *_ = load()
    pairs = df[["title", "soc_code"]].drop_duplicates()
    print(f"{len(df):,} filings, {len(pairs):,} distinct (title, SOC) pairs", flush=True)

    track_diff, path_diff = [], 0
    for title, soc in pairs.itertuples(index=False):
        want = v5.classify(*v5.normalize_title(title)[:2], soc)
        got = classify_title(title, soc)
        if got[0] != want[0]:
            track_diff.append((title, soc, want[0], got[0]))
        elif got[1] != want[1]:
            path_diff += 1

    titles = df["title"].unique()
    none_diff = [t for t in titles if classify_title(t, None) != classify_title(t, "")]

    print(f"Track mismatches vs v5:       {len(track_diff):,}")
    print(f"Rule-path mismatches (same Track): {path_diff:,}")
    print(f"soc_code=None vs '' mismatches: {len(none_diff):,} (over {len(titles):,} distinct titles)")
    for title, soc, want, got in track_diff[:MAX_EXAMPLES]:
        print(f"  {title!r} SOC={soc!r}: v5={want} common={got}")
    for title in none_diff[:MAX_EXAMPLES]:
        print(f"  {title!r}: None -> {classify_title(title, None)}, '' -> {classify_title(title, '')}")
    sys.exit(1 if track_diff or path_diff or none_diff else 0)


if __name__ == "__main__":
    main()
