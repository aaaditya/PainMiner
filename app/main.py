from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.crawler import search_reddit
from app.extractor import extract_problem

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

    opportunities = [extract_problem(post) for post in posts]
    return {"keyword": request.keyword, "opportunities": opportunities}
