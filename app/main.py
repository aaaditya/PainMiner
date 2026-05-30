from fastapi import FastAPI
from pydantic import BaseModel

from app.crawler import search_reddit

app = FastAPI(title="PainMiner")


class AnalyzeRequest(BaseModel):
    keyword: str


@app.post("/analyze")
def analyze(request: AnalyzeRequest):
    posts = search_reddit(request.keyword)
    return {"keyword": request.keyword, "posts": posts}
