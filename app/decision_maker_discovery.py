"""Decision Maker Discovery Engine.

Given a company + market cluster, infers the likely decision-maker roles,
their seniority, buying authority, and LinkedIn search queries.

Important limitations:
  - No email discovery or scraping
  - No LinkedIn scraping (queries are Google site: strings only)
  - No personal data collected — names are always empty
  - Output represents role profiles, not real individuals

Pipeline per company:
  1. Build a Gemini prompt from company + cluster context
  2. Receive a list of inferred decision-maker role profiles
  3. Normalise seniority and buying-power fields
  4. Generate linkedin_query for each role

All companies are processed concurrently via ThreadPoolExecutor.
Falls back to a role-priority table when Gemini is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel

from app.models.decision_maker import CompanyDecisionMakers, DecisionMaker

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
_RATE_LIMIT_WORKERS = 3
_MAX_DM_PER_COMPANY = 5
_RETRY_AFTER_RE = re.compile(r"retry in (\d+(?:\.\d+)?)s", re.I)


# ---------------------------------------------------------------------------
# Buying-power scale
# ---------------------------------------------------------------------------

_BUYING_POWER_MAP: dict[str, int] = {
    "owner": 10, "founder": 10, "co-founder": 10,
    "ceo": 10, "president": 10, "principal": 10,
    "vp": 10, "vice president": 10, "head of": 9, "chief": 10,
    "director": 8, "regional": 7,
    "manager": 6, "portfolio": 6, "senior manager": 7,
    "coordinator": 4, "associate": 4, "administrator": 4,
    "specialist": 3, "analyst": 3,
    "individual": 2, "ic": 2, "contributor": 2,
}


def _infer_buying_power(title: str, seniority: str) -> int:
    """Return 0-10 buying-power score from title + seniority keywords."""
    combined = f"{title} {seniority}".lower()
    for keyword, power in _BUYING_POWER_MAP.items():
        if keyword in combined:
            return power
    return 5  # default mid-range


def _infer_seniority(title: str) -> str:
    """Map a job title to a seniority band."""
    t = title.lower()
    if any(w in t for w in ("owner", "founder", "ceo", "president", "principal", "chief")):
        return "Owner"
    if any(w in t for w in ("vp", "vice president", "head of")):
        return "VP"
    if "director" in t:
        return "Director"
    if "manager" in t or "regional" in t or "portfolio" in t:
        return "Manager"
    if any(w in t for w in ("coordinator", "associate", "administrator")):
        return "Coordinator"
    return "IC"


def _linkedin_query(title: str, company_name: str) -> str:
    """Build a Google site: query to find LinkedIn profiles."""
    # Extract company short name (first 2 meaningful words)
    short_name = " ".join(
        w for w in company_name.split()
        if w.lower() not in {"the", "a", "an", "and", "of", "for", "inc", "llc", "ltd"}
    )[:40]
    return f'site:linkedin.com/in "{title}" "{short_name}"'


# ---------------------------------------------------------------------------
# Gemini schema
# ---------------------------------------------------------------------------

class _DMSchema(BaseModel):
    title: str
    department: str
    reason: str


class _DMListSchema(BaseModel):
    decision_makers: list[_DMSchema]


# ---------------------------------------------------------------------------
# Role-priority fallback tables (used when Gemini is unavailable)
# ---------------------------------------------------------------------------

_FALLBACK_ROLES: dict[str, list[str]] = {
    "property management": [
        "Director of Property Management",
        "VP of Operations",
        "Regional Property Manager",
        "Community Manager",
        "Maintenance Director",
    ],
    "construction": [
        "Operations Manager",
        "Project Manager",
        "Field Operations Director",
        "Owner / Principal",
    ],
    "warehouse": [
        "Warehouse Manager",
        "Operations Director",
        "Supply Chain Director",
        "VP of Logistics",
    ],
    "dental": [
        "Practice Owner",
        "Office Manager",
        "Operations Director",
    ],
    "accounting": [
        "Managing Partner",
        "Firm Owner",
        "Operations Manager",
    ],
    "restaurant": [
        "Owner / Operator",
        "General Manager",
        "Director of Operations",
    ],
    "gym": [
        "Gym Owner",
        "Operations Manager",
        "Fitness Director",
    ],
    "default": [
        "Operations Manager",
        "Director of Operations",
        "VP of Operations",
        "Owner / Founder",
    ],
}


def _fallback_roles(cluster_name: str, buyer_roles: list[str]) -> list[dict]:
    """Return role-priority-table entries for a cluster."""
    key = next(
        (k for k in _FALLBACK_ROLES if k in cluster_name.lower()), "default"
    )
    titles = _FALLBACK_ROLES[key][:_MAX_DM_PER_COMPANY]
    # Merge buyer_roles at the front if they're not already included
    merged = list(dict.fromkeys(buyer_roles[:2] + titles))[:_MAX_DM_PER_COMPANY]
    return [
        {"title": t, "department": "Operations", "reason": f"Key decision-maker for {cluster_name} solutions"}
        for t in merged
    ]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a B2B sales intelligence analyst. Given the company and market \
cluster below, identify the {max_dm} most likely decision makers who would \
evaluate and approve a software purchase to solve the stated problem.

### Market cluster
Cluster name : {cluster_name}
Problem      : {problem}
Buyer roles  : {buyer_roles}

### Company
Name         : {company_name}
Website      : {website}
Description  : {description}

### Output instructions

For each decision maker provide:
  title      — exact job title (use common B2B titles, no made-up names)
  department — functional department (e.g. Operations, Finance, Property Management)
  reason     — 1 sentence: why this role controls the buying decision

Return exactly {max_dm} roles ordered from highest to lowest buying authority.
No names. No email addresses. No personal data. Strict JSON only.\
"""


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def discover_decision_makers(
    company: dict,
    cluster: dict,
    buyer_roles: list[str] | None = None,
    max_dm: int = _MAX_DM_PER_COMPANY,
) -> CompanyDecisionMakers:
    """Infer decision-maker roles for one company + cluster.

    Returns a CompanyDecisionMakers with up to *max_dm* inferred roles.
    Falls back to a role-priority table when Gemini is unavailable.
    """
    company_name: str = company.get("name", "")
    website: str = company.get("website", "")
    description: str = (company.get("description") or "")[:300]
    cluster_name: str = cluster.get("cluster_name", "")
    problem: str = (cluster.get("example_problems") or [""])[0]
    roles: list[str] = buyer_roles or cluster.get("buyer_types", [])

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — using role-priority fallback.")
        raw_roles = _fallback_roles(cluster_name, roles)
        return _build_result(company_name, website, cluster_name, raw_roles)

    from google import genai
    from google.genai import types

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_DMListSchema,
    )
    prompt = _PROMPT_TEMPLATE.format(
        max_dm=max_dm,
        cluster_name=cluster_name,
        problem=problem or cluster_name,
        buyer_roles=", ".join(roles) if roles else "Unknown",
        company_name=company_name,
        website=website,
        description=description or "No description available",
    )

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model_name, contents=prompt, config=config
            )
            data = json.loads(response.text)
            raw_roles = data.get("decision_makers", [])
            return _build_result(company_name, website, cluster_name, raw_roles)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "DM discovery attempt %d/%d failed for %r: %s",
                attempt + 1, _MAX_RETRIES, company_name, exc,
            )
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                m = _RETRY_AFTER_RE.search(str(exc))
                if m:
                    delay = max(delay, float(m.group(1)))
                time.sleep(delay)

    logger.warning(
        "DM discovery failed for %r after %d attempts (%s) — using fallback.",
        company_name, _MAX_RETRIES, last_error,
    )
    return _build_result(
        company_name, website, cluster_name,
        _fallback_roles(cluster_name, roles),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_result(
    company_name: str,
    website: str,
    cluster_name: str,
    raw_roles: list[dict],
) -> CompanyDecisionMakers:
    dms: list[DecisionMaker] = []
    for role in raw_roles[:_MAX_DM_PER_COMPANY]:
        title: str = role.get("title", "")
        if not title:
            continue
        seniority = _infer_seniority(title)
        buying_power = _infer_buying_power(title, seniority)
        dms.append(
            DecisionMaker(
                name="",
                title=title,
                seniority=seniority,
                department=role.get("department", ""),
                buying_power=buying_power,
                linkedin_query=_linkedin_query(title, company_name),
                reason=role.get("reason", ""),
            )
        )
    # Sort descending by buying_power
    dms.sort(key=lambda d: d.buying_power, reverse=True)
    return CompanyDecisionMakers(
        company_name=company_name,
        website=website,
        cluster=cluster_name,
        decision_makers=dms,
    )


# ---------------------------------------------------------------------------
# Batch (concurrent across companies)
# ---------------------------------------------------------------------------

def discover_decision_makers_batch(
    companies: list[dict],
    clusters_by_name: dict[str, dict],
    buyer_roles_by_cluster: dict[str, list[str]] | None = None,
    max_dm: int = _MAX_DM_PER_COMPANY,
) -> list[CompanyDecisionMakers]:
    """Discover decision makers for multiple companies concurrently.

    Args:
        companies:              Flat company dicts (each has a 'cluster' key).
        clusters_by_name:       Map of cluster_name → cluster dict.
        buyer_roles_by_cluster: Map of cluster_name → buyer_roles list.
        max_dm:                 Max decision makers per company.

    Returns list in the same order as *companies*.
    """
    if not companies:
        return []

    results: list[CompanyDecisionMakers | None] = [None] * len(companies)
    brc = buyer_roles_by_cluster or {}

    with ThreadPoolExecutor(max_workers=_RATE_LIMIT_WORKERS) as executor:
        future_to_idx = {
            executor.submit(
                discover_decision_makers,
                company,
                clusters_by_name.get(company.get("cluster", ""), {}),
                brc.get(company.get("cluster", ""), []),
                max_dm,
            ): i
            for i, company in enumerate(companies)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                company = companies[idx]
                logger.warning(
                    "DM discovery failed for company %d (%s): %s",
                    idx, company.get("name", "?"), exc,
                )
                results[idx] = CompanyDecisionMakers(
                    company_name=company.get("name", ""),
                    website=company.get("website", ""),
                    cluster=company.get("cluster", ""),
                )

    return [r for r in results if r is not None]
