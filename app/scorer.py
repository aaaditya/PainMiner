"""Opportunity scoring: combines extraction signals with Reddit engagement metrics."""

from __future__ import annotations

# Bonus thresholds
_POST_SCORE_THRESHOLD = 50
_COMMENTS_THRESHOLD = 10

# Weights applied to the two base signals (must sum to 1.0)
_SEVERITY_WEIGHT = 0.5
_URGENCY_WEIGHT = 0.5


def score_opportunity(opportunity: dict, post: dict) -> dict:
    """Add *opportunity_score* (1-10) to an extracted opportunity dict.

    Formula:
        base  = severity_score * 0.5 + urgency_score * 0.5   (range 1-10)
        bonus = 1 if post['score'] > 50 else 0
              + 1 if post['num_comments'] > 10 else 0         (range 0-2)
        raw   = base + bonus                                  (range 1-12)
        final = min(10, round(raw))                           (range 1-10)

    Returns a new dict that is the opportunity with opportunity_score added.
    """
    severity: float = opportunity.get("severity_score", 1)
    urgency: float = opportunity.get("urgency_score", 1)

    base = severity * _SEVERITY_WEIGHT + urgency * _URGENCY_WEIGHT

    bonus = 0
    if post.get("score", 0) > _POST_SCORE_THRESHOLD:
        bonus += 1
    if post.get("num_comments", 0) > _COMMENTS_THRESHOLD:
        bonus += 1

    raw = base + bonus
    final = min(10, round(raw))

    return {**opportunity, "opportunity_score": final}
