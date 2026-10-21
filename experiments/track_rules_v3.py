#!/usr/bin/env python3
"""
experiments/track_rules_v3.py - read-only evaluation of Track rules v3 on LCA titles

Same data as v1 (all 25 cached LCA files, CASE_NUMBER de-dup, Certified), but staffing firms /
universities / hospitals are removed with common/names.py BODYSHOPS, like lca/process_lca.py.
Writes experiments/track_rules_v3.md (with a v2 -> v3 comparison) and
experiments/track_labels_v3.csv (20 random filings per data/ML Track for hand labeling,
with the rule path that produced each prediction).
Touches neither lca/process_lca.py nor the database.

Changes from v1:
  1. SOC fallback only when the title mentions data / analytics / science / statistic /
     machine learning / ai / research / technical staff; new SOC map (SOC_MAP); 13-1111 and
     13-1161 dropped.
  2. SOC veto: 17-, 19-, 25-, 29- -> OTHER, before any title rule.
  3. Title-family rules (FAMILIES) checked before the core-word rules.
  4. ML/AI vocabulary adds computer vision, cv, nlp, natural language processing.
  5. A core word with no matching modifier -> SOC_MAP (no keyword gate), else OTHER.
  6. Precedence: analyst data > business; scientist data/decision/product > research/applied/
     ML/AI; engineer and developer/SDE ML/AI > data > research > software. Developer/SDE now
     use the engineer modifiers instead of always being SOFTWARE_ENGINEERING.
  7. manager / director / head / vp are level words (recorded, not used for the Track);
     a title that is empty after removing level words -> OTHER.

Changes from v2:
  1. SOC map adds pre-2018 codes: 15-1111 -> ML_RESEARCH; 15-1131/1132/1133 ->
     SOFTWARE_ENGINEERING; 15-1199.08 -> DATA_ANALYTICS.
  2. Core word with no modifier: analyst / scientist use the full SOC map; engineer /
     developer -> SOFTWARE_ENGINEERING only for SOC 15-113x / 15-125x, else OTHER.
  3. Business analyst: growth / marketing / product / customer / strategy / operations ->
     BUSINESS_ANALYTICS; a plain "business analyst" is decided by SOC (BA_SOC_BA ->
     BUSINESS_ANALYTICS, BA_SOC_IT -> OTHER, anything else -> OTHER). "analyst business
     systems" word order counts as business systems analyst -> OTHER.
  4. operations research scientist -> DATA_SCIENCE (with operations research analyst).
  5. SOC_GATE drops bare "data" (keeps data science / analytics / statistic / machine
     learning / ai / research / technical staff).
  6. Engineer / developer modifier precedence: research > ML/AI > data > software.

Usage (from the project root): python3 experiments/track_rules_v3.py
"""

import csv
import glob
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.names import BODYSHOPS, normalize   # noqa: E402
import track_rules_v1 as v1                     # noqa: E402  (md_table)
import track_rules_v2 as v2                     # noqa: E402  (baseline for the comparison)

CACHE_GLOB = str(ROOT / "data" / "processed" / "lca_parquet" / "*.parquet")
OUT_MD = Path(__file__).resolve().parent / "track_rules_v3.md"
OUT_LABELS = Path(__file__).resolve().parent / "track_labels_v3.csv"
SEED = 551
LABEL_TRACKS = ["DATA_ANALYTICS", "BUSINESS_ANALYTICS", "DATA_SCIENCE", "DATA_ENGINEERING",
                "ML_ENGINEERING", "ML_RESEARCH"]
ORDER = ["DATA_ANALYTICS", "BUSINESS_ANALYTICS", "DATA_SCIENCE", "ML_RESEARCH",
         "ML_ENGINEERING", "DATA_ENGINEERING", "SOFTWARE_ENGINEERING", "OTHER"]

# ------------------------------------------------ title normalization
LEVEL_WORDS = ["senior", "sr", "lead", "staff", "principal", "junior", "jr", "associate",
               "intern", "i", "ii", "iii", "iv", "v",
               "manager", "managers", "director", "head", "vp"]
ABBREV = {"mle": "machine learning engineer", "swe": "software engineer",
          "sde": "software development engineer", "bi": "business intelligence",
          "ds": "data scientist"}

# ------------------------------------------------ SOC rules
SOC_VETO = ("17-", "19-", "25-", "29-")          # engineering, sciences, education, healthcare


def soc_map(soc):
    s = str(soc or "").strip()
    if s in ("15-2051", "15-2051.00"):
        return "DATA_SCIENCE"
    if s.startswith("15-2051.01"):
        return "DATA_ANALYTICS"
    if s.startswith("15-2051.02"):
        return "OTHER"
    if s.startswith("15-1221") or s.startswith("15-1111"):       # 15-1111 = 2010 code
        return "ML_RESEARCH"
    if re.match(r"15-125\d|15-113[123]", s):                     # 15-113x = 2010 codes
        return "SOFTWARE_ENGINEERING"
    if s.startswith("15-1199.08"):                               # 2010 BI analysts
        return "DATA_ANALYTICS"
    return None


SWE_SOC = re.compile(r"15-113\d|15-125\d")    # engineer/developer with no modifier

# Plain "business analyst" (no other modifier) is decided by SOC
BA_SOC_BA = re.compile(r"15-2051\.01|15-2041|13-1161")
BA_SOC_IT = re.compile(r"15-1121|15-1211|15-1199|15-125\d|15-113\d")


# Titles must mention one of these for the no-core-word SOC fallback to apply
SOC_GATE = re.compile(r"\b(?:data science|analytics?|statistic\w*|machine learning|ai|"
                      r"research\w*|technical staff)\b")

# ------------------------------------------------ title-family rules (before core words)
# (name, regex on the normalized title, Track or "SOC:mts")
FAMILIES = [
    ("data center engineer", r"\bdata cent(?:er|re)\b.*\bengineers?\b|\bengineers?\b.*\bdata cent(?:er|re)\b", "OTHER"),
    ("business systems analyst", r"\bbusiness systems analysts?\b|\banalysts? business systems\b", "OTHER"),
    ("systems analyst", r"\bsystems analysts?\b", "OTHER"),
    ("programmer analyst", r"\bprogrammer analysts?\b", "OTHER"),
    ("operations research analyst/scientist", r"\boperations research (?:analyst|scientist)s?\b", "DATA_SCIENCE"),
    ("business intelligence engineer/developer", r"\bbusiness intelligence (?:engineer|developer)s?\b", "DATA_ANALYTICS"),
    ("statistician", r"\bstatisticians?\b", "DATA_SCIENCE"),
    ("quantitative analyst/researcher", r"\bquantitative (?:analyst|researcher)s?\b", "OTHER"),
]
MTS = re.compile(r"\bmember of (?:the )?technical staff\b")   # checked before level words are removed

# ------------------------------------------------ core-word rules: modifiers in precedence order
ML = r"machine learning|ml|mlops|deep learning|computer vision|cv|nlp|natural language processing"
AI = r"ai|artificial intelligence|llm|llms|genai|generative ai"
ENG_MODS = [
    ("ML_RESEARCH", r"research"),
    ("ML_ENGINEERING", rf"{ML}|{AI}"),
    ("DATA_ENGINEERING", r"data|analytics|etl"),
    ("SOFTWARE_ENGINEERING", r"software|backend|back end|frontend|front end|full stack|fullstack"),
]
CORES = [
    ("analyst", r"analysts?", [
        ("DATA_ANALYTICS", r"data|business intelligence|reporting"),
        ("BUSINESS_ANALYTICS", r"growth|marketing|product|customer|operations|strategy"),
        ("OTHER", r"financial|credit|investment"),
        ("BUSINESS_PLAIN", r"business(?! intelligence)"),     # decided by SOC, see classify()
    ]),
    ("scientist", r"scientists?", [
        ("DATA_SCIENCE", r"data|decision|product"),
        ("ML_RESEARCH", rf"research|applied|{ML}|{AI}"),
    ]),
    ("engineer", r"engineers?", ENG_MODS),
    ("developer", r"developers?|sde", ENG_MODS),
]

_CORE_RE = [(name, re.compile(rf"\b(?:{pat})\b"), [(t, re.compile(rf"\b(?:{m})\b")) for t, m in mods])
            for name, pat, mods in CORES]
_FAMILY_RE = [(n, re.compile(p), t) for n, p, t in FAMILIES]
_LEVEL_RE = re.compile(r"\b(" + "|".join(LEVEL_WORDS) + r")\b")
_ABBREV_RE = re.compile(r"\b(" + "|".join(ABBREV) + r")\b")


def normalize_title(raw):
    """-> (pre: normalized but level words kept, norm: level words removed, levels)"""
    t = str(raw or "").lower()
    t = re.sub(r"\d+(?:\.\d+)+", " ", t)                     # internal codes like 1615.32817
    t = re.sub(r"[()\[\]]", ",", t)                          # parentheses behave like a comma
    parts = [p.strip() for p in re.split(r",|\s[-–—|]\s|\s[-–—]|[-–—]\s", t)
             if p.strip()]
    t = " ".join(parts[1:] + parts[:1])                      # qualifiers first, main title last
    t = _ABBREV_RE.sub(lambda m: ABBREV[m.group(1)], t)
    pre = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()
    levels = " ".join(dict.fromkeys(_LEVEL_RE.findall(pre)))
    norm = _LEVEL_RE.sub(" ", pre)
    norm = re.sub(r"\b\d+\b", " ", norm)
    return pre, re.sub(r"\s+", " ", norm).strip(), levels


def core_hits(norm):
    """-> (head core name or None, head's tracks in precedence order, all tracks hit)"""
    hits, head = [], None
    for name, core_re, mods in _CORE_RE:
        for cm in core_re.finditer(norm):
            if head is None or cm.start() > head[1]:
                head = (name, cm.start())
            hits.extend((name, t) for t, mre in mods if mre.search(norm))
    if head is None:
        return None, [], frozenset()
    return (head[0], [t for n, t in hits if n == head[0]],
            frozenset("BUSINESS_ANALYTICS" if t == "BUSINESS_PLAIN" else t for _, t in hits))


def classify(pre, norm, soc):
    """-> (track, path, all tracks hit by core words)"""
    soc = str(soc or "").strip()
    head, head_tracks, hit = core_hits(norm)
    if soc.startswith(SOC_VETO):
        return "OTHER", "SOC veto", hit
    if MTS.search(pre):
        t = soc_map(soc)
        return (t if t in ("ML_RESEARCH", "SOFTWARE_ENGINEERING") else "OTHER"), "family: member of technical staff", hit
    if not norm:
        return "OTHER", "empty after level words", hit
    for name, fre, track in _FAMILY_RE:
        if fre.search(norm):
            return track, f"family: {name}", hit
    if head is None:
        if re.search(r"\bsoftware engineering\b", norm):
            return "SOFTWARE_ENGINEERING", "family: software engineering (no core)", hit
        if re.search(r"\bdata science\b", norm):
            return "DATA_SCIENCE", "family: data science (no core)", hit
        if SOC_GATE.search(pre):
            return soc_map(soc) or "OTHER", "no core: SOC (gated)", hit
        return "OTHER", "no core: not gated", hit
    if head_tracks and head_tracks[0] == "BUSINESS_PLAIN":
        if BA_SOC_BA.match(soc):
            return "BUSINESS_ANALYTICS", "business analyst -> SOC (BA)", hit
        if BA_SOC_IT.match(soc):
            return "OTHER", "business analyst -> SOC (IT)", hit
        return "OTHER", "business analyst -> SOC (unlisted)", hit
    if head_tracks:
        return head_tracks[0], f"{head}:modifier", hit
    if head in ("analyst", "scientist"):
        return soc_map(soc) or "OTHER", f"{head}:no-modifier -> SOC", hit
    if SWE_SOC.match(soc):
        return "SOFTWARE_ENGINEERING", f"{head}:no-modifier -> SOC 15-113x/125x", hit
    return "OTHER", f"{head}:no-modifier -> OTHER", hit


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
    n_cert = len(df)
    emap = {e: normalize(e) for e in df["employer"].fillna("").unique()}
    df["company"] = df["employer"].fillna("").map(emap)
    bodyshop_re = "|".join(re.escape(b) for b in BODYSHOPS)
    df = df[(df["company"] != "") &
            ~df["company"].str.lower().str.contains(bodyshop_re, regex=True)].copy()
    df["title"] = df["title"].fillna("")
    df["soc_code"] = df["soc_code"].fillna("").str.strip()
    return df, n_raw, n_dedup, n_cert


md_table = v1.md_table


def main():
    df, n_raw, n_dedup, n_cert = load()
    total = len(df)
    print(f"{n_raw:,} raw -> {n_dedup:,} de-dup -> {n_cert:,} Certified -> {total:,} after BODYSHOPS",
          flush=True)

    # v2: title-only parts once per distinct title, SOC-dependent part per (title, soc)
    norm_cache = {t: normalize_title(t) for t in df["title"].unique()}
    pairs = df[["title", "soc_code"]].drop_duplicates()
    print(f"classifying {len(pairs):,} distinct (title, SOC) pairs ...", flush=True)
    res = {}
    for t, s in pairs.itertuples(index=False):
        pre, norm, levels = norm_cache[t]
        res[(t, s)] = (norm, levels) + classify(pre, norm, s)
    key = list(zip(df["title"], df["soc_code"]))
    df["norm"] = [res[k][0] for k in key]
    df["level"] = [res[k][1] for k in key]
    df["track"] = [res[k][2] for k in key]
    df["path"] = [res[k][3] for k in key]
    df["hits"] = [res[k][4] for k in key]

    # v2 on the same population, for the comparison table
    v2_res = {}
    for t, so in pairs.itertuples(index=False):
        pre, norm, _ = v2.normalize_title(t)
        v2_res[(t, so)] = v2.classify(pre, norm, so)[0]
    df["v2_track"] = [v2_res[k] for k in key]

    g1 = df.groupby("v2_track").agg(filings=("case_number", "size"), companies=("company", "nunique"))
    g2 = df.groupby("track").agg(filings=("case_number", "size"), companies=("company", "nunique"))
    cmp_rows = []
    for t in ORDER:
        a = g1.loc[t] if t in g1.index else pd.Series({"filings": 0, "companies": 0})
        b = g2.loc[t] if t in g2.index else pd.Series({"filings": 0, "companies": 0})
        df_ = int(b.filings) - int(a.filings)
        dc = int(b.companies) - int(a.companies)
        cmp_rows.append((t, f"{int(a.filings):,}", f"{int(a.companies):,}",
                         f"{int(b.filings):,}", f"{int(b.companies):,}",
                         f"{df_:+,} ({100 * df_ / a.filings:+.0f}%)" if a.filings else f"{df_:+,}",
                         f"{dc:+,}"))

    moved = df[df["v2_track"] != df["track"]]
    flow = moved.groupby(["v2_track", "track"]).size().sort_values(ascending=False).head(15)

    lines = [
        "# Track rules v3 - evaluation on LCA titles",
        "",
        "Generated by `experiments/track_rules_v3.py` (read-only; `lca/process_lca.py` and the",
        "database are unchanged). Data: all 25 cached LCA files, de-duplicated by CASE_NUMBER",
        f"(latest quarter wins), Certified, staffing firms / universities / hospitals removed with",
        f"`common/names.py` BODYSHOPS: **{total:,} filings** ({n_raw:,} raw -> {n_dedup:,} de-dup",
        f"-> {n_cert:,} Certified -> {total:,}).",
        "",
        "## v2 -> v3",
        "",
        "Both versions run on the same filings (above). Companies = distinct normalized employer names.",
        "",
        md_table(["Track", "v2 filings", "v2 companies", "v3 filings", "v3 companies",
                  "filings change", "companies change"], cmp_rows),
        "",
        f"{len(moved):,} filings ({100 * len(moved) / total:.1f}%) changed Track. Largest flows:",
        "",
        md_table(["v2 Track", "v3 Track", "filings"],
                 [(a, b, f"{n:,}") for (a, b), n in flow.items()]),
        "",
        "## Rules as implemented",
        "",
        "Order of evaluation for each filing:",
        "",
        f"1. **SOC veto**: SOC starting with {', '.join(SOC_VETO)} -> OTHER.",
        "2. **Member of technical staff** (checked before level words are removed): SOC 15-1221 ->",
        "   ML_RESEARCH, 15-125x -> SOFTWARE_ENGINEERING, anything else -> OTHER.",
        "3. **Empty after removing level words** -> OTHER.",
        "4. **Title families** (first match wins): " + "; ".join(f"{n} -> {t}" for n, _, t in FAMILIES) + ".",
        "5. **Core word** (head = last core word after reordering): modifiers in precedence order",
        "   (table below). A plain `business` analyst modifier is decided by SOC: "
        f"`{BA_SOC_BA.pattern}` -> BUSINESS_ANALYTICS, `{BA_SOC_IT.pattern}` -> OTHER (IT business",
        "   analysts), anything else -> OTHER. No matching modifier: analyst / scientist -> SOC map,",
        "   else OTHER; engineer / developer -> SOFTWARE_ENGINEERING if SOC is 15-113x / 15-125x, else OTHER.",
        "6. **No core word**: `software engineering` -> SOFTWARE_ENGINEERING; `data science` ->",
        "   DATA_SCIENCE; title mentions a SOC_GATE word -> SOC map, else OTHER; otherwise OTHER.",
        "",
        "SOC map: 15-2051 / 15-2051.00 -> DATA_SCIENCE; 15-2051.01 and 15-1199.08 -> DATA_ANALYTICS;",
        "15-2051.02 -> OTHER; 15-1221 and 15-1111 -> ML_RESEARCH; 15-125x and 15-1131/1132/1133 ->",
        "SOFTWARE_ENGINEERING; anything else -> OTHER.",
        f"SOC_GATE: `{SOC_GATE.pattern}`.",
        "",
        "Level words (recorded in `level`, never used for the Track): " + ", ".join(LEVEL_WORDS) + ".",
        "",
        md_table(["core", "Track (precedence order) : modifiers"],
                 [(n, "<br>".join(f"{t}: `{m}`" for t, m in mods)) for n, _, mods in CORES]),
        "",
        "Interpretations (please confirm): a plain business analyst whose SOC is in neither list",
        "-> OTHER (counts by SOC in Observations); the engineer precedence (research > ML/AI > data",
        "> software) also applies to developer / SDE, which share the engineer modifiers.",
        "",
        "## 1. Filings and companies per Track",
        "",
    ]
    g = g2.reindex([o for o in ORDER if o in g2.index]).sort_values("filings", ascending=False)
    lines.append(md_table(["Track", "filings", "% of filings", "companies"],
                          [(t, f"{r.filings:,}", f"{100 * r.filings / total:.1f}%", f"{r.companies:,}")
                           for t, r in g.iterrows()]))
    p = df["path"].value_counts()
    lines += ["", "How each filing was classified:", "",
              md_table(["path", "filings", "%"],
                       [(k, f"{v:,}", f"{100 * v / total:.1f}%") for k, v in p.items()]), ""]

    oth = df[df["track"] == "OTHER"]
    top = (oth.groupby("norm").agg(filings=("case_number", "size"),
                                   path=("path", lambda s: s.mode().iat[0]),
                                   soc=("soc_code", lambda s: ", ".join(f"{k} ({v:,})" for k, v in s.value_counts().head(2).items())),
                                   example=("title", lambda s: s.mode().iat[0]))
              .sort_values("filings", ascending=False).head(50))
    lines += ["## 2. Top 50 OTHER titles (by filings)", "",
              f"OTHER total: {len(oth):,} filings.", "",
              md_table(["#", "normalized title", "example raw title", "filings", "path", "SOC (filings)"],
                       [(i + 1, n, r.example, f"{r.filings:,}", r.path, r.soc)
                        for i, (n, r) in enumerate(top.iterrows())]), ""]

    lines += ["## 3. Random sample: 20 filings per Track", "",
              f"`random_state={SEED}`, sampled over filings.", ""]
    for t in g.index:
        s = df[df["track"] == t].sample(min(20, (df["track"] == t).sum()), random_state=SEED)
        lines += [f"### {t}", "",
                  md_table(["raw title", "normalized", "level", "path", "SOC"],
                           [(r.title, r.norm, r.level, r.path, r.soc_code) for r in s.itertuples()]), ""]

    conf = df[df["hits"].map(len) >= 2]
    combos = Counter()
    for h, n in conf.groupby("hits").size().items():
        combos[" + ".join(sorted(h))] += n
    lines += ["## 4. Conflicts: titles whose core words hit two or more Tracks", "",
              f"{len(conf):,} filings ({100 * len(conf) / total:.1f}%). Assigned Track = the rules above.", "",
              md_table(["Tracks hit", "filings"], [(k, f"{v:,}") for k, v in combos.most_common()]), ""]
    for combo, _ in combos.most_common(10):
        sub = conf[conf["hits"].map(lambda h: " + ".join(sorted(h))) == combo]
        top_t = (sub.groupby("title").agg(filings=("case_number", "size"), norm=("norm", "first"),
                                          track=("track", lambda s: s.mode().iat[0]))
                 .sort_values("filings", ascending=False).head(10))
        lines += [f"### {combo}", "",
                  md_table(["raw title", "normalized", "assigned", "filings"],
                           [(t, r.norm, r.track, f"{r.filings:,}") for t, r in top_t.iterrows()]), ""]

    lines += observations(df)
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with open(OUT_LABELS, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["raw_title", "soc_code", "predicted_track", "rule_path", "my_label"])
        for t in LABEL_TRACKS:
            s = df[df["track"] == t].sample(min(20, (df["track"] == t).sum()), random_state=SEED)
            w.writerows((r.title, r.soc_code, t, r.path, "") for r in s.itertuples())

    print(f"wrote {OUT_MD.relative_to(ROOT)} and {OUT_LABELS.relative_to(ROOT)}")
    print(md_table(["Track", "v2 filings", "v2 companies", "v3 filings", "v3 companies",
                    "filings change", "companies change"], cmp_rows))


def observations(df):
    out = ["## 5. Observations", ""]
    mr = df[df["track"] == "ML_RESEARCH"]
    grp = mr["soc_code"].map(
        lambda s: "15-1221 (CS research)" if s.startswith("15-1221") else
                  "15-2051 (data science)" if s.startswith("15-2051") else
                  "15-125x (software)" if re.match(r"15-125\d", s) else "other SOC")
    out += ["### ML_RESEARCH by SOC group", "",
            md_table(["SOC group", "filings", "%"],
                     [(k, f"{v:,}", f"{100 * v / len(mr):.1f}%") for k, v in grp.value_counts().items()]), ""]

    ba = df[df["path"].str.startswith("business analyst -> SOC")]
    tab = (ba.groupby(["path", "soc_code"]).size().rename("filings").reset_index()
             .sort_values("filings", ascending=False))
    rows = []
    for path in ["business analyst -> SOC (BA)", "business analyst -> SOC (IT)",
                 "business analyst -> SOC (unlisted)"]:
        sub = tab[tab["path"] == path]
        rows.append((path, f"{int(sub.filings.sum()):,}",
                     ", ".join(f"{r.soc_code} ({r.filings:,})" for r in sub.head(6).itertuples())))
    out += ["### Plain business analyst: decided by SOC", "",
            md_table(["outcome", "filings", "top SOC codes"], rows), ""]

    fams = df[df["path"].str.startswith("family")]["path"].value_counts()
    out += ["### Title-family rules: filings per rule", "",
            md_table(["rule", "filings"], [(k, f"{v:,}") for k, v in fams.items()]), ""]

    lv = df[df["level"] != ""]
    out += ["### Level words recorded (top 12)", "",
            md_table(["level", "filings"], [(k, f"{v:,}") for k, v in lv["level"].value_counts().head(12).items()]), ""]
    return out


if __name__ == "__main__":
    main()
