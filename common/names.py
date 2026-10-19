# common/names.py - company-name and job-title rules shared by the new pipeline.
# Copied from legacy/build_companies.py so new code never imports legacy scripts; the two
# copies are allowed to drift (legacy is frozen).
import re
import unicodedata

# Senior-level title markers (substring match on the lowercased title). An LCA filing whose
# title contains one of these does not count toward non_senior_filing_count.
TITLE_SKIP = ["senior", "sr ", "sr.", "staff", "principal", "lead ", "manager",
              "director", "architect", "vice president", " iii", " iv"]

# Staffing/consulting firms, universities, hospitals, national labs: they show up in H-1B data
# but are not target employers. Matched as substrings of the lowercased normalized name.
BODYSHOPS = [
    "infosys", "tata consultancy", "cognizant", "wipro", "accenture", "hcl",
    "capgemini", "tech mahindra", "larsen", "lti mindtree", "ltimindtree",
    "deloitte", "ernst", "kpmg", "pricewaterhouse", "pwc", "mphasis",
    "syntel", "ust global", "virtusa", "birlasoft", "hexaware", "zensar",
    "iflex", "polaris", "igate", "ntt data", "dxc", "unisys", "compunnel",
    "sonsoft", "cybertech", "diverse lynx", "photon", "collabera", "artech",
    "randstad", "kforce", "insight global", "apex systems", "robert half",
    "staffing", "consultancy", "consulting services", "solutions inc",
    "technologies inc", "infotech", "softech", "systems inc",
    "university", "college", "institute of technology", "school of",
    "hospital", "medical center", "health system", "national laborator",
    "research institute", "academy", "foundation", "trustees of",
    # National-lab operators: the company name often lacks "national laborator" (e.g.
    # "UChicago Argonne LLC"), so the rule above misses them; list the operators by name.
    "argonne",
]


def clean_name(name):
    n = str(name).strip().upper()
    n = re.sub(r"[.,]", "", n)
    n = re.sub(r"\b(INC|LLC|LTD|CORP|CORPORATION|COMPANY|CO|LP|LLP|PLC|HOLDINGS|USA|US|AMERICA|NA)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def normalize(name):
    """Normalized company name used as companies.normalized_name and for matching other
    sources (e.g. ATS lists) to it: clean_name, then accented letters folded to plain ASCII
    (MySQL's utf8mb4_0900_ai_ci compares accent-insensitively, so unfolded names would
    collide on the UNIQUE key)."""
    n = clean_name(name)
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", n).strip()[:255]
