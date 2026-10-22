"""
common/tracks.py - Track classification for job titles (LCA filings and ATS postings)

The rules implement docs/TRACKS.md. They are copied unchanged from the frozen
experiments/track_rules_v5.py (commit ff07fa0); experiments/check_common_tracks.py verifies that
this module and v5 agree on every distinct (title, SOC) pair in the LCA data. Change the rules
in docs/TRACKS.md first, then here.

Public API:
  classify_title(title, soc_code=None) -> (track, rule_path)
  normalize_title(title) -> (pre, norm, levels)
  TRACKS

soc_code may be None: ATS postings have no SOC. Without a SOC there is no SOC veto and every
SOC tie-break falls through to its default (usually OTHER), so vague titles such as a bare
"Engineer" or "Business Analyst" come out as OTHER.
"""

import re

TRACKS = ["DATA_ANALYTICS", "BUSINESS_ANALYTICS", "SYSTEMS_ANALYSIS", "FINANCIAL_ANALYTICS", "QUANT",
          "DATA_SCIENCE", "DATA_ENGINEERING", "ML_ENGINEERING", "ML_RESEARCH",
          "SOFTWARE_ENGINEERING", "OTHER"]

# ------------------------------------------------ title normalization
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
    parts = [p.strip() for p in re.split(r",|\s[-\u2013\u2014|]\s|\s[-\u2013\u2014]|[-\u2013\u2014]\s", t)
             if p.strip()]
    t = " ".join(parts[1:] + parts[:1])                      # qualifiers first, main title last
    t = _ABBREV_RE.sub(lambda m: ABBREV[m.group(1)], t)
    pre = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()
    levels = " ".join(dict.fromkeys(_LEVEL_RE.findall(pre)))
    norm = re.sub(r"\b\d+\b", " ", _LEVEL_RE.sub(" ", pre))
    return pre, re.sub(r"\s+", " ", norm).strip(), levels


def _w(pattern):
    """Whole-word regex."""
    return re.compile(rf"\b(?:{pattern})\b")


# ------------------------------------------------ vocabulary (docs/TRACKS.md)
QUANT_W = _w(r"quant|quants|quantitative")
NONFIN_QUANT = _w(r"user|ux|marketing|survey|clinical|health|behavioral|social|market research")
IT_SYSTEM = _w(r"sap|oracle|salesforce|sfdc|guidewire|erp|crm|servicenow|workday|it")
FIN_W = _w(r"financial|finance|credit|risk|investment|investments|accounting")
DA_W = _w(r"data|business intelligence|reporting")
BA_W = _w(r"growth|marketing|product|customer|operations|strategy")
SYS_W = _w(r"business systems|systems")
BUSINESS = _w(r"business")
DS_SCI = _w(r"data|decision|product|operations research")
ML = r"machine learning|ml|mlops|deep learning|computer vision|cv|nlp|natural language processing"
AI = r"ai|artificial intelligence|llm|llms|genai|generative ai"
MLR_SCI = _w(rf"research|applied|{ML}|{AI}")
BI_ENG = _w(r"business intelligence|tableau|power business intelligence|looker")
DATA_CENTER = _w(r"data cent(?:er|re)")
RESEARCH = _w(r"research")
ML_ENG = _w(rf"{ML}|{AI}")
DE_ENG = _w(r"data|analytics|etl|big data|data warehouse")
SWE_ENG = _w(r"software|backend|back end|frontend|front end|full stack|fullstack|mobile|ios|android|"
             r"web|devops|site reliability|qa|quality assurance|test automation|test|automation|"
             r"java|javascript|python|net|golang|react|node|ui")
OTHER_ENG = _w(r"network|security|cybersecurity|cyber|systems|infrastructure|hardware|solutions|"
               r"sales|support|field")
CORE = _w(r"analyst|analysts|scientist|scientists|engineer|engineers|developer|developers|sde")
CORE_NAME = {"analyst": "analyst", "analysts": "analyst", "scientist": "scientist",
             "scientists": "scientist", "engineer": "engineer", "engineers": "engineer",
             "developer": "engineer", "developers": "engineer", "sde": "engineer"}
MTS = _w(r"member of (?:the )?technical staff")
ANALYST_W = _w(r"analysts?")
QA_W = _w(r"qa|quality assurance|software quality|test|testing|tester")
NOT_QA = _w(r"a b|ab|experiment\w*")         # A/B testing and experimentation analysts are not QA
QUALITY_W = _w(r"quality|assurance")
QA_SOC = (r"15-1253", r"15-1199\.01")
ANALYTICS_W = _w(r"analytics")
CONSULTANT_W = _w(r"consultants?")
NOT_ANALYTICS_ONLY = _w(r"data science|machine learning|ai|consultants?")   # consultants are not DA
VAGUE_GATE = _w(r"data science|analytics|statistic\w*|machine learning|ai|research\w*")

# ------------------------------------------------ SOC (veto and tie-break only)
SOC_VETO = ("17-", "19-", "25-", "29-")
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


def _soc_in(soc, *patterns):
    return any(re.match(p, soc) for p in patterns)


def _analyst_by_soc(soc):
    """Analyst with no direction word: SOC breaks the tie among the Analyst Tracks."""
    if _soc_in(soc, *DA_SOC):
        return "DATA_ANALYTICS"
    if _soc_in(soc, *BA_SOC):
        return "BUSINESS_ANALYTICS"
    if _soc_in(soc, *COMPUTER_SOC):
        return "SYSTEMS_ANALYSIS"
    if _soc_in(soc, *FIN_SOC):
        return "FINANCIAL_ANALYTICS"
    if _soc_in(soc, *QUANT_SOC):
        return "QUANT"
    return "OTHER"


def _vague_by_soc(soc):
    """No core word but a data/ML word: SOC breaks the tie."""
    if _soc_in(soc, *DA_SOC):
        return "DATA_ANALYTICS"
    if _soc_in(soc, *DS_SOC):
        return "DATA_SCIENCE"
    if _soc_in(soc, *MLR_SOC):
        return "ML_RESEARCH"
    if _soc_in(soc, *SWE_SOC):
        return "SOFTWARE_ENGINEERING"
    return "OTHER"


def _is_quant(t):
    return bool(QUANT_W.search(t)) and not NONFIN_QUANT.search(t)


# ------------------------------------------------ title families (checked before core words)
FAMILIES = [
    ("UX / user researcher", _w(r"(?:ux|user) research(?:ers?)?"), "OTHER"),
    ("operations research (anywhere)", _w(r"operations? research"), "DATA_SCIENCE"),
    ("programmer analyst", _w(r"programmer analysts?"), "SOFTWARE_ENGINEERING"),
    ("statistical programmer", _w(r"statistical programmers?"), "DATA_ANALYTICS"),   # before "programmer"
    ("programmer", _w(r"(?:computer )?programmers?"), "SOFTWARE_ENGINEERING"),
    ("statistician", _w(r"statisticians?"), "DATA_SCIENCE"),
    ("quant trader/strategist/researcher", _w(r"(?:quant|quantitative) (?:trader|strategist|researcher)s?|quant"), "QUANT"),
    ("data architect", _w(r"data architects?"), "DATA_ENGINEERING"),
    ("data science (no core word)", _w(r"data science"), "DATA_SCIENCE"),
    ("software engineering/development (no core word)", _w(r"software (?:engineering|development)"), "SOFTWARE_ENGINEERING"),
]
# families that only apply when the title has no core word
NO_CORE_FAMILIES = {"data science (no core word)", "software engineering/development (no core word)"}


def _classify(pre, norm, soc):
    """-> (track, rule path); soc is a stripped string, "" when unknown."""
    if soc.startswith(SOC_VETO):
        return "OTHER", "SOC veto"
    if MTS.search(pre):
        if _soc_in(soc, *MLR_SOC):
            return "ML_RESEARCH", "MTS -> SOC"
        if _soc_in(soc, *SWE_SOC):
            return "SOFTWARE_ENGINEERING", "MTS -> SOC"
        return "OTHER", "MTS -> SOC"
    if not norm:
        return "OTHER", "empty after level words"

    cores = [CORE_NAME[m.group(0)] for m in CORE.finditer(norm)]
    head = cores[-1] if cores else None
    for name, rx, track in FAMILIES:
        if name in NO_CORE_FAMILIES and head:
            continue
        if name.startswith("quant") and not _is_quant(norm):
            continue
        if rx.search(norm):
            return track, f"family: {name}"

    if ANALYST_W.search(norm):
        if QA_W.search(norm) and not NOT_QA.search(norm):
            return "SOFTWARE_ENGINEERING", "analyst: QA / testing"
        if QUALITY_W.search(norm) and _soc_in(soc, *QA_SOC):
            return "SOFTWARE_ENGINEERING", "analyst: quality + QA SOC"

    if head == "analyst":
        if IT_SYSTEM.search(norm):
            return "SYSTEMS_ANALYSIS", "analyst: IT system"
        if _is_quant(norm):
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
            if _soc_in(soc, *BA_SOC):
                return "BUSINESS_ANALYTICS", "business analyst -> SOC"
            if _soc_in(soc, *COMPUTER_SOC):
                return "SYSTEMS_ANALYSIS", "business analyst -> SOC"
            return "OTHER", "business analyst -> SOC"
        return _analyst_by_soc(soc), "analyst: no direction -> SOC"

    if head == "scientist":
        if _is_quant(norm):
            return "QUANT", "scientist: quant"
        if DS_SCI.search(norm):
            return "DATA_SCIENCE", "scientist: data"
        if MLR_SCI.search(norm):
            return "ML_RESEARCH", "scientist: research/ML"
        if _soc_in(soc, *DS_SOC):
            return "DATA_SCIENCE", "scientist: no direction -> SOC"
        if _soc_in(soc, *MLR_SOC):
            return "ML_RESEARCH", "scientist: no direction -> SOC"
        return "OTHER", "scientist: no direction -> SOC"

    if head == "engineer":                     # engineer / developer / SDE
        if _is_quant(norm):
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
        if _soc_in(soc, *SWE_SOC):
            return "SOFTWARE_ENGINEERING", "engineer: no direction -> SOC"
        return "OTHER", "engineer: no direction -> SOC"

    if CONSULTANT_W.search(norm):              # title families above still win
        return "OTHER", "no core word: consultant"
    if ANALYTICS_W.search(norm) and not NOT_ANALYTICS_ONLY.search(norm):
        return "DATA_ANALYTICS", "no core word: analytics"
    if VAGUE_GATE.search(pre):
        return _vague_by_soc(soc), "no core word: vague -> SOC"
    return "OTHER", "no core word"


def classify_title(title, soc_code=None):
    """-> (track, rule_path). soc_code is optional (ATS postings have none)."""
    pre, norm, _ = normalize_title(title)
    return _classify(pre, norm, str(soc_code or "").strip())
