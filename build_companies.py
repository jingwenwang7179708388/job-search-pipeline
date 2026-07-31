#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_companies.py — 从劳工部 H-1B 数据里生成目标公司清单

思路: 谁真的给 Data Scientist / MLE 办过签证, 谁就进清单。
不靠猜, 靠政府数据。

用法:
    pip install pandas openpyxl
    python build_companies.py LCA_Disclosure_Data_FY2025.xlsx
    python build_companies.py FY2025.xlsx FY2026_Q2.xlsx      # 多个文件一起

输出:
    sponsors.csv        —— 完整名单, 按 sponsor 次数排序
    companies_new.csv   —— 可以直接喂给 job_feed.py 的格式
"""


import re
import sys
import pandas as pd

# 你的目标职位 —— 职位名里出现这些词就算命中
TITLES = [
    "data scientist", "applied scientist", "decision scientist",
    "machine learning", "ml engineer", "deep learning",
    "artificial intelligence", "ai engineer",
    "research scientist", "research engineer",
    "quantitative research", "quantitative analyst", "quantitative developer",
    "computer vision", "remote sensing", "geospatial",
]

# 排除的职位 —— 这些是资深岗, 不是你的池子
TITLE_SKIP = ["senior", "sr ", "sr.", "staff", "principal", "lead ", "manager",
              "director", "architect", "vice president", " iii", " iv"]

# 外包/咨询公司 —— 它们霸占 H-1B 数据榜首, 但不是你要去的地方
BODYSHOPS = [
    "infosys", "tata consultancy", "cognizant", "wipro", "accenture", "hcl",
    "capgemini", "tech mahindra", "larsen", "lti mindtree", "ltimindtree",
    "deloitte", "ernst", "kpmg", "pricewaterhouse", "pwc", "mphasis",
    "syntel", "ust global", "virtusa", "birlasoft", "hexaware", "zensar",
    "iflex", "polaris", "igate", "ntt data", "dxc", "unisys", "compunnel",
    "sonsoft", "cybertech", "diverse lynx", "photon", "collabera", "artech",
    "randstad", "kforce", "insight global", "apex systems", "robert half",
    "staffing", "consultancy", "consulting services", "solutions inc",
    "technologies inc", "infotech", "softech", "systems inc","university", "college", "institute of technology", "school of",
    "hospital", "medical center", "health system", "national laborator",
    "research institute", "academy", "foundation", "trustees of",
    # 国家实验室运营方: 公司名往往不含 "national laborator" 字样（比如
    # "UChicago Argonne LLC"), 上面那条规则抓不住, 单独列出运营方名字。
    "argonne",
]

# 只看这些州（改成你愿意去的地方；留空 = 全美）
STATES = []          # 例: ["CA", "NY", "WA", "MA", "IL", "TX"]

MIN_COUNT = 1        # 至少 sponsor 过几次才进清单


def find_col(df, *keys):
    """列名各年份不一样, 模糊匹配。"""
    for c in df.columns:
        norm = re.sub(r"[^a-z]", "", str(c).lower())
        if all(re.sub(r"[^a-z]", "", k) in norm for k in keys):
            return c
    return None


def clean_name(name):
    n = str(name).strip().upper()
    n = re.sub(r"[.,]", "", n)
    n = re.sub(r"\b(INC|LLC|LTD|CORP|CORPORATION|COMPANY|CO|LP|LLP|PLC|HOLDINGS|USA|US|AMERICA|NA)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def load(path):
    print(f"读取 {path} ...")
    if path.lower().endswith((".csv", ".txt")):
        df = pd.read_csv(path, low_memory=False)
    else:
        df = pd.read_excel(path)
    print(f"  {len(df):,} 行")
    return df


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)

    frames = []
    for path in sys.argv[1:]:
        df = load(path)
        emp = find_col(df, "employer", "name")
        job = find_col(df, "job", "title")
        st = find_col(df, "worksite", "state") or find_col(df, "employer", "state")
        status = find_col(df, "case", "status")
        if not (emp and job):
            print(f"  跳过：找不到公司名或职位名列。实际列名: {list(df.columns)[:12]}")
            continue
        sub = pd.DataFrame({
            "employer": df[emp],
            "title": df[job],
            "state": df[st] if st else "",
            "status": df[status] if status else "CERTIFIED",
        })
        frames.append(sub)

    if not frames:
        sys.exit("没读到有效数据")

    df = pd.concat(frames, ignore_index=True)
    print(f"\n合计 {len(df):,} 条申请记录")

    # 只要批准的
    df = df[df["status"].astype(str).str.upper().str.contains("CERTIFIED", na=False)]
    df = df[~df["status"].astype(str).str.upper().str.contains("WITHDRAWN", na=False)]
    print(f"批准的: {len(df):,}")

    t = df["title"].astype(str).str.lower()
    df = df[t.apply(lambda x: any(k in x for k in TITLES))]
    print(f"职位对口: {len(df):,}")

    t = df["title"].astype(str).str.lower()
    df = df[~t.apply(lambda x: any(k in x for k in TITLE_SKIP))]
    print(f"去掉资深岗: {len(df):,}")

    if STATES:
        df = df[df["state"].astype(str).str.upper().str[:2].isin(STATES)]
        print(f"限定州: {len(df):,}")

    df["clean"] = df["employer"].apply(clean_name)
    low = df["clean"].str.lower()
    df = df[~low.apply(lambda x: any(b in x for b in BODYSHOPS))]
    print(f"去掉外包/咨询: {len(df):,}")

    g = df.groupby("clean").agg(
        sponsor_count=("title", "size"),
        titles=("title", lambda s: " | ".join(sorted(set(s))[:4])),
        states=("state", lambda s: ", ".join(sorted(set(str(x)[:2] for x in s))[:5])),
        raw_name=("employer", "first"),
    ).reset_index()

    g = g[g["sponsor_count"] >= MIN_COUNT].sort_values("sponsor_count", ascending=False)

    g[["raw_name", "sponsor_count", "titles", "states"]].to_csv(
        "sponsors.csv", index=False, encoding="utf-8-sig")

    out = pd.DataFrame({
        "company": g["raw_name"],
        "ats": "",
        "slug": g["clean"].str.lower().str.replace(r"[^a-z0-9]", "", regex=True),
        "priority": "",
    })
    out.to_csv("companies_new.csv", index=False, encoding="utf-8-sig")

    print(f"\n{'='*50}")
    print(f"目标公司: {len(g):,} 家")
    print(f"  sponsors.csv       完整名单（带职位和州）")
    print(f"  companies_new.csv  喂给 job_feed.py 用的")
    print("=" * 50)
    print("\nsponsor 次数最多的 25 家:\n")
    for _, r in g.head(25).iterrows():
        print(f"  {r['sponsor_count']:>4}  {str(r['raw_name'])[:38]:<40} {str(r['titles'])[:44]}")


if __name__ == "__main__":
    main()