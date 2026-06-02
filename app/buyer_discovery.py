"""Buyer Discovery Engine.

Converts pain-point clusters into actionable buyer intelligence:
  company_types          — specific org profiles that have this problem + budget
  buyer_roles            — day-to-day users who feel the pain
  decision_makers        — economic buyers / purchase authority holders
  search_keywords        — phrases buyers search when looking for solutions
  linkedin_search_queries — LinkedIn people-search strings for prospecting
  outreach_angles        — pain-focused cold outreach talking points
  why_they_would_buy     — primary purchase motivation (1-2 sentences)

All cluster profiles are generated concurrently via ThreadPoolExecutor.
Falls back to a rule-based profile when Gemini is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel

from app.models.buyer import BuyerDiscoveryInput, BuyerProfile

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
_RATE_LIMIT_WORKERS = 3          # max concurrent Gemini calls (free tier: 5 RPM)
_RETRY_AFTER_RE = re.compile(r"retry in (\d+(?:\.\d+)?)s", re.I)


# ---------------------------------------------------------------------------
# Internal Gemini schema (mirrors BuyerProfile minus the context fields)
# ---------------------------------------------------------------------------

class _GeminiSchema(BaseModel):
    company_types: list[str]
    buyer_roles: list[str]
    decision_makers: list[str]
    search_keywords: list[str]
    linkedin_search_queries: list[str]
    outreach_angles: list[str]
    why_they_would_buy: str


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a B2B go-to-market strategist helping a SaaS founder identify and \
reach the most qualified buyers for a specific pain point.

### Cluster context
Cluster name             : {cluster_name}
Problem                  : {problem}
Primary buyer type       : {buyer_type}
Industry                 : {industry}
Average opportunity score: {avg_score}/10

### Output instructions

company_types (4-6 items):
  Specific, named types of organisations that experience this problem AND \
  have budget to pay for a software solution. Include size/context clues \
  (e.g. "Residential property management firms with 50-500 units", not \
  just "companies").

buyer_roles (4-6 items):
  Job titles of the people who feel this pain day-to-day. These are the \
  champions who will push for the purchase internally.

decision_makers (3-5 items):
  Job titles of the people who sign off on the budget. May overlap with \
  buyer_roles in small companies, but should reflect economic buyer authority \
  in mid-market / enterprise.

search_keywords (6-8 items):
  Exact phrases these buyers type into Google or Reddit when looking for a \
  solution. Mix problem-aware ("how to track maintenance requests") and \
  solution-aware ("maintenance request software small landlord") terms.

linkedin_search_queries (4-6 items):
  Ready-to-use LinkedIn people-search strings a sales rep would enter to \
  find prospects. Format: job title + company type or industry keyword \
  (e.g. "Property Manager residential property management").

outreach_angles (4-6 items):
  Concise, pain-focused talking points for cold email, LinkedIn DMs, or \
  conference conversations. Reference a specific outcome or metric the \
  buyer wants. Start each with an action verb or a number.

why_they_would_buy:
  1-2 sentences explaining the primary purchase motivation — the \
  business risk or cost that makes this a must-solve, not a nice-to-have.

Return strict JSON matching the schema. No markdown fences, no extra keys.\
"""


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def discover_buyers(inp: BuyerDiscoveryInput) -> BuyerProfile:
    """Generate a BuyerProfile for a single cluster input.

    Falls back to a rule-based profile on EnvironmentError or API failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — using rule-based buyer profile.")
        return _fallback(inp)

    from google import genai
    from google.genai import types

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_GeminiSchema,
    )
    prompt = _PROMPT_TEMPLATE.format(
        cluster_name=inp.cluster_name or inp.problem,
        problem=inp.problem,
        buyer_type=inp.buyer_type or "Unknown",
        industry=inp.industry or "General Business",
        avg_score=inp.average_opportunity_score,
    )

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            data = json.loads(response.text)
            return BuyerProfile(
                cluster_name=inp.cluster_name,
                problem=inp.problem,
                **{k: data[k] for k in _GeminiSchema.model_fields},
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Buyer discovery attempt %d/%d failed for %r: %s",
                attempt + 1, _MAX_RETRIES, inp.cluster_name or inp.problem, exc,
            )
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                m = _RETRY_AFTER_RE.search(str(exc))
                if m:
                    delay = max(delay, float(m.group(1)))
                time.sleep(delay)

    logger.warning(
        "Buyer discovery failed after %d attempts (%s) — using fallback.",
        _MAX_RETRIES, last_error,
    )
    return _fallback(inp)


# ---------------------------------------------------------------------------
# Batch (concurrent)
# ---------------------------------------------------------------------------

def discover_buyers_for_clusters(clusters: list[dict]) -> list[BuyerProfile]:
    """Generate BuyerProfiles for all clusters concurrently.

    Each cluster dict is converted to a BuyerDiscoveryInput using its
    available fields. Results are returned in the same order as clusters.
    """
    if not clusters:
        return []

    inputs = [_cluster_to_input(c) for c in clusters]
    results: list[BuyerProfile | None] = [None] * len(inputs)

    with ThreadPoolExecutor(max_workers=min(len(inputs), _RATE_LIMIT_WORKERS)) as executor:
        future_to_idx = {
            executor.submit(discover_buyers, inp): i
            for i, inp in enumerate(inputs)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                logger.warning(
                    "Concurrent buyer discovery failed for cluster %d: %s", idx, exc
                )
                results[idx] = _fallback(inputs[idx])

    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cluster_to_input(cluster: dict) -> BuyerDiscoveryInput:
    """Map a cluster dict to a BuyerDiscoveryInput."""
    buyer_types: list[str] = cluster.get("buyer_types") or []
    industries: list[str] = cluster.get("industries") or []
    example_problems: list[str] = cluster.get("example_problems") or []

    problem = (
        example_problems[0]
        if example_problems
        else cluster.get("cluster_name", "")
    )

    return BuyerDiscoveryInput(
        problem=problem,
        buyer_type=buyer_types[0] if buyer_types else "",
        industry=industries[0] if industries else "",
        cluster_name=cluster.get("cluster_name", ""),
        average_opportunity_score=float(
            cluster.get("average_opportunity_score", 0.0)
        ),
    )


def _fallback(inp: BuyerDiscoveryInput) -> BuyerProfile:
    """Rule-based buyer profile used when Gemini is unavailable."""
    industry = inp.industry or "Business"
    buyer = inp.buyer_type or "Manager"
    problem_lc = inp.problem.lower()

    return BuyerProfile(
        cluster_name=inp.cluster_name,
        problem=inp.problem,
        company_types=[
            f"{industry} firms",
            f"Small and mid-size {industry.lower()} companies",
        ],
        buyer_roles=[buyer, "Operations Manager"],
        decision_makers=["CEO / Owner", f"Director of {industry}"],
        search_keywords=[
            f"{industry.lower()} software",
            f"{problem_lc} solution",
            f"best tool for {industry.lower()}",
        ],
        linkedin_search_queries=[
            f"{buyer} {industry}",
            f"Operations Manager {industry}",
        ],
        outreach_angles=[
            f"Reduce time spent on {problem_lc}",
            f"Fix {problem_lc} without extra headcount",
        ],
        why_they_would_buy=(
            f"{buyer}s in {industry} face recurring costs from {problem_lc}. "
            "A focused solution removes this bottleneck and directly improves team output."
        ),
    )
