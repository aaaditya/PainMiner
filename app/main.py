from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.ai_extractor import extract_opportunity
from app.crawler import search_reddit
from app.scorer import score_opportunity

app = FastAPI(title="PainMiner")


class AnalyzeRequest(BaseModel):
    keyword: str


@app.post("/analyze")
def analyze(request: AnalyzeRequest):
    try:
        posts = search_reddit(request.keyword)
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    opportunities = sorted(
        [score_opportunity(extract_opportunity(post), post) for post in posts],
        key=lambda o: o["opportunity_score"],
        reverse=True,
    )

    return {
        "keyword": request.keyword,
        "total_posts": len(posts),
        "opportunities": opportunities,
    }
