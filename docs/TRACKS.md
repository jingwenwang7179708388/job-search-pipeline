# Track definitions (v1, confirmed 2026-10-21)

This file defines what each Track means. It is the single source of truth for:
- the classification rules in `common/tracks.py` (used for both LCA titles and ATS postings),
- hand labeling (`experiments/track_labels_*.csv`),
- the per-Track accuracy reported in the README.

The rules approximate these definitions. When a rule and this file disagree, this file wins and the rule gets fixed.

## Principles

1. **One Track per posting.** Every title/posting gets exactly one Track.
2. **Track = the core work, not the seniority.** Level (Intern, Senior, Staff, Manager, Director, ...) is stored separately and never changes the Track. "Director of Data Science" is DATA_SCIENCE + level director.
3. **Decide by the decision question.** Each Track has one question about the main output of the job. When two Tracks seem to fit, the question decides.
4. **The core word limits the options.** The title's head noun narrows which Tracks are possible:
   - Analyst -> DATA_ANALYTICS, BUSINESS_ANALYTICS, SYSTEMS_ANALYSIS, FINANCIAL_ANALYTICS, QUANT, OTHER
   - Scientist -> DATA_SCIENCE, ML_RESEARCH, QUANT, OTHER
   - Engineer / Developer -> DATA_ANALYTICS (BI only), DATA_ENGINEERING, ML_ENGINEERING, ML_RESEARCH, SOFTWARE_ENGINEERING, QUANT, OTHER
5. **SOC codes only break ties or veto.** Only LCA filings have a SOC code; ATS postings do not. SOC is used when the title is vague, and SOC 17-, 19-, 25-, 29- (traditional engineering, life/physical science, education, healthcare) always means OTHER.
6. **Classification is separate from scope.** All Tracks below are classified. Which Tracks users can select and which companies we monitor is a product decision made elsewhere.
7. **When unsure, say so.** Labelers use `UNSURE`; it is counted separately, not forced into a Track.

## Overview

| Family | Track | Decision question: is the main output... |
|---|---|---|
| Analytics | DATA_ANALYTICS | data, reports and dashboards? |
| Analytics | BUSINESS_ANALYTICS | a business recommendation based on data? |
| Analytics | SYSTEMS_ANALYSIS | requirements or configuration for an IT system? |
| Finance | FINANCIAL_ANALYTICS | analysis of money, risk, credit or investments? |
| Finance | QUANT | quantitative models for trading, pricing or portfolio risk? |
| Data & ML | DATA_SCIENCE | a statistical model, experiment or forecast? |
| Data & ML | DATA_ENGINEERING | data pipelines, warehouses or data models? |
| Data & ML | ML_ENGINEERING | an ML model running in a product? |
| Data & ML | ML_RESEARCH | a new ML method (papers, novel models)? |
| Software | SOFTWARE_ENGINEERING | software code for a product or service? |
| — | OTHER | none of the above |

## Tracks

### DATA_ANALYTICS
- **Definition:** Pulls, cleans and summarizes data; builds reports and dashboards.
- **Question:** Is the main output data, reports or dashboards?
- **Includes:** Data Analyst, BI Analyst, Reporting Analyst, BI Engineer, BI Developer (Tableau / Power BI / Looker), Business Data Analyst, Statistical Programmer.
- **Excludes:** Data Engineer (-> DATA_ENGINEERING), Analytics Engineer (-> DATA_ENGINEERING), Financial Data Analyst (-> FINANCIAL_ANALYTICS), Data Scientist (-> DATA_SCIENCE).
- **SOC hints:** 15-2051.01 (BI analysts, 2018), 15-1199.08 (BI analysts, 2010).

### BUSINESS_ANALYTICS
- **Definition:** Uses data to answer a business question and recommend an action.
- **Question:** Is the main output a business recommendation based on data?
- **Includes:** Growth / Marketing / Product / Customer / Operations / Strategy Analyst, Marketing Research Analyst, Business Analyst whose SOC is analytical (15-2051.01, 15-1199.08, 15-2041, 15-2031, 15-2051.00, 13-1161).
- **Excludes:** Business Systems Analyst and IT-system Business Analysts (-> SYSTEMS_ANALYSIS), Financial Analyst (-> FINANCIAL_ANALYTICS), Product Manager (-> OTHER), Management Consultant (-> OTHER).
- **SOC hints:** 13-1161 (market research analysts), 15-2031 (operations research analysts).

### SYSTEMS_ANALYSIS
- **Definition:** Turns business needs into requirements, configuration or tests for IT systems.
- **Question:** Is the main output requirements or configuration for an IT system?
- **Includes:** Business Systems Analyst, Systems Analyst, Computer Systems Analyst, IT Business Analyst, Business Analyst with an IT-system name (SAP, Oracle, Salesforce/SFDC, Guidewire, ERP, CRM, ServiceNow, Workday), Business Analyst whose SOC is a computer-occupation code (15-1211, 15-1121, 15-1199.x except 15-1199.08, 15-125x, 15-113x), Analyst with no direction word and a computer-occupation SOC.
- **Excludes:** Programmer Analyst (-> SOFTWARE_ENGINEERING), Data Analyst (-> DATA_ANALYTICS).
- **SOC hints:** 15-1211 (2018), 15-1121 (2010).

### FINANCIAL_ANALYTICS
- **Definition:** Analyzes financial performance, budgets, risk, credit or investments.
- **Question:** Is the thing being analyzed money, risk, credit or investments?
- **Includes:** Financial Analyst, FP&A Analyst, Risk Analyst, Credit Analyst, Investment Analyst, Financial Data Analyst, Financial Reporting Analyst.
- **Excludes:** Accountant, Auditor (-> OTHER), Quantitative Analyst (-> QUANT), Risk Data Scientist (-> DATA_SCIENCE).
- **SOC hints:** 13-2051 (financial and investment analysts), 13-2054 (financial risk specialists), 13-2041 (credit analysts).

### QUANT
- **Definition:** Builds quantitative models for trading, pricing, or portfolio risk in finance.
- **Question:** Is the main output a quantitative model for trading, pricing or portfolio risk?
- **Includes:** Quantitative Analyst, Quantitative Researcher, Quant Trader, Quant Developer, Quantitative Strategist.
- **Excludes:** Quantitative User Research Analyst (not finance; decide by the other words), Financial Analyst (-> FINANCIAL_ANALYTICS).
- **SOC hints:** 13-2099.01 (financial quantitative analysts).

### DATA_SCIENCE
- **Definition:** Builds statistical models, runs experiments, makes forecasts.
- **Question:** Is the main output a statistical model, an experiment or a forecast?
- **Includes:** Data Scientist (incl. Product / Risk / ML-flavored Data Scientist), Decision Scientist, Statistician, Operations Research Analyst / Scientist.
- **Excludes:** Applied Scientist, Research Scientist (-> ML_RESEARCH), Data Analyst (-> DATA_ANALYTICS), ML Engineer (-> ML_ENGINEERING).
- **SOC hints:** 15-2051.00 (data scientists), 15-2041 (statisticians).

### DATA_ENGINEERING
- **Definition:** Builds and maintains data pipelines, warehouses and data models.
- **Question:** Is the main output a data pipeline, warehouse or data model?
- **Includes:** Data Engineer, Big Data Engineer, Analytics Engineer, ETL Developer, Data Warehouse Engineer, Data Architect, Software Engineer - Data.
- **Excludes:** Data Center Engineer (-> OTHER), Database Administrator (-> OTHER), BI Engineer (-> DATA_ANALYTICS).
- **SOC hints:** none reliable; the title decides.

### ML_ENGINEERING
- **Definition:** Builds, deploys and maintains ML / AI models in products.
- **Question:** Is the main output an ML model running in a product?
- **Includes:** ML Engineer, AI Engineer, Applied AI Engineer, MLOps Engineer, LLM Engineer, Computer Vision / NLP Engineer, Software Engineer - Machine Learning.
- **Excludes:** Research Engineer (-> ML_RESEARCH), Data Scientist (-> DATA_SCIENCE).
- **SOC hints:** none reliable (often filed as 15-1252, 15-2051 or 15-1221); the title decides.

### ML_RESEARCH
- **Definition:** Invents and evaluates new ML methods; often publishes; often requires a PhD.
- **Question:** Is the main output a new ML method?
- **Includes:** Research Scientist (computing), Research Engineer, ML Research Engineer, Applied Scientist, Machine Learning Scientist, Member of Technical Staff with SOC 15-1221.
- **Excludes:** Research Scientist with SOC 19- (life/physical science -> OTHER), UX Researcher (-> OTHER), Research Data Scientist (-> DATA_SCIENCE).
- **SOC hints:** 15-1221 (2018), 15-1111 (2010).

### SOFTWARE_ENGINEERING
- **Definition:** Writes, tests and operates software for a product or service.
- **Question:** Is the main output software code?
- **Includes:** Software Engineer, Software Developer, SDE, Backend / Frontend / Full Stack / Mobile Engineer, Java / .NET Developer, Programmer Analyst, Computer Programmer / Programmer, DevOps / SRE, QA / Test Automation Engineer, Member of Technical Staff with SOC 15-125x.
- **Excludes:** Network Engineer, Security Engineer, Systems Engineer (IT infrastructure -> OTHER), Solutions Architect (-> OTHER), hardware engineers (-> OTHER).
- **SOC hints:** 15-1252, 15-1253, 15-1251 (2018); 15-1131, 15-1132, 15-1133 (2010).

### OTHER
Everything else, for example: Product / Project / Program Manager, Consultant, Solutions Architect, Accountant, Auditor, IT Auditor (its main output is an audit report, not system requirements), Network / Security / Systems Engineer, Database Administrator, traditional engineering, life and physical science, education, healthcare, legal.

## Precedence when a title has several signals

- **Analyst** (first match wins): IT-system name -> SYSTEMS_ANALYSIS; quant/quantitative + finance context -> QUANT; financial / finance / credit / risk / investment / accounting -> FINANCIAL_ANALYTICS; data / BI / reporting -> DATA_ANALYTICS; growth / marketing / product / customer / operations / strategy -> BUSINESS_ANALYTICS; business systems / systems -> SYSTEMS_ANALYSIS; plain "business analyst" -> by SOC (analytical SOC -> BUSINESS_ANALYTICS, computer-occupation SOC (15-1211, 15-1121, 15-1199.x except 15-1199.08, 15-125x, 15-113x) -> SYSTEMS_ANALYSIS, otherwise OTHER); plain "analyst" with no direction word -> by SOC (BI SOC -> DATA_ANALYTICS, other analytical SOC -> BUSINESS_ANALYTICS, then computer-occupation SOC -> SYSTEMS_ANALYSIS, financial SOC -> FINANCIAL_ANALYTICS, 13-2099.01 -> QUANT, otherwise OTHER). Finance words beat data words, and data words beat business words, because finance is a separate career direction and "business" is not.
- **Scientist:** quant -> QUANT; data / decision / product / operations research -> DATA_SCIENCE; research / applied / ML / AI -> ML_RESEARCH.
- **Engineer / Developer / SDE:** quant -> QUANT; BI -> DATA_ANALYTICS; research -> ML_RESEARCH; ML / AI / CV / NLP / LLM -> ML_ENGINEERING; data / analytics / ETL -> DATA_ENGINEERING; software / backend / frontend / full stack / mobile / devops / QA -> SOFTWARE_ENGINEERING.

## Settled edge cases

| Title | Track | Why |
|---|---|---|
| Business Data Analyst | DATA_ANALYTICS | data beats business for Analyst |
| Product Analyst | BUSINESS_ANALYTICS | business recommendation |
| BI Engineer / BI Developer | DATA_ANALYTICS | output is reports and dashboards |
| Analytics Engineer | DATA_ENGINEERING | output is data models |
| SAP Business Analyst | SYSTEMS_ANALYSIS | output is system requirements |
| Programmer Analyst | SOFTWARE_ENGINEERING | output is code |
| Financial Data Analyst | FINANCIAL_ANALYTICS | domain (finance) beats function for Analyst |
| Quant Developer | QUANT | quant work in finance |
| Operations Research Analyst | DATA_SCIENCE | optimization and modeling |
| Data Scientist, Machine Learning | DATA_SCIENCE | head noun Scientist + data |
| Software Engineer, Machine Learning | ML_ENGINEERING | ML beats software |
| Machine Learning Research Engineer | ML_RESEARCH | research beats ML for Engineer |
| Applied Scientist | ML_RESEARCH | Amazon-style applied ML science |
| Member of Technical Staff | by SOC | title has no direction |
| Data Science Manager | DATA_SCIENCE (+ level manager) | level never changes Track |

## Labeling guide

For each row in `track_labels_*.csv`, read the raw title (and SOC if present) and fill `my_label` with the Track this file says it belongs to, or `UNSURE`. Judge by the definition and decision question, not by the words in the title. Do not look at `predicted_track` until you have decided.

## Scope

SOFTWARE_ENGINEERING postings from companies we already monitor are classified and can be recommended, but we do not add companies to the monitored list only because they sponsor SWE roles.
