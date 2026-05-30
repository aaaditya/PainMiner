# PainMiner

Analyze Reddit discussions to surface posts that reveal user pain points and rank them as structured business opportunities.

## Requirements

- Python 3.10+
- A Reddit API application — see [Reddit setup](#reddit-setup)
- A Google AI Studio API key — see [Gemini setup](#gemini-setup)

## Reddit setup

1. Log in to [reddit.com](https://www.reddit.com) and go to **Preferences → Apps → Create another app**.
2. Choose **script** as the application type.
3. Fill in a name and any redirect URI (e.g. `http://localhost:8080`).
4. After creation you will see:
   - **client id** — the short string directly under the app name
   - **client secret** — labelled "secret"

## Gemini setup

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey) and click **Create API key**.
2. Copy the key — it starts with `AIza…`.

> The app uses `gemini-2.5-flash` by default. Override with the `GEMINI_MODEL` env var (e.g. `gemini-2.0-flash`).

## Environment variables

Create a `.env` file in the project root (already git-ignored):

```
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=painminer/1.0

GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash   # optional, this is the default
```

`REDDIT_USER_AGENT` and `GEMINI_MODEL` are optional.

> **Fallback behaviour** — if `GEMINI_API_KEY` is not set, or if Gemini returns an error after 3 retries, extraction falls back automatically to the built-in rule-based extractor. The API response format is identical in both cases.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the server

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

## API

### `POST /analyze`

Accepts a keyword and returns up to 25 Reddit posts as ranked business opportunities.

**Request body**

```json
{
  "keyword": "warehouse"
}
```

**Response**

```json
{
  "keyword": "warehouse",
  "total_posts": 25,
  "opportunities": [
    {
      "problem": "Manual inventory tracking wastes hours each week",
      "buyer_type": "Warehouse Manager",
      "industry": "Logistics",
      "severity_score": 8,
      "urgency_score": 7,
      "why_this_is_a_problem": "Teams relying on spreadsheets face frequent errors and delays. A real-time tracking solution could save significant labour costs.",
      "opportunity_score": 10
    }
  ]
}
```

Opportunities are sorted by `opportunity_score` (highest first).

**Error responses**

| Status | Meaning |
|--------|---------|
| `500`  | Reddit credentials not configured |
| `502`  | Reddit API returned an error |

### Interactive docs

Once the server is running, open `http://localhost:8000/docs` to explore the API via Swagger UI.

## Architecture

```
app/
├── main.py            # FastAPI app + /analyze endpoint
├── crawler.py         # Reddit search via PRAW
├── ai_extractor.py    # AI provider wiring + rule-based fallback
├── extractor.py       # Rule-based extractor (fallback)
├── scorer.py          # Opportunity scoring formula
└── ai/
    ├── provider.py        # Abstract AIProvider base class
    └── gemini_provider.py # Google Gemini Flash implementation
```

To plug in a different AI provider (OpenAI, Anthropic, OpenRouter…), subclass `AIProvider`, implement `extract_opportunity(post)`, and swap the import in `ai_extractor.py`.
