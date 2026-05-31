"""SaaS opportunity generation from market clusters.

For each cluster produced by app.clusterer, Gemini generates a complete
B2B SaaS idea with name, pitch, pricing model, MVP features, and GTM plan.
All cluster ideas are generated concurrently, then ranked by three signals:
  - pain_intensity  (average_opportunity_score from the cluster)
  - buyer_clarity   (how specific and concentrated the buyer persona is)
  - recurrence      (mention_count — how often the theme appeared)
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0

# Generic buyer labels that signal a vague buyer persona
_GENERIC_BUYERS = frozenset(
    {"business owner", "general", "unknown", "manager", "professional", "user"}
)


# ---------------------------------------------------------------------------
# Gemini response schema
# ---------------------------------------------------------------------------

class _SaaSSchema(BaseModel):
    saas_name: str
    one_line_pitch: str
    ideal_customer: str
    pricing_model: str
    why_existing_solutions_fail: str
    mvp_features: list[str]
    go_to_market: list[str]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a B2B SaaS product strategist. Based on the market research cluster \
below, design a focused, buildable SaaS product that solves the dominant pain.

### Market cluster
Cluster name       : {cluster_name}
Buyer types        : {buyer_types}
Industries         : {industries}
Mention count      : {mention_count}
Avg opportunity score: {avg_score}/10
Example problems   :
{example_problems}

### Instructions
- saas_name: Short, memorable product name (2-4 words, no generic suffixes like "Hub" or "Pro")
- one_line_pitch: One sentence value proposition focused on the buyer's outcome, not features
- ideal_customer: Specific job title + company context (e.g. "Property manager at a 50-200 unit \
  residential portfolio")
- pricing_model: Recommended pricing strategy with a rationale (e.g. "Per-unit SaaS, $X/mo/unit \
  — aligns cost with customer scale")
- why_existing_solutions_fail: 2-3 sentences on why current tools miss the mark for this buyer
- mvp_features: 4-6 concrete features the MVP must ship (action-oriented, not marketing copy)
- go_to_market: 3-5 specific GTM tactics suited to this buyer persona and pain

Return strict JSON matching the schema. No markdown fences, no extra keys.\
"""


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------

def _buyer_clarity_score(cluster: dict) -> float:
    """0–1: how specific and concentrated the buyer persona is."""
    buyer_types: list[str] = cluster.get("buyer_types") or []
    if not buyer_types:
        return 0.0

    # Penalise generic labels
    specific = [b for b in buyer_types if b.lower() not in _GENERIC_BUYERS]
    specificity = len(specific) / len(buyer_types) if buyer_types else 0

    # Penalise fragmentation: fewer distinct types = clearer buyer
    diversity_penalty = min(len(buyer_types) / 5, 1.0)
    clarity = specificity * (1 - 0.3 * diversity_penalty)
    return round(max(0.0, min(1.0, clarity)), 3)


def _pain_intensity_score(cluster: dict) -> float:
    """0–1: normalised from average_opportunity_score (1–10)."""
    raw = cluster.get("average_opportunity_score", 0)
    return round(min(max(raw / 10.0, 0.0), 1.0), 3)


def _recurrence_score(cluster: dict, max_mentions: int = 10) -> float:
    """0–1: normalised mention_count, capped at max_mentions."""
    count = cluster.get("mention_count", 0)
    return round(min(count / max_mentions, 1.0), 3)


def _rank_score(cluster: dict, max_mentions: int = 10) -> float:
    """Weighted composite rank score (0–10)."""
    pi = _pain_intensity_score(cluster)
    bc = _buyer_clarity_score(cluster)
    rc = _recurrence_score(cluster, max_mentions)
    return round((pi * 0.40 + bc * 0.35 + rc * 0.25) * 10, 2)


# ---------------------------------------------------------------------------
# Single-cluster generation
# ---------------------------------------------------------------------------

def generate_saas_idea(cluster: dict) -> dict | None:
    """Generate one SaaS opportunity dict from a cluster.

    Returns None on unrecoverable failure (after retries).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — skipping SaaS generation.")
        return None

    from google import genai
    from google.genai import types

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_SaaSSchema,
    )

    example_problems = cluster.get("example_problems") or []
    prompt = _PROMPT_TEMPLATE.format(
        cluster_name=cluster.get("cluster_name", ""),
        buyer_types=", ".join(cluster.get("buyer_types") or []) or "Unknown",
        industries=", ".join(cluster.get("industries") or []) or "Unknown",
        mention_count=cluster.get("mention_count", 0),
        avg_score=cluster.get("average_opportunity_score", 0),
        example_problems="\n".join(f"  - {p}" for p in example_problems) or "  - (none)",
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
            return {
                **data,
                # Attach cluster context for ranking and display
                "source_cluster": cluster.get("cluster_name", ""),
                "buyer_types": cluster.get("buyer_types", []),
                "industries": cluster.get("industries", []),
                "mention_count": cluster.get("mention_count", 0),
                "average_opportunity_score": cluster.get("average_opportunity_score", 0),
            }
        except Exception as exc:
            last_error = exc
            logger.warning(
                "SaaS generation attempt %d/%d failed for cluster %r: %s",
                attempt + 1, _MAX_RETRIES,
                cluster.get("cluster_name", "?"), exc,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))

    logger.error(
        "SaaS generation failed for cluster %r after %d attempts: %s",
        cluster.get("cluster_name", "?"), _MAX_RETRIES, last_error,
    )
    return None


# ---------------------------------------------------------------------------
# Batch generation + ranking
# ---------------------------------------------------------------------------

def generate_saas_ideas(clusters: list[dict]) -> list[dict]:
    """Generate SaaS ideas for all clusters concurrently, then rank them.

    Skips clusters where generation fails. Returns ranked list with
    rank_score attached to each idea.
    """
    if not clusters:
        return []

    max_mentions = max((c.get("mention_count", 0) for c in clusters), default=1)

    ideas: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(len(clusters), 10)) as executor:
        future_to_cluster = {
            executor.submit(generate_saas_idea, cluster): cluster
            for cluster in clusters
        }
        for future in as_completed(future_to_cluster):
            cluster = future_to_cluster[future]
            try:
                idea = future.result()
                if idea:
                    idea["rank_score"] = _rank_score(cluster, max_mentions)
                    ideas.append(idea)
            except Exception as exc:
                logger.warning(
                    "Concurrent SaaS generation failed for %r: %s",
                    cluster.get("cluster_name", "?"), exc,
                )

    ideas.sort(key=lambda i: i["rank_score"], reverse=True)
    return ideas
