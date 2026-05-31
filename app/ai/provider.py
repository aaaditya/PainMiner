"""Abstract AI provider interface.

New providers (OpenAI, Anthropic, OpenRouter, …) must subclass AIProvider
and implement extract_opportunity(). Business logic never imports a concrete
provider directly — it goes through app.ai_extractor, which wires the
active provider and handles fallback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AIProvider(ABC):
    @abstractmethod
    def extract_opportunity(self, post: dict) -> dict:
        """Extract a structured business opportunity from a Reddit post.

        Args:
            post: dict containing at least ``title`` and ``body``.

        Returns:
            dict with keys:
                problem              (str)
                buyer_type           (str)
                industry             (str)
                severity_score       (int, 1-10)
                urgency_score        (int, 1-10)
                why_this_is_a_problem (str)

        Raises:
            RuntimeError: if extraction fails after all retries.
        """
