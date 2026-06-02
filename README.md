# PainMiner

Analyze Reddit discussions to surface business pain points, cluster them into market themes, generate SaaS ideas, and discover real companies to target.

## Requirements

- Python 3.10+
- A Reddit API application — see [Reddit setup](#reddit-setup) *(only needed for PRAW; Firecrawl is the active source)*
- A Google AI Studio API key — see [Gemini setup](#gemini-setup)
- A Firecrawl API key — see [Firecrawl setup](#firecrawl-setup)

## Gemini setup

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey) and click **Create API key**.
2. Copy the key (starts with `AIza…`).

> Default model: `gemini-2.5-flash`. Override with `GEMINI_MODEL` env var.

## Firecrawl setup

1. Sign up at [firecrawl.dev](https://www.firecrawl.dev).
2. Go to **Dashboard → API Keys → Create key**.
3. Copy the key (starts with `fc-…`).

## Reddit setup *(optional — inactive in the current pipeline)*

1. Go to [reddit.com Preferences → Apps → Create another app](https://www.reddit.com/prefs/apps).
2. Choose type **script**, fill in a name and any redirect URI.
3. Note the **client id** (under the app name) and **client secret**.

## Environment variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | — | Google AI Studio key |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Gemini model name |
| `FIRECRAWL_API_KEY` | Yes | — | Firecrawl API key |
| `REDDIT_CLIENT_ID` | No | — | Reddit app client ID (inactive) |
| `REDDIT_CLIENT_SECRET` | No | — | Reddit app secret (inactive) |
| `REDDIT_USER_AGENT` | No | `painminer/1.0` | Reddit user agent |

> **Fallback:** if `GEMINI_API_KEY` is absent, AI extraction and clustering fall back to rule-based logic automatically.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the server

```bash
uvicorn app.main:app --reload
```

API available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

## API endpoints

### `POST /analyze`

Run the full pain-discovery pipeline for a keyword. Returns per-post opportunities ranked by score.

```json
// Request
{"keyword": "warehouse"}

// Response
{
  "keyword": "warehouse",
  "expanded_terms": ["warehouse operations", "..."],
  "total_posts": 30,
  "opportunities": [
    {
      "problem": "Manual inventory tracking wastes hours per week",
      "buyer_type": "Warehouse Manager",
      "industry": "Logistics",
      "severity_score": 9,
      "urgency_score": 8,
      "why_this_is_a_problem": "...",
      "opportunity_score": 10
    }
  ]
}
```

### `POST /analyze-market`

Cluster opportunities into recurring market themes.

```json
{"keyword": "warehouse"}
→ {"market": "warehouse", "expanded_terms": [...], "clusters": [...]}
```

### `POST /generate-saas`

Generate ranked SaaS product ideas from market clusters.

```json
{"keyword": "property management"}
→ {
  "market": "property management",
  "clusters": [...],
  "saas_opportunities": [
    {
      "saas_name": "TenantLoop",
      "one_line_pitch": "...",
      "ideal_customer": "...",
      "pricing_model": "...",
      "why_existing_solutions_fail": "...",
      "mvp_features": ["..."],
      "go_to_market": ["..."],
      "rank_score": 8.79
    }
  ]
}
```

### `POST /discover-buyers`

Identify buyer personas and outreach intelligence for each cluster.

```json
{"keyword": "property management"}
→ {
  "market": "property management",
  "clusters": [...],
  "buyer_profiles": [
    {
      "cluster_name": "Tenant Communication",
      "company_types": ["Residential PM firms with 50-500 units", "..."],
      "buyer_roles": ["Property Manager", "Community Manager", "..."],
      "decision_makers": ["Director of PM", "VP of Operations", "..."],
      "search_keywords": ["property maintenance vendor management", "..."],
      "linkedin_search_queries": ["Property Manager residential", "..."],
      "outreach_angles": ["Reduce response times by 50%", "..."],
      "why_they_would_buy": "..."
    }
  ]
}
```

### `POST /discover-companies`

Full pipeline: expand → crawl → extract → score → cluster → buyer profiles → **company discovery**.

Finds real company websites matching each cluster's buyer profile using Firecrawl web search. Deduplicates by domain.

```json
{"keyword": "property management"}
→ {
  "market_summary": {
    "keyword": "property management",
    "total_posts": 30,
    "total_clusters": 6,
    "total_companies": 45,
    "unique_companies": 38
  },
  "clusters": [...],
  "buyer_profiles": [...],
  "companies": [
    {
      "cluster": "Tenant Communication",
      "name": "AppFolio",
      "website": "https://appfolio.com",
      "location": "Santa Barbara, CA",
      "description": "Property management software for...",
      "employee_range": "500-999",
      "why_match": "Targets Tenant Communication buyers. Key value: Reduce response times by 50%."
    }
  ]
}
```

### `POST /test-search`

Quick URL discovery — no scraping, no AI.

```json
{"keyword": "warehouse"}
→ {"urls_found": ["https://reddit.com/..."], "sample_titles": ["..."]}
```

## Batch analysis

```bash
python scripts/batch_analysis.py
```

Runs the full pipeline across 10 niches and prints a ranked market report.



## CSV Export

`app/exporter.py` --- `POST /export-csv`

Runs the full pipeline and writes four CSV files to `exports/<keyword>/` for import into Clay, Apollo, HubSpot, Instantly, or any spreadsheet.

### Files generated

| File | Columns |
|---|---|
| `opportunities.csv` | keyword, cluster, problem, buyer_role, severity_score, urgency_score, opportunity_score, outreach_angle |
| `companies.csv` | cluster, company_name, website, location, employee_range, why_match |
| `decision_makers.csv` | company, website, cluster, title, seniority, department, buying_power, linkedin_query, reason |
| `saas_ideas.csv` | rank, saas_name, one_line_pitch, ideal_customer, pricing_model, rank_score, source_cluster |

### CRM import tips

- **Clay**: import `companies.csv` and enrich with Clay waterfall
- **Apollo**: import `companies.csv`, use `website` for domain matching
- **HubSpot**: import `companies.csv` as Companies, `decision_makers.csv` as Contacts
- **Instantly**: use `linkedin_query` to find emails via enrichment

## Decision maker discovery

`app/decision_maker_discovery.py` — `discover_decision_makers(company, cluster)`

Given a discovered company and its market cluster, Gemini infers the decision-maker roles most likely to evaluate and approve a software purchase.

### Output per company

```json
{
  "company_name": "Livingintown Property Management",
  "website": "https://livingintown.com",
  "cluster": "Tenant Communication",
  "decision_makers": [
    {
      "name": "",
      "title": "Director of Property Management",
      "seniority": "Director",
      "department": "Property Management",
      "buying_power": 8,
      "linkedin_query": "site:linkedin.com/in \"Director of Property Management\" \"Livingintown\"",
      "reason": "Responsible for all PM operations and technology decisions."
    }
  ]
}
```

### Buying-power scale

| Score | Level |
|---|---|
| 10 | Owner / Founder / CEO / VP |
| 8 | Director |
| 7 | Regional Manager / Senior Manager |
| 6 | Manager / Portfolio Manager |
| 4 | Coordinator / Associate |
| 2 | Individual Contributor |

### LinkedIn query generation

Each role produces a Google `site:` query to find profiles without scraping LinkedIn directly:

```
site:linkedin.com/in "Director of Property Management" "Livingintown Property Management"
```

### Role-priority fallback table

When Gemini is unavailable, a built-in table supplies prioritized roles by industry:
Property Management, Construction, Warehouse, Dental, Accounting, Restaurant, Gym.

### Limitations

- **No email discovery** — email addresses are never collected or inferred
- **No LinkedIn scraping** — only search query strings are generated
- **No personal data** — `name` field is always empty; output represents role profiles, not real individuals
- **No lead enrichment** — that is a future phase

## Company quality scoring

After discovery, every company passes through a two-stage quality filter in `app/company_scorer.py`.

### Stage 1 — Rule-based scoring (always runs)

| Signal | Points | Examples |
|---|---|---|
| Domain contains media/ranking word | −5 | `multihousingnews.com` |
| Directory / ranking text | −4 | "top companies", "best companies", "list of" |
| Media / publication text | −4 | "news", "magazine", "blog", "article" |
| Aggregator text | −4 | "marketplace", "vendor directory", "listing site" |
| Official company page indicator | +1 | "About Us", "Services", "Request Demo" |
| Company size language | +1 | "units managed", "employees", "clients served" |
| Business voice | +1 | "we manage", "we serve", "our customers" |

Base score: **7** (assume valid unless negatives drop it below threshold).  
Threshold: `company_score >= 6`.

### Stage 2 — Gemini validation (borderline only, score 4–7)

Borderline companies are sent to Gemini for classification:

- **A) operating_business** → survives
- **B) media**, **C) directory**, **D) article/listicle**, **E) association** → rejected

Falls back to rule-based decision if Gemini is unavailable.

### POST /validate-companies

Returns raw vs filtered companies with per-company scores, removal reasons, and precision metrics.

## Architecture

```
app/
├── main.py                    # All 6 endpoints
├── expander.py                # Gemini keyword expansion
├── ai_extractor.py            # Concurrent Gemini extraction + rule-based fallback
├── clusterer.py               # Gemini semantic clustering
├── scorer.py                  # Opportunity scoring formula
├── saas_generator.py          # Gemini SaaS idea generation + ranking
├── buyer_discovery.py         # Buyer intelligence via Gemini
├── company_discovery.py       # Company discovery via Firecrawl web search
├── extractor.py               # Rule-based fallback extractor
├── crawler.py                 # (legacy PRAW — inactive)
├── ai/
│   ├── provider.py            # Abstract AIProvider
│   └── gemini_provider.py     # Gemini Flash implementation
├── sources/
│   ├── source.py              # Abstract DataSource
│   └── firecrawl_source.py    # Firecrawl: Reddit search + multi-term + URL-only
└── models/
    ├── buyer.py               # BuyerDiscoveryInput, BuyerProfile
    └── company.py             # Company, ClusterCompanies
scripts/
└── batch_analysis.py          # Multi-niche batch runner + niche ranking report
```
