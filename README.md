# PainMiner

Analyze Reddit discussions to surface posts that reveal user pain points.

## Requirements

- Python 3.10+
- A Reddit API application (free) — see setup below

## Reddit API setup

1. Log in to [reddit.com](https://www.reddit.com) and go to **Preferences → Apps → Create another app**.
2. Choose **script** as the application type.
3. Fill in a name and redirect URI (e.g. `http://localhost:8080`) — the values do not matter for script-type apps.
4. After creation you will see:
   - **client id** — the short string under the app name
   - **client secret** — labelled "secret"

## Environment variables

Create a `.env` file in the project root (it is git-ignored):

```
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=painminer/1.0
```

`REDDIT_USER_AGENT` is optional and defaults to `painminer/1.0`.

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

Accepts a keyword and returns up to 25 matching Reddit posts.

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
  "posts": [
    {
      "title": "Inventory management is painful",
      "body": "We still use Excel spreadsheets for everything",
      "subreddit": "supplychain",
      "score": 142,
      "num_comments": 37,
      "url": "https://www.reddit.com/r/supplychain/comments/..."
    }
  ]
}
```

**Error responses**

| Status | Meaning |
|--------|---------|
| `500`  | Reddit credentials not configured |
| `502`  | Reddit API returned an error |

### Interactive docs

Once the server is running, open `http://localhost:8000/docs` to explore the API via Swagger UI.
