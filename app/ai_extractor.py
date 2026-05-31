"""AI-powered opportunity extractor with rule-based fallback.

Wires the active AI provider (currently GeminiProvider) to the business
logic. Supports both single-post and concurrent batch extraction.

Fallback behaviour: if the provider is unavailable (missing API key) or
raises after all retries, extraction falls back transparently to the
rule-based extractor in app.extractor.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.extractor import extract_problem

logger = logging.getLogger(__name__)

_provider = None
_provider_lock = threading.Lock()


def _get_provider():
    """Lazily initialize the AI provider (thread-safe singleton)."""
    global _provider
    if _provider is not None:
        return _provider
    with _provider_lock:
        if _provider is None:
            from app.ai.gemini_provider import GeminiProvider
            _provider = GeminiProvider()
    return _provider


# ---------------------------------------------------------------------------
# Single-post extraction
# ---------------------------------------------------------------------------

def extract_opportunity(post: dict) -> dict:
    """Extract a structured opportunity from a single Reddit post.

    Falls back to rule-based extraction on EnvironmentError or any API
    failure after retries.
    """
    try:
        provider = _get_provider()
        return provider.extract_opportunity(post)
    except EnvironmentError:
        logger.warning("GEMINI_API_KEY is not set — falling back to rule-based extraction.")
        return extract_problem(post)
    except Exception as exc:
        logger.warning("AI extraction failed (%s) — falling back to rule-based extraction.", exc)
        return extract_problem(post)


# ---------------------------------------------------------------------------
# Concurrent batch extraction
# ---------------------------------------------------------------------------

def extract_opportunities(posts: list[dict], max_workers: int = 10) -> list[dict]:
    """Extract opportunities from multiple posts concurrently.

    Submits up to *max_workers* Gemini calls in parallel using a thread pool.
    Results are returned in the same order as *posts*. Individual failures
    fall back to rule-based extraction rather than propagating.

    With 10 workers and Gemini round-trips of ~4 s each, 10 posts finish
    in roughly the time of a single call instead of ~40 s sequentially.
    """
    if not posts:
        return []

    n = len(posts)
    results: list[dict | None] = [None] * n

    with ThreadPoolExecutor(max_workers=min(max_workers, n)) as executor:
        future_to_idx = {
            executor.submit(extract_opportunity, post): i
            for i, post in enumerate(posts)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                logger.warning(
                    "Concurrent extraction failed for post %d (%s) — using fallback.",
                    idx, exc,
                )
                results[idx] = extract_problem(posts[idx])

    # as_completed guarantees all futures are done; filter out any None slots
    return [r for r in results if r is not None]
