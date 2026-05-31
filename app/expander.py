"""Keyword expansion for operator-pain-focused search queries.

Given a broad keyword like "property management", Gemini generates 4-6
specific Reddit search terms that target operator complaints, software
questions, and workflow pain — not consumer recommendation threads.
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


class _ExpansionResponse(BaseModel):
    terms: list[str]


_PROMPT_TEMPLATE = """\
You are a B2B market research expert helping discover operator pain points on Reddit.

Original keyword: "{keyword}"

Generate 4-6 Reddit search queries that will surface OPERATOR PAIN POSTS — \
threads where business owners or managers describe specific problems, ask for \
software solutions, complain about broken workflows, or look for better tools.

Rules:
- Do NOT simply prefix the original keyword onto generic words
- Generate terms that cover DISTINCT pain-point angles: billing, scheduling, \
  staff/hiring, communication with clients, compliance/reporting, field operations, \
  software integration, customer retention — pick the most relevant 4-6 for this niche
- Terms may or may not contain the original keyword; concise 2-5 word phrases work best
- Avoid terms that attract consumer recommendation posts, career questions, or local reviews
- Each term should feel like something a frustrated operator would actually search or post about

Example for "property management":
["tenant communication landlord", "maintenance request tracking software", \
"landlord rent collection problems", "property manager reporting tools", \
"HOA management software pain"]

Example for "dentist":
["dental practice scheduling software", "patient no-show dentist problem", \
"dental billing workflow issues", "dental staff management", \
"dental insurance claims pain"]

Return only the JSON list of terms. No explanations.\
"""

def expand_keyword(keyword: str) -> list[str]:
    """Expand a keyword into 4-6 operator-pain-focused search terms.

    Returns the expanded terms (excluding the original keyword — the caller
    decides whether to include it). Falls back to rule-based expansion when
    Gemini is unavailable or all retries fail.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — using rule-based keyword expansion.")
        return _fallback_expansion(keyword)

    from google import genai
    from google.genai import types

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_ExpansionResponse,
    )
    prompt = _PROMPT_TEMPLATE.format(keyword=keyword)

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            data = json.loads(response.text)
            terms = [t.strip() for t in data.get("terms", []) if isinstance(t, str) and t.strip()]
            if terms:
                logger.info("Expanded %r → %d terms: %s", keyword, len(terms), terms)
                return terms
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Gemini expansion attempt %d/%d failed: %s", attempt + 1, _MAX_RETRIES, exc
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))

    logger.warning("Gemini expansion failed (%s) — using rule-based fallback.", last_error)
    return _fallback_expansion(keyword)


def _fallback_expansion(keyword: str) -> list[str]:
    """Simple suffix-based expansion used when Gemini is unavailable."""
    return [
        f"{keyword} operations",
        f"{keyword} software problems",
        f"{keyword} workflow issues",
        f"{keyword} management tools",
    ]
