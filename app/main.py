from dotenv import load_dotenv

load_dotenv()  # loads .env from project root if present; no-op otherwise

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.ai_extractor import extract_opportunities
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
