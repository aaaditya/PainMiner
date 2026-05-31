from dotenv import load_dotenv

load_dotenv()  # loads .env from project root if present; no-op otherwise

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.ai_extractor import extract_opportunity
from app.clusterer import cluster_opportunities
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


def _collect_and_score(keyword: str) -> tuple[list[dict], list[dict]]:
    """Shared logic: fetch posts → extract → score. Returns (posts, scored_opps)."""
    source = _get_source()
    posts = source.search(keyword)
    scored = sorted(
        [score_opportunity(extract_opportunity(post), post) for post in posts],
        key=lambda o: o["opportunity_score"],
        reverse=True,
    )
    return posts, scored


# ---------------------------------------------------------------------------
# POST /analyze
# ---------------------------------------------------------------------------

@app.post("/analyze")
def analyze(request: KeywordRequest):
    try:
        posts, opportunities = _collect_and_score(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search error: {exc}")

    return {
        "keyword": request.keyword,
        "total_posts": len(posts),
        "opportunities": opportunities,
    }


# ---------------------------------------------------------------------------
# POST /analyze-market
# ---------------------------------------------------------------------------

@app.post("/analyze-market")
def analyze_market(request: KeywordRequest):
    """Full market analysis: collect → extract → score → cluster into themes."""
    try:
        _, scored = _collect_and_score(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Search error: {exc}")

    clusters = cluster_opportunities(scored)

    return {
        "market": request.keyword,
        "clusters": clusters,
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
