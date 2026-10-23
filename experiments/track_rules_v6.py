#!/usr/bin/env python3
"""
experiments/track_rules_v6.py - Track rules v6: v5 (frozen) plus a fallback for titles with no SOC

v6 changes ONLY what happens when there is no SOC code (ATS postings; soc None or ""). With a SOC
code every path is identical to v5. Read-only: no MySQL writes, no files written; prints
v5 vs v6 on all LCA (title, SOC) pairs (must be identical) and on the jobs table (soc None).

Change from v5:
  1. No core word, a vague data/ML title (the "no core word: vague -> SOC" path) and NO SOC:
     a. sales / marketing / customer success|support|service|experience|care / recruiting /
        legal / partnerships words, or product /
        program / project manager, solutions architect, business development,
        commercialization, account manager, client, enablement, accounting, business risk
        (non-engineering roles, OTHER in docs/TRACKS.md) -> OTHER
     b. research / researcher plus an ML/AI word -> ML_RESEARCH (a PhD requirement is a
        degree, not the work output, so "phd" alone does not make it research)
     c. any other ML/AI word -> ML_ENGINEERING
     d. otherwise OTHER (as v5)
     ML/AI words: machine learning, ml, ai, artificial intelligence, deep learning, llm(s),
     computer vision, nlp, foundation model(s), world model(s), vlm, post training /
     post-training, perception (whole words). Level words (intern, manager, director, ...) do not
     change the Track, as in docs/TRACKS.md. Engineers / analysts with no direction word stay
     on their v5 paths (OTHER without a SOC).

Order of evaluation (docs/TRACKS.md; SOC only vetoes or breaks ties on vague titles):
  1. SOC veto: 17-, 19-, 25-, 29- -> OTHER
  2. Member of Technical Staff -> by SOC
  3. Empty after removing level words -> OTHER
  4. Title families (settled edge cases and Includes lists with no core word)
  5. Core word (head = last of analyst / scientist / engineer / developer / sde), its modifiers in
     the precedence order of docs/TRACKS.md; no modifier -> SOC tie-break
  6. No core word: vague data/ML titles -> SOC tie-break, otherwise OTHER

Usage (from the project root): python3 experiments/track_rules_v6.py
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
import track_rules_v5 as v5            # noqa: E402  frozen baseline for the comparison
from common.db import get_conn          # noqa: E402  read-only: SELECT from jobs

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
# v6: fallback for vague titles with no SOC
NO_SOC_EXCLUDE = w(r"account executives?|sales|marketing|recruiters?|recruiting|legal|"
                   r"customer (?:success|support|service|experience|care)|"
                   r"partnerships?|product managers?|program managers?|project managers?|"
                   r"solutions? architects?|business development|commercialization|"
                   r"account managers?|clients?|enablement|accounting|business risk")
NO_SOC_ML = w(r"machine learning|ml|ai|artificial intelligence|deep learning|llms?|computer vision|nlp|"
              r"foundation models?|world models?|vlm|post training|perception")   # pre: "-" -> " "
NO_SOC_RESEARCH = w(r"research|researchers?")

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


def no_soc_fallback(pre):
    """v6: vague title with no core word and no SOC (ATS postings) -> (track, rule path)."""
    if NO_SOC_EXCLUDE.search(pre):
        return "OTHER", "no core word: vague, no SOC: non-engineering role"
    if NO_SOC_ML.search(pre):
        if NO_SOC_RESEARCH.search(pre):
            return "ML_RESEARCH", "no core word: vague, no SOC: research + ML/AI"
        return "ML_ENGINEERING", "no core word: vague, no SOC: ML/AI"
    return "OTHER", "no core word: vague, no SOC"


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
        if not soc:                            # v6: no SOC to break the tie
            return no_soc_fallback(pre)
        return vague_by_soc(soc), "no core word: vague -> SOC"
    return "OTHER", "no core word"


def main():
    # 1. LCA titles (all have a SOC): v6 must equal v5 on every (title, SOC) pair
    df, *_ = load()
    pairs = df[["title", "soc_code"]].drop_duplicates()
    print(f"LCA: {len(df):,} filings, {len(pairs):,} distinct (title, SOC) pairs", flush=True)
    n_track = n_path = 0
    for t, s in pairs.itertuples(index=False):
        a = v5.classify(*v5.normalize_title(t)[:2], s)
        b = classify(*normalize_title(t)[:2], s)
        n_track += a[0] != b[0]
        n_path += a[0] == b[0] and a[1] != b[1]
    print(f"LCA v5 vs v6: Track differences {n_track:,}, rule-path differences {n_path:,}\n")

    # 2. jobs table (ATS postings, no SOC)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT title FROM jobs WHERE status = 'active'")
        titles = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    res = [(t, v5.classify(*v5.normalize_title(t)[:2], None), classify(*normalize_title(t)[:2], None))
           for t in titles]
    c5 = pd.Series([a[0] for _, a, _ in res]).value_counts()
    c6 = pd.Series([b[0] for _, _, b in res]).value_counts()
    print(md_table(["Track", "v5", "v6", "change"],
                   [(t, int(c5.get(t, 0)), int(c6.get(t, 0)), f"{int(c6.get(t, 0)) - int(c5.get(t, 0)):+d}")
                    for t in TRACKS]))
    moved = [(t, a[0], b[0], b[1]) for t, a, b in res if a[0] != b[0]]
    print(f"\n{len(moved)} active jobs changed Track (of {len(titles):,}):")
    for track in TRACKS:
        g = sorted(m for m in moved if m[2] == track)
        if g:
            print(f"\n[{track}] {len(g)}")
            for t, a, b, _ in g:
                print(f"  {t.strip()}   ({a} -> {b})")
    excl = sorted(t.strip() for t, _, b in res if b[1].endswith("non-engineering role"))
    print(f"\nKept OTHER by the non-engineering-role exclusion: {len(excl)}")
    for t in excl:
        print(f"  {t}")


if __name__ == "__main__":
    main()
