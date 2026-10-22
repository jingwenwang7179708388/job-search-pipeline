#!/usr/bin/env python3
"""
experiments/score_track_labels.py - per-Track precision of the frozen Track rules on hand labels

Input: experiments/track_labels_v4_hand.csv (220 rows, hand-labeled blind by Jingwen on 2026-10-21).
  These rows are the 20-per-Track sample drawn by an EARLY run of v4, before three fixes
  (Computer Programmer, plain Analyst + computer SOC, Statistical Programmer) were added and v4 was
  frozen. So the ids do NOT match experiments/track_labels_v4_key.csv. Instead of using the key
  file, this script re-classifies every row with the frozen rules, so the comparison is still blind.
  Columns:
    label_raw            the label as first written (UNSURE allowed)
    label_by_definition  label_raw, with 19 rows changed where the label contradicted docs/TRACKS.md
                         (decided 2026-10-21: "all per definition"); correction_note says why

Output: precision per predicted Track (rows labeled UNSURE are left out) for
  v4 vs label_raw, v4 vs label_by_definition, v5 vs label_by_definition.
  v5 was fixed after looking at these errors, so its number on this same sample is optimistic.

Read-only. Usage (from the project root): python3 experiments/score_track_labels.py
"""

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import track_rules_v4 as v4   # noqa: E402  frozen, commit 1d421e7
import track_rules_v5 as v5   # noqa: E402  frozen, commit ff07fa0

LABELS = HERE / "track_labels_v4_hand.csv"


def predict(mod, title, soc):
    pre, norm, _ = mod.normalize_title(title)
    return mod.classify(pre, norm, soc)[0]


def precision(rows, label_col, pred_col, track=None):
    """-> (correct, scored) over rows predicted as `track` (all rows if None), UNSURE excluded."""
    g = [r for r in rows if r[label_col] != "UNSURE" and (track is None or r[pred_col] == track)]
    return sum(r[label_col] == r[pred_col] for r in g), len(g)


def main():
    with open(LABELS, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["label_raw"] = r["label_raw"].strip().upper()
        r["label_by_definition"] = r["label_by_definition"].strip().upper()
        r["v4"] = predict(v4, r["raw_title"], r["soc_code"])
        r["v5"] = predict(v5, r["raw_title"], r["soc_code"])
    unknown = {r["label_raw"] for r in rows} - set(v5.TRACKS) - {"UNSURE"}
    if unknown:
        sys.exit(f"unknown labels: {sorted(unknown)}")

    cols = [("v4 / raw labels", "label_raw", "v4"),
            ("v4 / by definition", "label_by_definition", "v4"),
            ("v5 / by definition", "label_by_definition", "v5")]
    print(f"{len(rows)} labeled rows, {sum(r['label_raw'] == 'UNSURE' for r in rows)} UNSURE\n")
    print(f"{'Track':22}" + "".join(f"{c[0]:>22}" for c in cols))
    for t in v5.TRACKS + [None]:
        cells = []
        for _, lab, pred in cols:
            ok, n = precision(rows, lab, pred, t)
            cells.append(f"{ok}/{n} {ok / n:.0%}" if n else "-")
        print(f"{(t or 'ALL'):22}" + "".join(f"{c:>22}" for c in cells))

    print("\nRemaining v5 errors (label_by_definition != v5):")
    for r in rows:
        if r["label_by_definition"] not in ("UNSURE", r["v5"]):
            print(f"  {r['id']:>3} {r['raw_title'][:50]:50} {r['soc_code']:11} "
                  f"label={r['label_by_definition']} v5={r['v5']}")


if __name__ == "__main__":
    main()
