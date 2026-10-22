#!/usr/bin/env python3
"""
experiments/track_rules_v5.py - Track rules v5: v4 (frozen) plus three fixes, per docs/TRACKS.md

Input and filters are track_rules_v3.load() unchanged. Read-only: no MySQL; writes only
experiments/track_rules_v5.md (counts per Track, change vs v4 on the same filings, top OTHER
titles). No labeling sample.

Changes from v4:
  1. QA / testing analysts -> SOFTWARE_ENGINEERING (before the Analyst rules): an analyst title
     with qa / quality assurance / software quality / test / testing / tester (but not A/B or
     experiment), or with quality / assurance and SOC 15-1253 or 15-1199.01.
  2. "operations research" / "operation research" anywhere -> DATA_SCIENCE, as a title family,
     so it is checked before every "research" rule (Operations Research Engineer included).
  3. No core word, mentions analytics but not data science / machine learning / ai /
     consultant -> DATA_ANALYTICS, before the vague-title SOC tie-break.
  4. No core word and "consultant" -> OTHER regardless of SOC (title families still win).

Order of evaluation (docs/TRACKS.md; SOC only vetoes or breaks ties on vague titles):
  1. SOC veto: 17-, 19-, 25-, 29- -> OTHER
  2. Member of Technical Staff -> by SOC
  3. Empty after removing level words -> OTHER
  4. Title families (settled edge cases and Includes lists with no core word)
  5. Core word (head = last of analyst / scientist / engineer / developer / sde), its modifiers in
     the precedence order of docs/TRACKS.md; no modifier -> SOC tie-break
  6. No core word: vague data/ML titles -> SOC tie-break, otherwise OTHER

Usage (from the project root): python3 experiments/track_rules_v5.py
"""

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
import track_rules_v4 as v4            # noqa: E402  frozen baseline for the comparison

OUT_MD = HERE / "track_rules_v5.md"

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
ANALYST_W = w(r"analysts?")
QA_W = w(r"qa|quality assurance|software quality|test|testing|tester")
NOT_QA = w(r"a b|ab|experiment\w*")          # A/B testing and experimentation analysts are not QA
QUALITY_W = w(r"quality|assurance")
QA_SOC = (r"15-1253", r"15-1199\.01")
ANALYTICS_W = w(r"analytics")
CONSULTANT_W = w(r"consultants?")
NOT_ANALYTICS_ONLY = w(r"data science|machine learning|ai|consultants?")   # consultants are not DA
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
    ("operations research (anywhere)", w(r"operations? research"), "DATA_SCIENCE"),
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

    if ANALYST_W.search(norm):
        if QA_W.search(norm) and not NOT_QA.search(norm):
            return "SOFTWARE_ENGINEERING", "analyst: QA / testing"
        if QUALITY_W.search(norm) and soc_in(soc, *QA_SOC):
            return "SOFTWARE_ENGINEERING", "analyst: quality + QA SOC"

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

    if CONSULTANT_W.search(norm):              # title families above still win
        return "OTHER", "no core word: consultant"
    if ANALYTICS_W.search(norm) and not NOT_ANALYTICS_ONLY.search(norm):
        return "DATA_ANALYTICS", "no core word: analytics"
    if VAGUE_GATE.search(pre):
        return vague_by_soc(soc), "no core word: vague -> SOC"
    return "OTHER", "no core word"


def main():
    df, n_raw, n_dedup, n_cert = load()
    total = len(df)
    print(f"{n_raw:,} raw -> {n_dedup:,} de-dup -> {n_cert:,} Certified -> {total:,} after BODYSHOPS",
          flush=True)

    pairs = df[["title", "soc_code"]].drop_duplicates()
    print(f"classifying {len(pairs):,} distinct (title, SOC) pairs with v5 and v4 ...", flush=True)
    res = {}
    for t, s in pairs.itertuples(index=False):
        pre, norm, _ = normalize_title(t)
        res[(t, s)] = classify(pre, norm, s) + (v4.classify(*v4.normalize_title(t)[:2], s)[0],)
    key = list(zip(df["title"], df["soc_code"]))
    df["track"] = [res[k][0] for k in key]
    df["path"] = [res[k][1] for k in key]
    df["v4_track"] = [res[k][2] for k in key]

    g4 = df.groupby("v4_track").agg(filings=("case_number", "size"), companies=("company", "nunique"))
    g5 = df.groupby("track").agg(filings=("case_number", "size"), companies=("company", "nunique"))
    rows = []
    for t in TRACKS:
        a = g4.loc[t] if t in g4.index else pd.Series({"filings": 0, "companies": 0})
        b = g5.loc[t] if t in g5.index else pd.Series({"filings": 0, "companies": 0})
        d = int(b.filings) - int(a.filings)
        rows.append((t, f"{int(b.filings):,}", f"{100 * b.filings / total:.1f}%", f"{int(b.companies):,}",
                     f"{int(a.filings):,}", f"{d:+,}" + (f" ({100 * d / a.filings:+.1f}%)" if a.filings else ""),
                     f"{int(b.companies) - int(a.companies):+,}"))
    moved = df[df["v4_track"] != df["track"]]
    flow = moved.groupby(["v4_track", "track"]).size().sort_values(ascending=False).head(15)
    new_paths = df[df["path"].isin(["analyst: QA / testing", "analyst: quality + QA SOC",
                                    "family: operations research (anywhere)", "no core word: analytics"])]
    oth = df[df["track"] == "OTHER"]
    top = (oth.groupby("title").agg(filings=("case_number", "size"),
                                    path=("path", lambda x: x.mode().iat[0]),
                                    soc=("soc_code", lambda x: x.mode().iat[0]))
              .sort_values("filings", ascending=False).head(30))

    lines = [
        "# Track rules v5 - v4 plus three fixes",
        "",
        "Generated by `experiments/track_rules_v5.py` (read-only). Same input and filters as v3/v4:",
        f"**{total:,} filings** ({n_raw:,} raw -> {n_dedup:,} de-dup -> {n_cert:,} Certified ->",
        f"{total:,} after BODYSHOPS). v4 is re-run on the same filings for the comparison. Rules:",
        "module docstring and `docs/TRACKS.md`.",
        "",
        "## 1. Filings per Track, and change vs v4",
        "",
        md_table(["Track", "v5 filings", "% of filings", "v5 companies", "v4 filings",
                  "filings change", "companies change"], rows),
        "",
        f"{len(moved):,} filings ({100 * len(moved) / total:.2f}%) changed Track. Largest flows:",
        "",
        md_table(["v4 Track", "v5 Track", "filings"], [(a, b, f"{n:,}") for (a, b), n in flow.items()]),
        "",
        "Filings decided by the three new rules:",
        "",
        md_table(["rule path", "filings", "top raw titles"],
                 [(p, f"{len(g):,}", ", ".join(f"{k} ({v:,})" for k, v in g["title"].value_counts().head(4).items()))
                  for p, g in new_paths.groupby("path")]),
        "",
        "## 2. Top 30 raw titles in OTHER",
        "",
        md_table(["#", "raw title", "filings", "rule path", "most common SOC"],
                 [(i + 1, k, f"{r.filings:,}", r.path, r.soc) for i, (k, r) in enumerate(top.iterrows())]),
        "",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_MD.name}")
    print(md_table(["Track", "v5", "%", "companies", "v4", "change", "companies change"], rows))


if __name__ == "__main__":
    main()
