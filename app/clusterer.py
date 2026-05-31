"""Semantic opportunity clustering via Gemini.

Takes a flat list of scored opportunity dicts and groups them into recurring
themes using Gemini as the semantic engine. Falls back to one-cluster-per-
opportunity when the API key is absent or all retries fail.
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

class _ClusterAssignment(BaseModel):
    cluster_name: str
    member_indices: list[int]


class _ClusteringResponse(BaseModel):
    clusters: list[_ClusterAssignment]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a B2B market research analyst. Below is a numbered list of business \
problems extracted from Reddit discussions.

Group them into meaningful recurring themes. Each theme should represent a \
distinct pain-point category that buyers in a specific market face.

### Problems
{problems_json}

### Instructions
- Produce 2-8 clusters (fewer when problems are similar; more when they are \
  genuinely distinct).
- cluster_name must be a concise market theme (2-5 words), e.g. \
  "Inventory Management", "Workforce Hiring", "Compliance Burden".
- member_indices must be the 0-based indices from the input list.
- Every index from 0 to {max_index} must appear in exactly one cluster.
- Do NOT invent problems — only use the provided indices.

Return strict JSON matching the schema. No markdown fences, no extra keys.\
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cluster_opportunities(opportunities: list[dict]) -> list[dict]:
    """Cluster a list of scored opportunities into recurring themes.

    Returns a list of cluster dicts sorted by mention_count (desc) then
    average_opportunity_score (desc). Each cluster contains:
        cluster_name            (str)
        mention_count           (int)
        average_opportunity_score (float)
        buyer_types             (list[str])
        industries              (list[str])
        example_problems        (list[str], up to 3)
    """
    if not opportunities:
        return []

    if len(opportunities) == 1:
        return [_single_cluster(opportunities[0])]

    problems = [
        {"index": i, "problem": opp.get("problem", f"Problem {i}")}
        for i, opp in enumerate(opportunities)
    ]
    prompt = _PROMPT_TEMPLATE.format(
        problems_json=json.dumps(problems, indent=2),
        max_index=len(opportunities) - 1,
    )

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — using trivial fallback clustering.")
        return _fallback_clusters(opportunities)

    from google import genai
    from google.genai import types

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_ClusteringResponse,
    )

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            raw = json.loads(response.text)
            return _build_clusters(opportunities, raw.get("clusters", []))
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Gemini clustering attempt %d/%d failed: %s",
                attempt + 1, _MAX_RETRIES, exc,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))

    logger.warning(
        "Gemini clustering failed after %d attempts (%s) — using fallback.",
        _MAX_RETRIES, last_error,
    )
    return _fallback_clusters(opportunities)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_clusters(opportunities: list[dict], raw_clusters: list[dict]) -> list[dict]:
    """Assemble cluster output dicts from Gemini's grouping."""
    assigned: set[int] = set()
    clusters: list[dict] = []

    for raw in raw_clusters:
        name: str = raw.get("cluster_name") or "Unknown"
        indices: list[int] = [
            int(i) for i in raw.get("member_indices", [])
            if 0 <= int(i) < len(opportunities)
        ]
        if not indices:
            continue

        members = [opportunities[i] for i in indices]
        assigned.update(indices)

        clusters.append(_make_cluster(name, members))

    # Catch anything Gemini failed to assign
    unassigned = [i for i in range(len(opportunities)) if i not in assigned]
    if unassigned:
        members = [opportunities[i] for i in unassigned]
        clusters.append(_make_cluster("Other", members))

    return _sort_clusters(clusters)


def _make_cluster(name: str, members: list[dict]) -> dict:
    scores = [m.get("opportunity_score", 0) for m in members]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    # Preserve insertion order while deduplicating
    buyer_types = list(dict.fromkeys(
        m["buyer_type"] for m in members if m.get("buyer_type")
    ))
    industries = list(dict.fromkeys(
        m["industry"] for m in members if m.get("industry")
    ))
    example_problems = [
        m["problem"] for m in members if m.get("problem")
    ][:3]

    return {
        "cluster_name": name,
        "mention_count": len(members),
        "average_opportunity_score": avg_score,
        "buyer_types": buyer_types,
        "industries": industries,
        "example_problems": example_problems,
    }


def _sort_clusters(clusters: list[dict]) -> list[dict]:
    return sorted(
        clusters,
        key=lambda c: (-c["mention_count"], -c["average_opportunity_score"]),
    )


def _single_cluster(opp: dict) -> dict:
    return _make_cluster(opp.get("problem", "General"), [opp])


def _fallback_clusters(opportunities: list[dict]) -> list[dict]:
    """One cluster per opportunity — used when Gemini is unavailable."""
    return _sort_clusters([_single_cluster(opp) for opp in opportunities])
