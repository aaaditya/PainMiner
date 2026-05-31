"""Google Gemini Flash provider implementation."""

from __future__ import annotations

import json
import logging
import os
import time

from pydantic import BaseModel

from app.ai.provider import AIProvider

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt
_BODY_CHAR_LIMIT = 1500   # truncate long posts to keep prompts lean


# ---------------------------------------------------------------------------
# Response schema (enforced by Gemini's structured-output mode)
# ---------------------------------------------------------------------------

class _OpportunitySchema(BaseModel):
    problem: str
    buyer_type: str
    industry: str
    severity_score: int
    urgency_score: int
    why_this_is_a_problem: str


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a B2B market research analyst. Analyze the Reddit post below and \
extract the core business pain point as a structured opportunity.

### Post title
{title}

### Post body
{body}

### Instructions
- problem: One concise sentence describing the core problem.
- buyer_type: The job role most likely experiencing this pain \
(e.g. "Warehouse Manager", "HR Director", "CFO").
- industry: The industry vertical this problem belongs to \
(e.g. "Logistics", "Healthcare", "Retail").
- severity_score: Integer 1-10 — how painful/costly this problem is \
(1 = minor inconvenience, 10 = business-critical / revenue impact).
- urgency_score: Integer 1-10 — how urgently a solution is needed \
(1 = nice-to-have someday, 10 = needed immediately).
- why_this_is_a_problem: 1-2 sentences explaining why this matters as a \
business opportunity.

Return strict JSON matching the schema. No extra keys. No markdown fences.\
"""


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class GeminiProvider(AIProvider):
    def __init__(self) -> None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY environment variable must be set."
            )

        # Import here so missing package only errors when this provider is used
        from google import genai
        from google.genai import types

        model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self._client = genai.Client(api_key=api_key)
        self._model = model_name
        self._config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_OpportunitySchema,
        )

    def extract_opportunity(self, post: dict) -> dict:
        title: str = post.get("title", "")
        body: str = (post.get("body") or "")[:_BODY_CHAR_LIMIT]
        prompt = _PROMPT_TEMPLATE.format(title=title, body=body)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=self._config,
                )
                data = json.loads(response.text)
                return _validate(data)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Gemini attempt %d/%d failed: %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BASE_DELAY * (2**attempt))

        raise RuntimeError(
            f"Gemini extraction failed after {_MAX_RETRIES} attempts: {last_error}"
        ) from last_error


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def _validate(data: dict) -> dict:
    required = (
        "problem",
        "buyer_type",
        "industry",
        "severity_score",
        "urgency_score",
        "why_this_is_a_problem",
    )
    for field in required:
        if field not in data:
            raise ValueError(f"Missing field in Gemini response: {field!r}")

    data["severity_score"] = max(1, min(10, int(data["severity_score"])))
    data["urgency_score"] = max(1, min(10, int(data["urgency_score"])))
    return data
