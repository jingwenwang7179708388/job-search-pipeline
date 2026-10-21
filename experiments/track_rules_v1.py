#!/usr/bin/env python3
"""
experiments/track_rules_v1.py - read-only evaluation of candidate Track rules (v1) on LCA titles

Reads the per-file LCA cache (data/processed/lca_parquet/*.parquet), de-duplicates by
CASE_NUMBER (latest quarter wins), keeps Certified, classifies every JOB_TITLE with the v1
"core word x modifier" rules below, and writes experiments/track_rules_v1.md.
Touches neither lca/process_lca.py nor the database.

Usage (from the project root): python3 experiments/track_rules_v1.py
"""

import glob
import random
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common.names import normalize   # noqa: E402

CACHE_GLOB = str(ROOT / "data" / "processed" / "lca_parquet" / "*.parquet")
OUT_MD = Path(__file__).resolve().parent / "track_rules_v1.md"
SEED = 551

# ------------------------------------------------ title normalization
LEVEL_WORDS = ["senior", "sr", "lead", "staff", "principal", "junior", "jr", "associate",
               "intern", "i", "ii", "iii", "iv", "v"]
ABBREV = {"mle": "machine learning engineer", "swe": "software engineer",
          "sde": "software development engineer", "bi": "business intelligence",
          "ds": "data scientist"}

# ------------------------------------------------ rules: core word -> [(track, modifier regex)]
# Listed order is precedence when one core matches several modifiers.
ML = r"machine learning|ml|mlops|deep learning"
AI = r"ai|artificial intelligence|llm|llms|genai|generative ai"
CORES = [
    ("analyst", r"analysts?", [
        ("DATA_ANALYTICS", r"data|business intelligence|reporting"),
        ("BUSINESS_ANALYTICS", r"business(?! intelligence)|growth|marketing|product|customer|operations|strategy"),
        ("OTHER", r"financial|credit|investment"),
    ]),
    ("scientist", r"scientists?", [
        ("DATA_SCIENCE", r"data|decision|product"),
        ("ML_RESEARCH", rf"research|applied|{ML}|{AI}"),
    ]),
    ("engineer", r"engineers?", [
        ("ML_ENGINEERING", rf"{ML}|{AI}"),
        ("DATA_ENGINEERING", r"data|analytics|etl"),
        ("ML_RESEARCH", r"research"),
        ("SOFTWARE_ENGINEERING", r"software|backend|back end|frontend|front end|full stack|fullstack"),
    ]),
    ("developer", r"developers?|sde", []),   # always SOFTWARE_ENGINEERING
]
SOC_FALLBACK = [("15-2051", "DATA_SCIENCE"), ("15-1221", "ML_RESEARCH"),
                ("13-1111", "BUSINESS_ANALYTICS"), ("13-1161", "BUSINESS_ANALYTICS"),
                ("19-", "OTHER")]

_CORE_RE = [(name, re.compile(rf"\b(?:{pat})\b"), [(t, re.compile(rf"\b(?:{m})\b")) for t, m in mods])
            for name, pat, mods in CORES]
_LEVEL_RE = re.compile(r"\b(" + "|".join(LEVEL_WORDS) + r")\b")
_ABBREV_RE = re.compile(r"\b(" + "|".join(ABBREV) + r")\b")


def normalize_title(raw):
    """-> (normalized title, level word or '')"""
    t = str(raw or "").lower()
    t = re.sub(r"\d+(?:\.\d+)+", " ", t)                     # internal codes like 1615.32817
    t = re.sub(r"[()\[\]]", ",", t)                          # parentheses behave like a comma
    parts = [p.strip() for p in re.split(r",|\s[-–—|]\s|\s[-–—]|[-–—]\s", t)
             if p.strip()]
    t = " ".join(parts[1:] + parts[:1])                      # qualifiers first, main title last
    t = _ABBREV_RE.sub(lambda m: ABBREV[m.group(1)], t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    m = _LEVEL_RE.search(t)
    level = m.group(1) if m else ""
    t = _LEVEL_RE.sub(" ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    return re.sub(r"\s+", " ", t).strip(), level


def classify_title(norm):
    """-> (track or None if no core word, path, sorted set of all tracks hit)"""
    hits, head = [], None
    for name, core_re, mods in _CORE_RE:
        for cm in core_re.finditer(norm):
            if head is None or cm.start() > head[1]:
                head = (name, cm.start())
            if name == "developer":
                hits.append((name, "SOFTWARE_ENGINEERING"))
                continue
            matched = [t for t, mre in mods if mre.search(norm)]
            hits.extend((name, t) for t in matched)
            if not matched:
                hits.append((name, None))
    if head is None:
        return None, "no core word", frozenset()
    head_tracks = [t for n, t in hits if n == head[0]]
    explicit = [t for t in head_tracks if t]
    track = explicit[0] if explicit else "OTHER"
    if head[0] == "developer":
        path = "developer:always"
    else:
        path = f"{head[0]}:" + ("modifier" if explicit else "no-modifier")
    return track, path, frozenset(t for _, t in hits if t)


def soc_track(soc):
    s = str(soc or "").strip()
    for prefix, track in SOC_FALLBACK:
        if s.startswith(prefix):
            return track
    return "OTHER"


def load():
    frames = [pd.read_parquet(f, columns=["case_number", "status", "employer", "title",
                                          "soc_code", "fy", "q"]) for f in sorted(glob.glob(CACHE_GLOB))]
    df = pd.concat(frames, ignore_index=True)
    n_raw = len(df)
    df["case_number"] = df["case_number"].str.strip()
    df = df[df["case_number"].notna() & (df["case_number"] != "")]
    df = df.sort_values(["fy", "q"], kind="stable").drop_duplicates("case_number", keep="last")
    n_dedup = len(df)
    df = df[df["status"].str.strip().str.lower() == "certified"].copy()
    return df, n_raw, n_dedup


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(out)


def observations(df, total):
    """Counts behind the issues noted in the summary (section 5)."""
    out = ["## 5. Observations (counts for issues seen above)", ""]
    soc = df["soc_code"].fillna("").str.strip()

    mr = df[df["track"] == "ML_RESEARCH"]
    grp = mr["soc_code"].fillna("").str.strip().map(
        lambda s: "15-1221 (CS research)" if s.startswith("15-1221") else
                  "15-2051 (data science)" if s.startswith("15-2051") else
                  "19-xxxx (life/physical science)" if s.startswith("19-") else "other SOC")
    out += ["### ML_RESEARCH by SOC group", "",
            md_table(["SOC group", "filings", "% of ML_RESEARCH"],
                     [(k, f"{v:,}", f"{100 * v / len(mr):.1f}%") for k, v in grp.value_counts().items()]), ""]

    fb = df[df["path"] == "SOC fallback"]
    rows = []
    for t in ["DATA_SCIENCE", "ML_RESEARCH", "BUSINESS_ANALYTICS"]:
        sub = fb[fb["track"] == t]
        top = ", ".join(f"{k} ({v:,})" for k, v in sub["soc_code"].fillna("").str.strip().value_counts().head(3).items())
        ex = ", ".join(sub["norm"].value_counts().head(6).index)
        rows.append((t, f"{len(sub):,}", top, ex))
    out += ["### Tracks assigned by SOC fallback (no core word in the title)", "",
            "Note: SOC 15-2051.01 is *Business Intelligence Analysts*; the `15-2051` prefix rule "
            "also catches it.", "",
            md_table(["Track", "filings", "top SOC codes", "most common normalized titles"], rows), ""]

    fam = [
        ("business systems analyst", r"\bbusiness systems analysts?\b"),
        ("operations research analyst", r"\boperations research analysts?\b"),
        ("business intelligence engineer / developer", r"\bbusiness intelligence (?:engineer|developer)s?\b"),
        ("'software engineering' (no core word)", r"^software engineering$"),
        ("data center ... engineer", r"\bdata cent(?:er|re)\b.*\bengineers?\b"),
        ("member of (the) technical (staff)", r"\bmember of (?:the )?technical\b"),
        ("statistician", r"\bstatisticians?\b"),
        ("quantitative analyst / researcher", r"\bquantitative (?:analyst|researcher)s?\b"),
        ("computer vision / nlp (not in ML/AI lists)", r"\bcomputer vision\b|\bnlp\b|\bnatural language\b"),
        ("data science (no core word)", r"\bdata science\b(?!.*\b(?:scientist|engineer|analyst|developer)s?\b)"),
        ("empty after removing level words", r"^$"),
    ]
    rows = []
    for label, pat in fam:
        sub = df[df["norm"].str.contains(pat, regex=True)]
        tr = ", ".join(f"{k} ({v:,})" for k, v in sub["track"].value_counts().head(3).items())
        rows.append((label, f"{len(sub):,}", tr))
    out += ["### Title families worth a decision", "",
            md_table(["title family (normalized)", "filings", "assigned Track (top 3)"], rows), ""]
    return out


def main():
    df, n_raw, n_dedup = load()
    print(f"{n_raw:,} raw -> {n_dedup:,} after CASE de-dup -> {len(df):,} Certified", flush=True)

    titles = df["title"].fillna("").unique()
    print(f"classifying {len(titles):,} distinct titles ...", flush=True)
    tinfo = {}
    for raw in titles:
        norm, level = normalize_title(raw)
        track, path, hit = classify_title(norm)
        tinfo[raw] = (norm, level, track, path, hit)
    df["title"] = df["title"].fillna("")
    df["norm"] = df["title"].map(lambda r: tinfo[r][0])
    df["level"] = df["title"].map(lambda r: tinfo[r][1])
    df["path"] = df["title"].map(lambda r: tinfo[r][3])
    df["hits"] = df["title"].map(lambda r: tinfo[r][4])
    title_track = df["title"].map(lambda r: tinfo[r][2])
    soc_based = title_track.isna()
    df["track"] = title_track.where(~soc_based, df["soc_code"].map(soc_track))
    df.loc[soc_based, "path"] = "SOC fallback"

    emp = df["employer"].fillna("").unique()
    emap = {e: normalize(e) for e in emp}
    df["company"] = df["employer"].fillna("").map(emap)

    order = ["DATA_ANALYTICS", "BUSINESS_ANALYTICS", "DATA_SCIENCE", "ML_RESEARCH",
             "ML_ENGINEERING", "DATA_ENGINEERING", "SOFTWARE_ENGINEERING", "OTHER"]
    total = len(df)
    lines = [
        "# Track rules v1 - evaluation on LCA titles",
        "",
        "Generated by `experiments/track_rules_v1.py` (read-only; `lca/process_lca.py` and the",
        "database are unchanged). Data: all 25 cached LCA files, de-duplicated by CASE_NUMBER",
        f"(latest quarter wins), Certified only: **{total:,} filings** "
        f"({n_raw:,} raw, {n_dedup:,} after de-dup). Staffing firms / universities are *not*",
        "removed here, unlike `process_lca.py`.",
        "",
        "## Rules as implemented",
        "",
        "Normalization: lowercase; strip internal codes (e.g. `1615.32817`); text after a comma,",
        "dash or inside parentheses is moved in front of the main title; expand MLE / SWE / SDE /",
        "BI / DS; record and remove the first level word (" + ", ".join(LEVEL_WORDS) + "); drop",
        "bare numbers.",
        "",
        "Classification: find core words (analyst, scientist, engineer, developer/SDE). The head",
        "core is the last one in the normalized title (= the main title's noun after reordering).",
        "The head core's modifiers decide the Track; when several match, the first in this list",
        "wins. A core with no matching modifier -> OTHER. No core word at all -> SOC fallback.",
        "",
        md_table(["core", "Track (precedence order) : modifiers"],
                 [(n, "<br>".join(f"{t}: `{m}`" for t, m in mods) or "SOFTWARE_ENGINEERING (always)")
                  for n, _, mods in CORES]),
        "",
        "SOC fallback: " + ", ".join(f"{p} -> {t}" for p, t in SOC_FALLBACK) + ", anything else -> OTHER.",
        "",
        "Choices not specified in the request (please confirm): ML = `" + ML + "`, AI = `" + AI +
        "` (computer vision / NLP are *not* included); analyst/engineer with no matching",
        "modifier -> OTHER (not SOC); conflicts resolved by the precedence order above.",
        "",
        "## 1. Filings and companies per Track",
        "",
        "Companies = distinct normalized employer names (`common.names.normalize`). "
        "Sorted by filings.",
        "",
    ]
    g = df.groupby("track").agg(filings=("case_number", "size"), companies=("company", "nunique"))
    g = g.reindex([o for o in order if o in g.index]).sort_values("filings", ascending=False)
    lines.append(md_table(["Track", "filings", "% of filings", "companies"],
                          [(t, f"{r.filings:,}", f"{100 * r.filings / total:.1f}%", f"{r.companies:,}")
                           for t, r in g.iterrows()]))
    p = df["path"].value_counts()
    lines += ["", "How each filing was classified:", "",
              md_table(["path", "filings", "%"],
                       [(k, f"{v:,}", f"{100 * v / total:.1f}%") for k, v in p.items()]), ""]

    # 2. top 50 OTHER titles
    oth = df[df["track"] == "OTHER"]
    top = (oth.groupby("norm").agg(filings=("case_number", "size"),
                                   path=("path", lambda s: s.mode().iat[0]),
                                   soc=("soc_code", lambda s: ", ".join(f"{k} ({v:,})" for k, v in s.value_counts().head(2).items())),
                                   example=("title", lambda s: s.mode().iat[0]))
              .sort_values("filings", ascending=False).head(50))
    lines += ["## 2. Top 50 OTHER titles (by filings)", "",
              f"OTHER total: {len(oth):,} filings. Grouped by normalized title; SOC = the two most "
              "common SOC codes.", "",
              md_table(["#", "normalized title", "example raw title", "filings", "path", "SOC (filings)"],
                       [(i + 1, n, r.example, f"{r.filings:,}", r.path, r.soc)
                        for i, (n, r) in enumerate(top.iterrows())]), ""]

    # 3. random 20 per Track (sampled over filings, so frequent titles show up more)
    lines += ["## 3. Random sample: 20 filings per Track", "",
              f"`random_state={SEED}`, sampled over filings.", ""]
    for t in g.index:
        s = df[df["track"] == t].sample(min(20, (df["track"] == t).sum()), random_state=SEED)
        lines += [f"### {t}", "",
                  md_table(["raw title", "normalized", "level", "path", "SOC"],
                           [(r.title, r.norm, r.level, r.path, r.soc_code) for r in s.itertuples()]), ""]

    # 4. conflicts: titles hitting >= 2 Tracks
    conf = df[df["hits"].map(len) >= 2]
    pairs = Counter()
    for h, n in conf.groupby("hits").size().items():
        pairs[" + ".join(sorted(h))] += n
    lines += ["## 4. Conflicts: titles hitting two or more Tracks", "",
              f"{len(conf):,} filings ({100 * len(conf) / total:.1f}%) hit 2+ Tracks. "
              "Assigned Track = the precedence rule above.", "",
              md_table(["Tracks hit", "filings"], [(k, f"{v:,}") for k, v in pairs.most_common()]), ""]
    rng = random.Random(SEED)
    for combo, _ in pairs.most_common():
        sub = conf[conf["hits"].map(lambda h: " + ".join(sorted(h))) == combo]
        top_t = sub.groupby("title").agg(filings=("case_number", "size"), norm=("norm", "first"),
                                         track=("track", "first")).sort_values("filings", ascending=False)
        pick = top_t.head(10)
        lines += [f"### {combo}", "",
                  "10 most frequent titles:", "",
                  md_table(["raw title", "normalized", "assigned", "filings"],
                           [(t, r.norm, r.track, f"{r.filings:,}") for t, r in pick.iterrows()]), ""]

    lines += observations(df, total)
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(g.to_string())


if __name__ == "__main__":
    main()
