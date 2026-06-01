from dotenv import load_dotenv

load_dotenv()  # loads .env from project root if present; no-op otherwise

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.ai_extractor import extract_opportunities
from app.buyer_discovery import discover_buyers_for_clusters
from app.company_discovery import deduplicate_companies, discover_companies_for_clusters, discover_score_filter
from app.decision_maker_discovery import discover_decision_makers_batch
from app.clusterer import cluster_opportunities
from app.expander import expand_keyword
from app.saas_generator import generate_saas_ideas
from app.scorer import score_opportunity
from app.sources.firecrawl_source import FirecrawlSource

app = FastAPI(title="PainMiner")

_source: FirecrawlSource | None = None


def _get_source() -> FirecrawlSource:
    global _source
    if _source is None:
        _source = FirecrawlSource()
    return _source


class KeywordRequest(BaseModel):
    keyword: str


def _collect_and_score(keyword: str) -> tuple[list[dict], list[dict], list[str]]:
    """Shared pipeline: expand → fetch (concurrent multi-term) → extract → score.

    Returns (posts, scored_opportunities, expanded_terms).
    """
    source = _get_source()
    expanded_terms = expand_keyword(keyword)
    posts = source.search_expanded(keyword, expanded_terms)
    opportunities = extract_opportunities(posts)
    scored = sorted(
        [score_opportunity(opp, post) for opp, post in zip(opportunities, posts)],
        key=lambda o: o["opportunity_score"],
        reverse=True,
    )
    return posts, scored, expanded_terms


# ---------------------------------------------------------------------------
# POST /analyze
# ---------------------------------------------------------------------------

@app.post("/analyze")
def analyze(request: KeywordRequest):
    try:
        posts, opportunities, expanded_terms = _collect_and_score(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search error: {exc}")

    return {
        "keyword": request.keyword,
        "expanded_terms": expanded_terms,
        "total_posts": len(posts),
        "opportunities": opportunities,
    }


# ---------------------------------------------------------------------------
# POST /analyze-market
# ---------------------------------------------------------------------------

@app.post("/analyze-market")
def analyze_market(request: KeywordRequest):
    """Full market analysis: expand → collect → extract → score → cluster."""
    try:
        _, scored, expanded_terms = _collect_and_score(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search error: {exc}")

    clusters = cluster_opportunities(scored)

    return {
        "market": request.keyword,
        "expanded_terms": expanded_terms,
        "clusters": clusters,
    }


# ---------------------------------------------------------------------------
# POST /generate-saas
# ---------------------------------------------------------------------------

@app.post("/generate-saas")
def generate_saas(request: KeywordRequest):
    """Full pipeline: expand → collect → extract → score → cluster → generate SaaS ideas.

    Returns clusters plus ranked SaaS opportunities, each with name, pitch,
    pricing model, MVP features, GTM strategy, and rank_score.
    """
    try:
        _, scored, expanded_terms = _collect_and_score(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search error: {exc}")

    clusters = cluster_opportunities(scored)
    saas_opportunities = generate_saas_ideas(clusters)

    return {
        "market": request.keyword,
        "expanded_terms": expanded_terms,
        "clusters": clusters,
        "saas_opportunities": saas_opportunities,
    }


# ---------------------------------------------------------------------------
# POST /test-search
# ---------------------------------------------------------------------------

@app.post("/test-search")
def test_search(request: KeywordRequest):
    """Quick URL discovery — no scraping, no AI extraction."""
    try:
        source = _get_source()
        items = source.search_urls(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search error: {exc}")

    return {
        "urls_found": [i["url"] for i in items],
        "sample_titles": [i["title"] for i in items],
    }


# ---------------------------------------------------------------------------
# POST /discover-buyers
# ---------------------------------------------------------------------------

@app.post("/discover-buyers")
def discover_buyers_endpoint(request: KeywordRequest):
    """Full pipeline: expand -> crawl -> extract -> score -> cluster -> buyer profiles.

    Reuses existing clusters and scoring. Returns buyer intelligence for every
    cluster: company types, buyer/decision-maker roles, LinkedIn search queries,
    outreach angles, and purchase motivation.
    """
    try:
        _, scored, expanded_terms = _collect_and_score(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search error: {exc}")

    clusters = cluster_opportunities(scored)
    buyer_profiles = discover_buyers_for_clusters(clusters)

    return {
        "market": request.keyword,
        "expanded_terms": expanded_terms,
        "clusters": clusters,
        "buyer_profiles": [p.model_dump() for p in buyer_profiles],
    }


# ---------------------------------------------------------------------------
# POST /discover-companies
# ---------------------------------------------------------------------------

@app.post("/discover-companies")
def discover_companies_endpoint(request: KeywordRequest):
    """Full pipeline: expand → crawl → extract → score → cluster →
    buyer profiles → company discovery.

    Returns a market summary, all clusters, buyer profiles, and a deduplicated
    list of real companies matched to each cluster's buyer persona.
    """
    try:
        posts, scored, expanded_terms = _collect_and_score(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pipeline error: {exc}")

    clusters = cluster_opportunities(scored)
    buyer_profiles = discover_buyers_for_clusters(clusters)
    raw, filtered, metrics = discover_score_filter(clusters, buyer_profiles)

    return {
        "market_summary": {
            "keyword": request.keyword,
            "expanded_terms": expanded_terms,
            **metrics,
        },
        "clusters": clusters,
        "buyer_profiles": [p.model_dump() for p in buyer_profiles],
        "companies": filtered,
    }


# ---------------------------------------------------------------------------
# POST /validate-companies
# ---------------------------------------------------------------------------

@app.post("/validate-companies")
def validate_companies_endpoint(request: KeywordRequest):
    """Full pipeline + quality scoring — returns raw vs filtered companies
    with per-company scoring details and removal reasons.
    """
    try:
        posts, scored_opps, expanded_terms = _collect_and_score(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pipeline error: {exc}")

    clusters = cluster_opportunities(scored_opps)
    buyer_profiles = discover_buyers_for_clusters(clusters)
    raw, filtered, metrics = discover_score_filter(clusters, buyer_profiles)

    precision_before = 0.0
    precision_after = 0.0
    if raw:
        valid_before = sum(1 for c in raw if c.get("company_score", 5) >= 6)
        precision_before = round(valid_before / len(raw), 3)
    if raw:
        precision_after = round(len(filtered) / len(raw), 3)

    rejected = [c for c in raw if c not in filtered]

    return {
        "market": request.keyword,
        "quality_metrics": {
            **metrics,
            "precision_before": precision_before,
            "precision_after": precision_after,
            "precision_improvement": round(precision_after - precision_before, 3),
        },
        "raw_companies": raw,
        "filtered_companies": filtered,
        "rejected_companies": [
            {
                "name": c.get("name"),
                "website": c.get("website"),
                "company_score": c.get("company_score"),
                "rejection_category": c.get("rejection_category"),
                "reasons": c.get("reasons", []),
            }
            for c in rejected
        ],
    }


# ---------------------------------------------------------------------------
# POST /discover-decision-makers
# ---------------------------------------------------------------------------

@app.post("/discover-decision-makers")
def discover_decision_makers_endpoint(request: KeywordRequest):
    """Full pipeline: expand → crawl → extract → score → cluster →
    buyer profiles → companies → decision makers.

    Processes only the top 3 highest-scoring companies per cluster to
    stay within Gemini rate limits. Returns decision-maker role profiles
    with buying-power scores and LinkedIn search queries.

    Limitations: no email discovery, no LinkedIn scraping, no personal data.
    """
    try:
        posts, scored_opps, expanded_terms = _collect_and_score(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pipeline error: {exc}")

    clusters = cluster_opportunities(scored_opps)
    buyer_profiles = discover_buyers_for_clusters(clusters)
    raw, filtered_companies, company_metrics = discover_score_filter(clusters, buyer_profiles)

    # Build lookup maps for the DM discovery
    clusters_by_name = {c["cluster_name"]: c for c in clusters}
    buyer_roles_by_cluster = {
        p.cluster_name: p.buyer_roles for p in buyer_profiles
    }

    # Cap to top 3 companies per cluster to manage Gemini quota
    top_companies: list[dict] = []
    seen_clusters: dict[str, int] = {}
    for company in filtered_companies:
        cl = company.get("cluster", "")
        if seen_clusters.get(cl, 0) < 3:
            top_companies.append(company)
            seen_clusters[cl] = seen_clusters.get(cl, 0) + 1

    dm_results = discover_decision_makers_batch(
        top_companies, clusters_by_name, buyer_roles_by_cluster
    )

    return {
        "market_summary": {
            "keyword": request.keyword,
            "expanded_terms": expanded_terms,
            "total_posts": len(posts),
            "total_clusters": len(clusters),
            **company_metrics,
            "companies_analyzed_for_dm": len(top_companies),
        },
        "clusters": clusters,
        "buyer_profiles": [p.model_dump() for p in buyer_profiles],
        "companies": filtered_companies,
        "decision_makers": [dm.model_dump() for dm in dm_results],
    }
