import os

import praw
from praw.exceptions import PRAWException


def _get_reddit_client() -> praw.Reddit:
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT", "painminer/1.0")

    if not client_id or not client_secret:
        raise EnvironmentError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET environment variables must be set."
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )


def search_reddit(keyword: str, limit: int = 25) -> list[dict]:
    """Search Reddit for posts matching *keyword* and return the top results.

    Each result contains: title, body, subreddit, score, num_comments, url.
    Raises RuntimeError on Reddit API failures.
    """
    try:
        reddit = _get_reddit_client()
        results = []

        for submission in reddit.subreddit("all").search(keyword, limit=limit):
            results.append(
                {
                    "title": submission.title,
                    "body": submission.selftext or "",
                    "subreddit": submission.subreddit.display_name,
                    "score": submission.score,
                    "num_comments": submission.num_comments,
                    "url": submission.url,
                }
            )

        return results

    except EnvironmentError:
        raise
    except PRAWException as exc:
        raise RuntimeError(f"Reddit API error: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected error while searching Reddit: {exc}") from exc
