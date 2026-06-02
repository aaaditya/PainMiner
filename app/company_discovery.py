"""Company Discovery Engine.

For each market cluster + buyer profile, searches the open web using Firecrawl
to find real companies that match the buyer personas. No Gemini calls needed —
all intelligence comes from Firecrawl search metadata and rule-based matching.

Pipeline per cluster:
  1. Generate 4-5 targeted search queries from company_types + buyer_roles
  2. Search the open web via Firecrawl (no site: restriction)
  3. Filter out non-company pages (social media, directories, news, etc.)
  4. Deduplicate by root domain — one entry per company
  5. Cap at *limit* companies (default 10) per cluster

All clusters are processed concurrently via ThreadPoolExecutor.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from firecrawl.v2.client import FirecrawlClient
from firecrawl.v2.types import SearchResultWeb

from app.company_scorer import score_and_filter
from app.models.buyer import BuyerProfile
from app.models.company import ClusterCompanies, Company

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain filter
# ---------------------------------------------------------------------------

_EXCLUDED_DOMAINS: frozenset[str] = frozenset(
    {
        # Social / community
        "reddit.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
        "tiktok.com", "youtube.com", "pinterest.com",
        # Professional networks (people, not companies)
        "linkedin.com",
        # Reference / encyclopedic
        "wikipedia.org", "wikihow.com",
        # Review / directory
        "yelp.com", "glassdoor.com", "trustpilot.com", "g2.com",
        "capterra.com", "getapp.com", "softwareadvice.com", "producthunt.com",
        # Job boards
        "indeed.com", "ziprecruiter.com", "monster.com", "simplyhired.com",
        # Business intelligence
        "crunchbase.com", "pitchbook.com", "zoominfo.com", "apollo.io",
        # News / media
        "forbes.com", "bloomberg.com", "businessinsider.com", "techcrunch.com",
        "inc.com", "entrepreneur.com", "wsj.com", "nytimes.com",
        # Developer platforms
        "github.com", "stackoverflow.com", "medium.com", "substack.com",
        "dev.to",
        # Rental / listing aggregators
        "apartments.com", "hotpads.com", "zillow.com", "trulia.com",
        "realtor.com", "rent.com", "rentals.com", "apartmentlist.com",
        # Community platforms
        "meetup.com", "eventbrite.com",
        # Aggregators
        "clutch.co", "expertise.com", "bark.com", "thumbtack.com",
    }
)

# Title cleanup: strip common boilerplate suffixes
_TITLE_CLEAN_RE = re.compile(
    r"\s*[\|\-–:]\s*(home|about|welcome|official\s+site|"
    r"homepage|property\s+management\s+software.*)?$",
    re.I,
)

# Employee range patterns in text
_EMP_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(\d{1,3}[,\s]?\d{3})\+?\s*employee", re.I), "1000+"),
    (re.compile(r"\b[5-9]\d{2}\s*[-–to]+\s*\d{3,4}\s*employee", re.I), "500-999"),
    (re.compile(r"\b[2-4]\d{2}\s*[-–to]+\s*\d{3}\s*employee", re.I), "200-499"),
    (re.compile(r"\b\d{1,2}\d\s*[-–to]+\s*\d{3}\s*employee", re.I), "50-199"),
    (re.compile(r"\b\d{1,2}\s*[-–to]+\s*\d{2}\s*employee", re.I), "10-49"),
]

# Location patterns
_LOCATION_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)*),\s*([A-Z]{2}|[A-Z][a-z]+)\b"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client() -> FirecrawlClient:
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise EnvironmentError("FIRECRAWL_API_KEY environment variable must be set.")
    return FirecrawlClient(api_key=api_key)


def _extract_domain(url: str) -> str:
    """Return bare root domain without www., e.g. 'appfolio.com'."""
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _is_company_site(url: str) -> bool:
    """Return True if the URL looks like an actual company website."""
    domain = _extract_domain(url)
    if not domain:
        return False
    root = domain.split(".")[-2] + "." + domain.split(".")[-1] if domain.count(".") >= 1 else domain
    return root not in _EXCLUDED_DOMAINS


def _clean_name(title: str) -> str:
    return _TITLE_CLEAN_RE.sub("", title).strip()


def _infer_employee_range(text: str) -> str:
    for pattern, label in _EMP_PATTERNS:
        if pattern.search(text):
            return label
    return ""


def _infer_location(text: str) -> str:
    m = _LOCATION_RE.search(text)
    return f"{m.group(1)}, {m.group(2)}" if m else ""


def _why_match(cluster_name: str, outreach_angles: list[str]) -> str:
    """Construct a brief match rationale from the cluster and outreach angles."""
    if outreach_angles:
        return f"Targets {cluster_name} buyers. Key value: {outreach_angles[0]}"
    return f"Matches the {cluster_name} buyer profile."


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

def _generate_queries(
    cluster_name: str,
    company_types: list[str],
    buyer_roles: list[str],
) -> list[str]:
    """Produce 4-5 search queries optimised for finding company websites."""
    queries: list[str] = []

    # Company-type queries are the most targeted
    for ct in company_types[:3]:
        queries.append(ct)

    # Role-based query surfaces companies by the buyer's job context
    if buyer_roles:
        queries.append(f"{buyer_roles[0]} companies")

    # Cluster-level catch-all
    queries.append(f"{cluster_name} companies")

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        if q.lower() not in seen:
            seen.add(q.lower())
            unique.append(q)

    return unique[:5]


# ---------------------------------------------------------------------------
# Single-cluster discovery
# ---------------------------------------------------------------------------

def discover_companies(
    cluster: dict,
    buyer_profile: BuyerProfile | None = None,
    limit: int = 10,
) -> ClusterCompanies:
    """Discover real companies for one cluster.

    Uses Firecrawl web search (no site: restriction) to find company pages,
    then normalises the results into Company objects.
    """
    cluster_name: str = cluster.get("cluster_name", "")

    company_types: list[str] = (
        buyer_profile.company_types if buyer_profile else cluster.get("company_types", [])
    )
    buyer_roles: list[str] = (
        buyer_profile.buyer_roles if buyer_profile else cluster.get("buyer_types", [])
    )
    outreach_angles: list[str] = (
        buyer_profile.outreach_angles if buyer_profile else []
    )

    queries = _generate_queries(cluster_name, company_types, buyer_roles)
    logger.info("Company discovery for %r — queries: %s", cluster_name, queries)

    try:
        client = _get_client()
    except EnvironmentError as exc:
        logger.error("Cannot discover companies: %s", exc)
        return ClusterCompanies(cluster=cluster_name)

    seen_domains: set[str] = set()
    companies: list[Company] = []

    for query in queries:
        if len(companies) >= limit:
            break
        try:
            results = client.search(query, limit=8)
        except Exception as exc:
            logger.warning("Firecrawl search failed for %r: %s", query, exc)
            continue

        for item in (results.web or []):
            if len(companies) >= limit:
                break

            url: str = getattr(item, "url", "") or ""
            if not url or not _is_company_site(url):
                continue

            domain = _extract_domain(url)
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)

            title: str = getattr(item, "title", "") or ""
            description: str = getattr(item, "description", "") or ""
            combined = f"{title} {description}"

            companies.append(
                Company(
                    name=_clean_name(title),
                    website=f"https://{domain}",
                    location=_infer_location(combined),
                    description=description[:300],
                    employee_range=_infer_employee_range(combined),
                    why_match=_why_match(cluster_name, outreach_angles),
                )
            )

    logger.info(
        "Discovered %d companies for %r from %d queries",
        len(companies), cluster_name, len(queries),
    )
    return ClusterCompanies(cluster=cluster_name, companies=companies)


# ---------------------------------------------------------------------------
# Batch (concurrent across clusters)
# ---------------------------------------------------------------------------

def discover_companies_for_clusters(
    clusters: list[dict],
    buyer_profiles: list[BuyerProfile],
    limit_per_cluster: int = 10,
) -> list[ClusterCompanies]:
    """Discover companies for all clusters concurrently.

    Pairs each cluster with its corresponding buyer_profile (by index).
    Returns results in the same order as *clusters*.
    """
    if not clusters:
        return []

    # Pad buyer_profiles if fewer than clusters
    profiles: list[BuyerProfile | None] = list(buyer_profiles) + [None] * max(
        0, len(clusters) - len(buyer_profiles)
    )

    results: list[ClusterCompanies | None] = [None] * len(clusters)

    with ThreadPoolExecutor(max_workers=min(len(clusters), 5)) as executor:
        future_to_idx = {
            executor.submit(
                discover_companies, cluster, profiles[i], limit_per_cluster
            ): i
            for i, cluster in enumerate(clusters)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                logger.warning(
                    "Company discovery failed for cluster %d: %s", idx, exc
                )
                results[idx] = ClusterCompanies(
                    cluster=clusters[idx].get("cluster_name", "")
                )

    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Global deduplication
# ---------------------------------------------------------------------------

def deduplicate_companies(
    cluster_results: list[ClusterCompanies],
) -> list[dict]:
    """Flatten all cluster results and deduplicate by website domain.

    Returns a flat list of company dicts, each annotated with its source
    cluster. First occurrence of a domain wins.
    """
    seen_domains: set[str] = set()
    flat: list[dict] = []

    for cr in cluster_results:
        for company in cr.companies:
            domain = _extract_domain(company.website)
            if domain and domain in seen_domains:
                continue
            if domain:
                seen_domains.add(domain)
            flat.append({"cluster": cr.cluster, **company.model_dump()})

    return flat


# ---------------------------------------------------------------------------
# Scored batch discovery
# ---------------------------------------------------------------------------

def discover_score_filter(
    clusters: list[dict],
    buyer_profiles: list[BuyerProfile],
    limit_per_cluster: int = 10,
    use_gemini: bool = True,
) -> tuple[list[dict], list[dict], dict]:
    """Full company pipeline: discover → score → filter.

    Returns:
        (raw_companies, filtered_companies, quality_metrics)
        Both company lists are flat dicts annotated with source cluster.
    """
    cluster_results = discover_companies_for_clusters(
        clusters, buyer_profiles, limit_per_cluster
    )
    raw = deduplicate_companies(cluster_results)
    # Score all companies; scored_raw has score fields merged in
    filtered, metrics = score_and_filter(raw, use_gemini=use_gemini)
    # Rebuild scored_raw by merging score fields from filtered + rejected
    scored_lookup = {c.get("website", i): c for i, c in enumerate(filtered)}
    scored_raw = []
    for c in raw:
        key = c.get("website", "")
        # Find matching scored version (filtered or rejected)
        scored_raw.append(c)  # will be enriched by score_and_filter below
    # Re-run scoring on raw to get scores without filtering
    from app.company_scorer import score_company
    scored_raw = [{**c, **score_company(c)} for c in raw]
    return scored_raw, filtered, metrics
