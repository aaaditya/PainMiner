"""Company Quality Scoring & Validation.

Two-stage pipeline:
  1. score_company()  — rule-based signal scoring (no API calls, instant)
  2. validate_company() — Gemini classification for borderline cases

Scoring signals
  +3  official company indicators  (About Us, Services, Request Demo, …)
  +2  company size language        (employees, units managed, clients served, …)
  +2  first-person business voice  (we manage, we serve, our customers, …)
  −4  directory/ranking signals    (top companies, best companies, list of, …)
  −4  media/publication signals    (news, magazine, journal, blog, article, …)
  −4  aggregator signals           (marketplace, vendor directory, listing site)

Threshold: company_score >= 6  AND  is_valid_company == True

Gemini is only called for borderline scores (4-7); clear positives (≥ 8) and
clear negatives (≤ 3) are decided by the rule engine alone.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_MIN_PASS_SCORE = 6
_GEMINI_BORDER_LOW = 4
_GEMINI_BORDER_HIGH = 7
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
_RATE_LIMIT_WORKERS = 3

_RETRY_AFTER_RE = re.compile(r"retry in (\d+(?:\.\d+)?)s", re.I)


# ---------------------------------------------------------------------------
# Rule-based signal tables
# ---------------------------------------------------------------------------

# Each list holds (keyword/phrase, points, label)  — only first match scores
_POSITIVE_SIGNALS: list[tuple[list[str], int, str]] = [
    (
        ["about us", "our team", "meet the team", "services", "contact us",
         "request demo", "book consultation", "request a quote",
         "property management services", "get started"],
        1, "official company page",
    ),
    (
        ["employees", "locations", "units managed", "clients served",
         "properties managed", "under management", "doors managed"],
        1, "company size indicator",
    ),
    (
        ["we manage", "we serve", "our customers", "our properties",
         "our clients", "our portfolio", "our team", "we offer",
         "we provide", "our mission"],
        1, "business voice",
    ),
]

_NEGATIVE_SIGNALS: list[tuple[list[str], int, str]] = [
    (
        ["top companies", "best companies", "top 10", "top 7", "top 5",
         "best property management", "list of companies", "compare companies",
         "directory", "companies in ", "companies near", "find a company"],
        -4, "directory/ranking",
    ),
    (
        ["news", "magazine", "journal", "blog", "article", "press release",
         "industry news", "publication", "editorial", "column"],
        -4, "media/publication",
    ),
    (
        ["marketplace", "vendor directory", "listing site", "platform for",
         "aggregator", "review site", "rated by", "compare providers"],
        -4, "aggregator",
    ),
]

# Domain-name negative keywords (checked against the bare domain)
_DOMAIN_NEGATIVE_WORDS: frozenset[str] = frozenset({
    "news", "magazine", "journal", "blog", "article", "media", "press",
    "top", "best", "directory", "listing", "compare", "review", "rank",
    "rankings", "lists", "guide", "hub", "central",
})

def _domain_has_negative_word(domain: str) -> str | None:
    """Return the first negative word found in domain parts, or None."""
    parts = re.split(r"[.\-_]", domain.lower())
    # Also check substrings of concatenated parts
    joined = domain.lower().replace(".", "").replace("-", "").replace("_", "")
    for word in _DOMAIN_NEGATIVE_WORDS:
        if word in joined:
            return word
    return None



# ---------------------------------------------------------------------------
# Rule-based scorer
# ---------------------------------------------------------------------------

def score_company(company: dict) -> dict:
    """Return rule-based quality score, validity flag, and reason list.

    Returns:
        {
          "company_score": int (0-10),
          "is_valid_company": bool,
          "reasons": list[str],
          "rejection_category": str | None
        }
    """
    name: str = company.get("name", "")
    desc: str = company.get("description", "")
    website: str = company.get("website", "")
    why: str = company.get("why_match", "")

    text = f"{name} {desc} {why}".lower()

    # Extract bare domain for domain-level checks
    domain = re.sub(r"https?://(www\.)?", "", website).split("/")[0].lower()

    score = 7  # assume valid unless negatives knock it down
    reasons: list[str] = []
    rejection_category: str | None = None

    # Domain-level negative signal (strongest signal — check first)
    neg_word = _domain_has_negative_word(domain)
    if neg_word:
        score -= 5
        reasons.append(f"-5 domain contains '{neg_word}' ({_classify_domain_word(neg_word)})")
        rejection_category = _classify_domain_word(neg_word)

    # Positive signals
    for keywords, pts, label in _POSITIVE_SIGNALS:
        for kw in keywords:
            if kw in text:
                score += pts
                reasons.append(f"+{pts} {label} ('{kw}')")
                break  # one match per signal group

    # Negative signals (text-level)
    for keywords, pts, label in _NEGATIVE_SIGNALS:
        for kw in keywords:
            if kw in text:
                score += pts  # pts is negative
                reasons.append(f"{pts} {label} ('{kw}')")
                if not rejection_category:
                    rejection_category = label
                break

    score = max(0, min(10, score))
    return {
        "company_score": score,
        "is_valid_company": score >= _MIN_PASS_SCORE,
        "reasons": reasons,
        "rejection_category": rejection_category,
    }


def _classify_domain_word(word: str) -> str:
    word = word.lower()
    if any(w in word for w in ["news", "magazine", "journal", "media", "press", "blog", "article"]):
        return "media/publication"
    if any(w in word for w in ["top", "best", "rank", "directory", "listing", "compare", "review"]):
        return "directory/ranking"
    return "non-company"


# ---------------------------------------------------------------------------
# Gemini validation (borderline cases only)
# ---------------------------------------------------------------------------

class _ValidationSchema(BaseModel):
    website_type: str   # "operating_business" | "media" | "directory" | "article" | "association"
    is_valid_company: bool
    reason: str


_VALIDATE_PROMPT = """\
You are evaluating a webpage to determine what type of site it is.

Website : {name}
URL     : {website}
Description: {description}

Classify this website as exactly one of:
  A) operating_business — An actual company offering products or services directly
  B) media              — News site, blog, magazine, trade publication, or editorial
  C) directory          — A list, directory, or comparison of multiple companies
  D) article            — A listicle, "best of" ranking, or review article
  E) association        — Industry association, trade body, or professional network

Rules:
- If the site IS the company (it sells something directly), choose operating_business
- If the site LISTS or RANKS companies, choose directory or article
- If the site REPORTS on companies, choose media

Return strict JSON. No markdown.\
"""


def validate_company(company: dict) -> dict:
    """Gemini classification for borderline companies.

    Returns the original score dict with is_valid_company updated by Gemini.
    Falls back to rule-based result on API failure.
    """
    rule_result = score_company(company)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return rule_result

    # Only call Gemini for borderline scores
    score = rule_result["company_score"]
    if score <= 3:
        return rule_result  # clear reject — skip API
    if score >= 8:
        return rule_result  # clear accept — skip API

    from google import genai
    from google.genai import types

    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_ValidationSchema,
    )
    prompt = _VALIDATE_PROMPT.format(
        name=company.get("name", ""),
        website=company.get("website", ""),
        description=(company.get("description", "") or "")[:300],
    )

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model_name, contents=prompt, config=config
            )
            data = json.loads(response.text)
            is_valid = data.get("is_valid_company", False)
            website_type = data.get("website_type", "unknown")
            reason = data.get("reason", "")

            rule_result["is_valid_company"] = is_valid
            rule_result["gemini_type"] = website_type
            rule_result["reasons"].append(
                f"Gemini: {website_type} — {reason[:120]}"
            )
            if not is_valid and not rule_result["rejection_category"]:
                rule_result["rejection_category"] = website_type
            return rule_result

        except Exception as exc:
            last_error = exc
            logger.warning(
                "Gemini validation attempt %d/%d failed: %s",
                attempt + 1, _MAX_RETRIES, exc,
            )
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                m = _RETRY_AFTER_RE.search(str(exc))
                if m:
                    delay = max(delay, float(m.group(1)))
                time.sleep(delay)

    logger.warning("Gemini validation failed (%s) — using rule-based result.", last_error)
    return rule_result


# ---------------------------------------------------------------------------
# Batch scoring + filtering
# ---------------------------------------------------------------------------

def score_and_filter(
    companies: list[dict],
    use_gemini: bool = True,
) -> tuple[list[dict], dict]:
    """Score all companies, validate borderline ones, filter, and return metrics.

    Returns:
        (filtered_companies, metrics_dict)
        Each company in filtered_companies has score fields merged in.
    """
    if not companies:
        return [], _empty_metrics()

    # Choose validation function
    validate_fn = validate_company if use_gemini else score_company

    # Score concurrently for borderline Gemini calls; safe for rule-only too
    scored: list[dict] = []
    with ThreadPoolExecutor(max_workers=_RATE_LIMIT_WORKERS) as executor:
        future_to_idx = {
            executor.submit(validate_fn, c): i for i, c in enumerate(companies)
        }
        results: list[dict | None] = [None] * len(companies)
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                logger.warning("Scoring failed for company %d: %s", idx, exc)
                results[idx] = {"company_score": 5, "is_valid_company": True,
                                "reasons": [], "rejection_category": None}

    # Merge scores into company dicts
    for company, result in zip(companies, results):
        if result:
            scored.append({**company, **result})
        else:
            scored.append(company)

    # Filter
    passed = [c for c in scored if c.get("is_valid_company", True)
              and c.get("company_score", 5) >= _MIN_PASS_SCORE]
    rejected = [c for c in scored if c not in passed]

    passed.sort(key=lambda c: c.get("company_score", 0), reverse=True)

    # Metrics
    cat_counts: dict[str, int] = {}
    for c in rejected:
        cat = c.get("rejection_category") or "other"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    metrics = {
        "companies_found": len(companies),
        "filtered_out": len(rejected),
        "final_companies": len(passed),
        "media_removed": sum(v for k, v in cat_counts.items() if "media" in k.lower()),
        "directories_removed": sum(v for k, v in cat_counts.items()
                                    if "director" in k.lower() or "ranking" in k.lower()),
        "rejection_breakdown": cat_counts,
    }

    return passed, metrics


def _empty_metrics() -> dict:
    return {
        "companies_found": 0, "filtered_out": 0, "final_companies": 0,
        "media_removed": 0, "directories_removed": 0, "rejection_breakdown": {},
    }
