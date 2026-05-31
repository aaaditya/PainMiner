"""Buyer discovery from a problem cluster.

Given a problem description, buyer type, and industry, Gemini identifies:
  - company_types    : specific company profiles that have this problem
  - buyer_roles      : job titles with both the pain and budget authority
  - search_keywords  : what these buyers search when looking for a solution
  - outreach_angles  : pain-focused talking points for cold outreach

Falls back to a lightweight rule-based profile when Gemini is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import time

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0


# ---------------------------------------------------------------------------
# Gemini response schema
# ---------------------------------------------------------------------------

class _BuyerDiscoverySchema(BaseModel):
    company_types: list[str]
    buyer_roles: list[str]
    search_keywords: list[str]
    outreach_angles: list[str]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a B2B go-to-market strategist helping a SaaS founder identify the \
most qualified buyers for a specific pain point.

### Input
Problem      : {problem}
Buyer type   : {buyer_type}
Industry     : {industry}

### Instructions

company_types (4-6 items):
  Specific types of organisations that experience this problem AND have \
  budget to pay for a SaaS solution. Be concrete — not "companies" but \
  e.g. "Residential property management firms with 50-500 units".

buyer_roles (4-6 items):
  Job titles of the people who feel this pain directly AND have \
  purchasing authority or strong influence. Include both champion \
  (day-to-day user) and economic buyer where they differ.

search_keywords (6-8 items):
  Exact phrases these buyers type into Google or Reddit when looking \
  for a solution. Mix problem-aware terms ("how to track maintenance \
  requests") and solution-aware terms ("maintenance request software \
  small landlord").

outreach_angles (4-6 items):
  Concise, pain-focused talking points for cold email, LinkedIn, or \
  conference conversations. Each angle should reference a specific \
  outcome the buyer wants, not a feature. Start each with an action \
  verb or metric.

Return strict JSON matching the schema. No markdown fences, no extra keys.\
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_buyers(problem: str, buyer_type: str, industry: str) -> dict:
    """Return a buyer profile for a given problem + buyer context.

    Tries Gemini first; falls back to a rule-based profile on failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — using rule-based buyer profile.")
        return _fallback(problem, buyer_type, industry)

    from google import genai
    from google.genai import types

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_BuyerDiscoverySchema,
    )
    prompt = _PROMPT_TEMPLATE.format(
        problem=problem,
        buyer_type=buyer_type or "Unknown",
        industry=industry or "General Business",
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
            return _validate(data)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Buyer discovery attempt %d/%d failed: %s",
                attempt + 1, _MAX_RETRIES, exc,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))

    logger.warning(
        "Buyer discovery failed after %d attempts (%s) — using fallback.",
        _MAX_RETRIES, last_error,
    )
    return _fallback(problem, buyer_type, industry)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate(data: dict) -> dict:
    required = ("company_types", "buyer_roles", "search_keywords", "outreach_angles")
    for field in required:
        if field not in data or not isinstance(data[field], list):
            raise ValueError(f"Missing or invalid field in Gemini response: {field!r}")
    return {k: data[k] for k in required}


def _fallback(problem: str, buyer_type: str, industry: str) -> dict:
    """Minimal rule-based buyer profile used when Gemini is unavailable."""
    kw_base = f"{industry.lower()} {buyer_type.lower()}"
    return {
        "company_types": [
            f"{industry} companies",
            f"Small and mid-size {industry.lower()} businesses",
        ],
        "buyer_roles": [
            buyer_type,
            "Operations Manager",
            "CEO / Founder",
        ],
        "search_keywords": [
            f"{kw_base} software",
            f"{problem.lower()} solution",
            f"best tool for {industry.lower()}",
            f"{buyer_type.lower()} pain points",
        ],
        "outreach_angles": [
            f"Reduce time spent on {problem.lower()}",
            f"Help {buyer_type.lower()}s fix {problem.lower()} without extra headcount",
        ],
    }
