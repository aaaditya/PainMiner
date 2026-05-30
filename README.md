# PainMiner

Analyze Reddit discussions to surface posts that reveal user pain points.

## Requirements

- Python 3.10+

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

Accepts a keyword and returns relevant Reddit posts.

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
      "body": "We still use Excel spreadsheets"
    },
    {
      "title": "Warehouse tracking takes hours",
      "body": "Everything is manual"
    }
  ]
}
```

### Interactive docs

Once the server is running, open `http://localhost:8000/docs` to explore the API via Swagger UI.
