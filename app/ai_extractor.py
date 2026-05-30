"""AI-powered opportunity extractor with rule-based fallback.

Wires the active AI provider (currently GeminiProvider) to the business
logic. If the provider is unavailable (missing API key) or returns an error
after all retries, extraction falls back transparently to the rule-based
extractor in app.extractor.
"""

from __future__ import annotations

import logging

from app.extractor import extract_problem

logger = logging.getLogger(__name__)

_provider = None


def _get_provider():
    """Lazily initialize the AI provider.

    Returns the provider on success, or raises if it cannot be created.
    A new attempt is made on every call so that setting the env var at
    runtime (e.g. in tests) is picked up without restarting the server.
    """
    global _provider
    if _provider is not None:
        return _provider

    from app.ai.gemini_provider import GeminiProvider

    _provider = GeminiProvider()
    return _provider


def extract_opportunity(post: dict) -> dict:
    """Extract a structured opportunity from a Reddit post.

    Tries the AI provider first; falls back to rule-based extraction if:
    - GEMINI_API_KEY is not set (EnvironmentError)
    - The provider raises any error after exhausting retries

    Always returns a dict with: problem, buyer_type, industry,
    severity_score, urgency_score. The AI path additionally includes
    why_this_is_a_problem.
    """
    try:
        provider = _get_provider()
        return provider.extract_opportunity(post)
    except EnvironmentError:
        logger.warning(
            "GEMINI_API_KEY is not set — falling back to rule-based extraction."
        )
        return extract_problem(post)
    except Exception as exc:
        logger.warning(
            "AI extraction failed (%s) — falling back to rule-based extraction.", exc
        )
        return extract_problem(post)
