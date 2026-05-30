from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.crawler import search_reddit

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

    return {"keyword": request.keyword, "posts": posts}
