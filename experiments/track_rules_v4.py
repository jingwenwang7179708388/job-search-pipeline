#!/usr/bin/env python3
"""
experiments/track_rules_v4.py - Track rules v4: an implementation of docs/TRACKS.md (v1, confirmed)

Input and filters are track_rules_v3.load() unchanged: all 25 cached LCA files, CASE_NUMBER
de-dup (latest quarter wins), Certified, BODYSHOPS removed. Read-only: no MySQL, and it only
writes these new files:
  experiments/track_rules_v4.md        counts per Track, top raw titles per Track, top OTHER titles
  experiments/track_labels_v4.csv      20 random filings per Track, shuffled: id, raw_title, soc_code, my_label
  experiments/track_labels_v4_key.csv  id, predicted_track (kept apart so labeling is blind)

Order of evaluation (docs/TRACKS.md; SOC only vetoes or breaks ties on vague titles):
  1. SOC veto: 17-, 19-, 25-, 29- -> OTHER
  2. Member of Technical Staff -> by SOC
  3. Empty after removing level words -> OTHER
  4. Title families (settled edge cases and Includes lists with no core word)
  5. Core word (head = last of analyst / scientist / engineer / developer / sde), its modifiers in
     the precedence order of docs/TRACKS.md; no modifier -> SOC tie-break
  6. No core word: vague data/ML titles -> SOC tie-break, otherwise OTHER

Usage (from the project root): python3 experiments/track_rules_v4.py
"""

import csv
import random
import re
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from track_rules_v3 import load        # noqa: E402  same input and filters as v3
from track_rules_v1 import md_table    # noqa: E402

OUT_MD = HERE / "track_rules_v4.md"
OUT_LABELS = HERE / "track_labels_v4.csv"
OUT_KEY = HERE / "track_labels_v4_key.csv"
SEED = 551
PER_TRACK = 20

TRACKS = ["DATA_ANALYTICS", "BUSINESS_ANALYTICS", "SYSTEMS_ANALYSIS", "FINANCIAL_ANALYTICS", "QUANT",
          "DATA_SCIENCE", "DATA_ENGINEERING", "ML_ENGINEERING", "ML_RESEARCH",
          "SOFTWARE_ENGINEERING", "OTHER"]

# ------------------------------------------------ title normalization (as v3, plus SRE)
LEVEL_WORDS = ["senior", "sr", "lead", "staff", "principal", "junior", "jr", "associate",
               "intern", "i", "ii", "iii", "iv", "v",
               "manager", "managers", "director", "head", "vp"]
ABBREV = {"mle": "machine learning engineer", "swe": "software engineer",
          "sde": "software development engineer", "bi": "business intelligence",
          "ds": "data scientist", "sre": "site reliability engineer"}
_LEVEL_RE = re.compile(r"\b(" + "|".join(LEVEL_WORDS) + r")\b")
_ABBREV_RE = re.compile(r"\b(" + "|".join(ABBREV) + r")\b")


def normalize_title(raw):
    """-> (pre: normalized, level words kept; norm: level words removed; levels)"""
    t = str(raw or "").lower()
    t = re.sub(r"\d+(?:\.\d+)+", " ", t)                     # internal codes like 1615.32817
    t = re.sub(r"[()\[\]]", ",", t)                          # parentheses behave like a comma
    parts = [p.strip() for p in re.split(r",|\s[-–—|]\s|\s[-–—]|[-–—]\s", t)
             if p.strip()]
    t = " ".join(parts[1:] + parts[:1])                      # qualifiers first, main title last
    t = _ABBREV_RE.sub(lambda m: ABBREV[m.group(1)], t)
    pre = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()
    levels = " ".join(dict.fromkeys(_LEVEL_RE.findall(pre)))
    norm = re.sub(r"\b\d+\b", " ", _LEVEL_RE.sub(" ", pre))
    return pre, re.sub(r"\s+", " ", norm).strip(), levels


def w(pattern):
    """Whole-word regex."""
    return re.compile(rf"\b(?:{pattern})\b")


# ------------------------------------------------ vocabulary (docs/TRACKS.md)
QUANT_W = w(r"quant|quants|quantitative")
NONFIN_QUANT = w(r"user|ux|marketing|survey|clinical|health|behavioral|social|market research")
IT_SYSTEM = w(r"sap|oracle|salesforce|sfdc|guidewire|erp|crm|servicenow|workday|it")
FIN_W = w(r"financial|finance|credit|risk|investment|investments|accounting")
DA_W = w(r"data|business intelligence|reporting")
BA_W = w(r"growth|marketing|product|customer|operations|strategy")
SYS_W = w(r"business systems|systems")
BUSINESS = w(r"business")
DS_SCI = w(r"data|decision|product|operations research")
ML = r"machine learning|ml|mlops|deep learning|computer vision|cv|nlp|natural language processing"
AI = r"ai|artificial intelligence|llm|llms|genai|generative ai"
MLR_SCI = w(rf"research|applied|{ML}|{AI}")
BI_ENG = w(r"business intelligence|tableau|power business intelligence|looker")
DATA_CENTER = w(r"data cent(?:er|re)")
RESEARCH = w(r"research")
ML_ENG = w(rf"{ML}|{AI}")
DE_ENG = w(r"data|analytics|etl|big data|data warehouse")
SWE_ENG = w(r"software|backend|back end|frontend|front end|full stack|fullstack|mobile|ios|android|"
            r"web|devops|site reliability|qa|quality assurance|test automation|test|automation|"
            r"java|javascript|python|net|golang|react|node|ui")
OTHER_ENG = w(r"network|security|cybersecurity|cyber|systems|infrastructure|hardware|solutions|"
              r"sales|support|field")
CORE = w(r"analyst|analysts|scientist|scientists|engineer|engineers|developer|developers|sde")
CORE_NAME = {"analyst": "analyst", "analysts": "analyst", "scientist": "scientist",
             "scientists": "scientist", "engineer": "engineer", "engineers": "engineer",
             "developer": "engineer", "developers": "engineer", "sde": "engineer"}
MTS = w(r"member of (?:the )?technical staff")
VAGUE_GATE = w(r"data science|analytics|statistic\w*|machine learning|ai|research\w*")

# ------------------------------------------------ SOC (veto and tie-break only)
SOC_VETO = ("17-", "19-", "25-", "29-")


def soc_in(soc, *patterns):
    return any(re.match(p, soc) for p in patterns)


DS_SOC = (r"15-2051(?:\.00)?$", r"15-2041")
DA_SOC = (r"15-2051\.01", r"15-1199\.08")
MLR_SOC = (r"15-1221", r"15-1111")
SWE_SOC = (r"15-125\d", r"15-113\d")
FIN_SOC = (r"13-2051", r"13-2054", r"13-2041")
QUANT_SOC = (r"13-2099\.01",)
BA_SOC = (r"15-2051\.01", r"15-1199\.08", r"15-2041", r"15-2031", r"15-2051(?:\.00)?$", r"13-1161")
# Computer-occupation SOC: a plain "business analyst" or "analyst" with one of these ->
# SYSTEMS_ANALYSIS. Always checked after the analytical SOCs, so 15-1199.08 (BI analysts)
# stays analytical.
COMPUTER_SOC = (r"15-1211", r"15-1121", r"15-1199", r"15-125\d", r"15-113\d")


def analyst_by_soc(soc):
    """Analyst with no direction word: SOC breaks the tie among the Analyst Tracks."""
    if soc_in(soc, *DA_SOC):
        return "DATA_ANALYTICS"
    if soc_in(soc, *BA_SOC):
        return "BUSINESS_ANALYTICS"
    if soc_in(soc, *COMPUTER_SOC):
        return "SYSTEMS_ANALYSIS"
    if soc_in(soc, *FIN_SOC):
        return "FINANCIAL_ANALYTICS"
    if soc_in(soc, *QUANT_SOC):
        return "QUANT"
    return "OTHER"


def vague_by_soc(soc):
    """No core word but a data/ML word: SOC breaks the tie."""
    if soc_in(soc, *DA_SOC):
        return "DATA_ANALYTICS"
    if soc_in(soc, *DS_SOC):
        return "DATA_SCIENCE"
    if soc_in(soc, *MLR_SOC):
        return "ML_RESEARCH"
    if soc_in(soc, *SWE_SOC):
        return "SOFTWARE_ENGINEERING"
    return "OTHER"


def is_quant(t):
    return bool(QUANT_W.search(t)) and not NONFIN_QUANT.search(t)


# ------------------------------------------------ title families (checked before core words)
FAMILIES = [
    ("UX / user researcher", w(r"(?:ux|user) research(?:ers?)?"), "OTHER"),
    ("operations research analyst/scientist", w(r"operations research (?:analyst|scientist)s?"), "DATA_SCIENCE"),
    ("programmer analyst", w(r"programmer analysts?"), "SOFTWARE_ENGINEERING"),
    ("statistical programmer", w(r"statistical programmers?"), "DATA_ANALYTICS"),   # before "programmer"
    ("programmer", w(r"(?:computer )?programmers?"), "SOFTWARE_ENGINEERING"),
    ("statistician", w(r"statisticians?"), "DATA_SCIENCE"),
    ("quant trader/strategist/researcher", w(r"(?:quant|quantitative) (?:trader|strategist|researcher)s?|quant"), "QUANT"),
    ("data architect", w(r"data architects?"), "DATA_ENGINEERING"),
    ("data science (no core word)", w(r"data science"), "DATA_SCIENCE"),
    ("software engineering/development (no core word)", w(r"software (?:engineering|development)"), "SOFTWARE_ENGINEERING"),
]
# families that only apply when the title has no core word
NO_CORE_FAMILIES = {"data science (no core word)", "software engineering/development (no core word)"}


def classify(pre, norm, soc):
    """-> (track, rule path)"""
    soc = str(soc or "").strip()
    if soc.startswith(SOC_VETO):
        return "OTHER", "SOC veto"
    if MTS.search(pre):
        if soc_in(soc, *MLR_SOC):
            return "ML_RESEARCH", "MTS -> SOC"
        if soc_in(soc, *SWE_SOC):
            return "SOFTWARE_ENGINEERING", "MTS -> SOC"
        return "OTHER", "MTS -> SOC"
    if not norm:
        return "OTHER", "empty after level words"

    cores = [CORE_NAME[m.group(0)] for m in CORE.finditer(norm)]
    head = cores[-1] if cores else None
    for name, rx, track in FAMILIES:
        if name in NO_CORE_FAMILIES and head:
            continue
        if name.startswith("quant") and not is_quant(norm):
            continue
        if rx.search(norm):
            return track, f"family: {name}"

    if head == "analyst":
        if IT_SYSTEM.search(norm):
            return "SYSTEMS_ANALYSIS", "analyst: IT system"
        if is_quant(norm):
            return "QUANT", "analyst: quant"
        if FIN_W.search(norm):
            return "FINANCIAL_ANALYTICS", "analyst: finance"
        if DA_W.search(norm):
            return "DATA_ANALYTICS", "analyst: data"
        if BA_W.search(norm):
            return "BUSINESS_ANALYTICS", "analyst: business function"
        if SYS_W.search(norm):
            return "SYSTEMS_ANALYSIS", "analyst: systems"
        if BUSINESS.search(norm):
            if soc_in(soc, *BA_SOC):
                return "BUSINESS_ANALYTICS", "business analyst -> SOC"
            if soc_in(soc, *COMPUTER_SOC):
                return "SYSTEMS_ANALYSIS", "business analyst -> SOC"
            return "OTHER", "business analyst -> SOC"
        return analyst_by_soc(soc), "analyst: no direction -> SOC"

    if head == "scientist":
        if is_quant(norm):
            return "QUANT", "scientist: quant"
        if DS_SCI.search(norm):
            return "DATA_SCIENCE", "scientist: data"
        if MLR_SCI.search(norm):
            return "ML_RESEARCH", "scientist: research/ML"
        if soc_in(soc, *DS_SOC):
            return "DATA_SCIENCE", "scientist: no direction -> SOC"
        if soc_in(soc, *MLR_SOC):
            return "ML_RESEARCH", "scientist: no direction -> SOC"
        return "OTHER", "scientist: no direction -> SOC"

    if head == "engineer":                     # engineer / developer / SDE
        if is_quant(norm):
            return "QUANT", "engineer: quant"
        if BI_ENG.search(norm):
            return "DATA_ANALYTICS", "engineer: BI"
        if DATA_CENTER.search(norm):
            return "OTHER", "engineer: data center"
        if RESEARCH.search(norm):
            return "ML_RESEARCH", "engineer: research"
        if ML_ENG.search(norm):
            return "ML_ENGINEERING", "engineer: ML/AI"
        if DE_ENG.search(norm):
            return "DATA_ENGINEERING", "engineer: data"
        if SWE_ENG.search(norm):
            return "SOFTWARE_ENGINEERING", "engineer: software"
        if OTHER_ENG.search(norm):
            return "OTHER", "engineer: IT infrastructure/other"
        if soc_in(soc, *SWE_SOC):
            return "SOFTWARE_ENGINEERING", "engineer: no direction -> SOC"
        return "OTHER", "engineer: no direction -> SOC"

    if VAGUE_GATE.search(pre):
        return vague_by_soc(soc), "no core word: vague -> SOC"
    return "OTHER", "no core word"


def main():
    df, n_raw, n_dedup, n_cert = load()
    total = len(df)
    print(f"{n_raw:,} raw -> {n_dedup:,} de-dup -> {n_cert:,} Certified -> {total:,} after BODYSHOPS",
          flush=True)

    norm_cache = {t: normalize_title(t) for t in df["title"].unique()}
    pairs = df[["title", "soc_code"]].drop_duplicates()
    print(f"classifying {len(pairs):,} distinct (title, SOC) pairs ...", flush=True)
    res = {(t, s): classify(norm_cache[t][0], norm_cache[t][1], s)
           for t, s in pairs.itertuples(index=False)}
    key = list(zip(df["title"], df["soc_code"]))
    df["track"] = [res[k][0] for k in key]
    df["path"] = [res[k][1] for k in key]

    # ---------------- report
    g = df.groupby("track").agg(filings=("case_number", "size"), companies=("company", "nunique"))
    g = g.reindex([t for t in TRACKS if t in g.index])
    lines = [
        "# Track rules v4 - docs/TRACKS.md on LCA titles",
        "",
        "Generated by `experiments/track_rules_v4.py` (read-only). Same input and filters as v3:",
        f"**{total:,} filings** ({n_raw:,} raw -> {n_dedup:,} de-dup -> {n_cert:,} Certified ->",
        f"{total:,} after BODYSHOPS). Rules: see the module docstring and `docs/TRACKS.md`.",
        "",
        "## 1. Filings per Track",
        "",
        md_table(["Track", "filings", "% of filings", "companies"],
                 [(t, f"{r.filings:,}", f"{100 * r.filings / total:.1f}%", f"{r.companies:,}")
                  for t, r in g.iterrows()]),
        "",
        "Rule paths:",
        "",
        md_table(["rule path", "filings", "%"],
                 [(k, f"{v:,}", f"{100 * v / total:.1f}%") for k, v in df["path"].value_counts().items()]),
        "",
        "## 2. Top 15 raw titles per Track",
        "",
    ]
    for t in g.index:
        top = df[df["track"] == t]["title"].value_counts().head(15)
        lines += [f"### {t}", "", md_table(["raw title", "filings"], [(k, f"{v:,}") for k, v in top.items()]), ""]
    oth = df[df["track"] == "OTHER"]
    top = (oth.groupby("title").agg(filings=("case_number", "size"),
                                    path=("path", lambda s: s.mode().iat[0]),
                                    soc=("soc_code", lambda s: s.mode().iat[0]))
              .sort_values("filings", ascending=False).head(30))
    lines += ["## 3. Top 30 raw titles in OTHER", "",
              md_table(["#", "raw title", "filings", "rule path", "most common SOC"],
                       [(i + 1, k, f"{r.filings:,}", r.path, r.soc) for i, (k, r) in enumerate(top.iterrows())]),
              ""]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------------- blind labeling sample: shuffled so row order reveals nothing
    samples = [df[df["track"] == t].sample(min(PER_TRACK, (df["track"] == t).sum()), random_state=SEED)
               for t in TRACKS if (df["track"] == t).any()]
    rows = [(r.title, r.soc_code, r.track) for s in samples for r in s.itertuples()]
    random.Random(SEED).shuffle(rows)
    with open(OUT_LABELS, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "raw_title", "soc_code", "my_label"])
        wr.writerows((i, title, soc, "") for i, (title, soc, _) in enumerate(rows, 1))
    with open(OUT_KEY, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "predicted_track"])
        wr.writerows((i, track) for i, (_, _, track) in enumerate(rows, 1))

    print(f"wrote {OUT_MD.name}, {OUT_LABELS.name} ({len(rows)} rows), {OUT_KEY.name}")
    print(g.assign(pct=(100 * g.filings / total).round(1)).to_string())


if __name__ == "__main__":
    main()
