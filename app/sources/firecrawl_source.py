"""Firecrawl-backed data source.

Searches Reddit discussions via Firecrawl's web search endpoint using a
``site:reddit.com`` query, then returns scraped page content as normalized
post dicts ready for the extraction pipeline.

Reads ``FIRECRAWL_API_KEY`` from the environment automatically
(FirecrawlClient falls back to the env var when api_key is not supplied).
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from firecrawl.v2.client import FirecrawlClient
from firecrawl.v2.types import Document, ScrapeOptions, SearchResultWeb

from app.sources.source import DataSource

logger = logging.getLogger(__name__)

_BODY_CHAR_LIMIT = 2000
_REDDIT_TITLE_SUFFIX_RE = re.compile(
    r"\s*[:\-–|]\s*(reddit|r/\w+|posted by .*)?$", re.I
)


class FirecrawlSource(DataSource):
    def __init__(self) -> None:
        api_key = os.environ.get("FIRECRAWL_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "FIRECRAWL_API_KEY environment variable must be set."
            )
        self._client = FirecrawlClient(api_key=api_key)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def search(self, keyword: str, limit: int = 25) -> list[dict]:
        """Search Reddit for *keyword* and return scraped post content.

        Uses ``site:reddit.com`` to constrain results to Reddit, then
        scrapes each page for the main content via Firecrawl's inline
        scrape_options. Falls back to description when markdown is absent.
        """
        query = f"site:reddit.com {keyword}"
        logger.info("Firecrawl search: %r (limit=%d)", query, limit)

        results = self._client.search(
            query,
            limit=limit,
            scrape_options=ScrapeOptions(
                formats=["markdown"],
                only_main_content=True,
            ),
        )

        posts = []
        for item in results.web or []:
            post = self._normalize(item)
            if post:
                posts.append(post)

        logger.info("Firecrawl returned %d posts", len(posts))
        return posts

    def search_expanded(
        self,
        keyword: str,
        expanded_terms: list[str],
        limit_per_term: int = 8,
        max_total: int = 30,
    ) -> list[dict]:
        """Search the original keyword plus all expanded terms concurrently.

        All searches run in parallel via a ThreadPoolExecutor. Results are
        merged and deduplicated by URL. The original keyword is always
        included so its posts are never lost.

        Args:
            keyword:        The original search term.
            expanded_terms: Additional operator-pain-focused search terms.
            limit_per_term: Max results to fetch per individual search.
            max_total:      Hard cap on total unique posts returned.

        Returns:
            Deduplicated list of normalized post dicts, up to *max_total*.
        """
        all_terms = [keyword] + expanded_terms
        logger.info(
            "Expanded search: %d terms (original + %d expanded), limit_per_term=%d",
            len(all_terms), len(expanded_terms), limit_per_term,
        )

        results_by_term: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=len(all_terms)) as executor:
            future_to_term = {
                executor.submit(self.search, term, limit_per_term): term
                for term in all_terms
            }
            for future in as_completed(future_to_term):
                term = future_to_term[future]
                try:
                    results_by_term[term] = future.result()
                    logger.info("  %r → %d posts", term, len(results_by_term[term]))
                except Exception as exc:
                    logger.warning("Search failed for term %r: %s", term, exc)
                    results_by_term[term] = []

        # Merge: original keyword first, then expanded terms in order
        seen_urls: set[str] = set()
        merged: list[dict] = []

        for term in all_terms:
            for post in results_by_term.get(term, []):
                url = post.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    merged.append(post)
                if len(merged) >= max_total:
                    break
            if len(merged) >= max_total:
                break

        logger.info(
            "Expanded search complete: %d unique posts from %d terms",
            len(merged), len(all_terms),
        )
        return merged

    def search_urls(self, keyword: str, limit: int = 25) -> list[dict]:
        """Quick URL-only search — no page scraping.

        Returns a list of ``{"url": ..., "title": ...}`` dicts.
        Used by the ``/test-search`` endpoint for lightweight discovery.
        """
        query = f"site:reddit.com {keyword}"
        results = self._client.search(query, limit=limit)

        items = []
        for item in results.web or []:
            if isinstance(item, SearchResultWeb):
                items.append({"url": item.url or "", "title": item.title or ""})
            elif isinstance(item, Document):
                meta = item.metadata or {}
                items.append({
                    "url": getattr(meta, "url", "") or "",
                    "title": _clean_title(getattr(meta, "title", "") or ""),
                })
        return items

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalize(self, item) -> dict | None:
        """Convert a Firecrawl search result to a normalized post dict."""
        if isinstance(item, Document):
            meta = item.metadata or {}
            title = _clean_title(getattr(meta, "title", "") or "")
            body = (item.markdown or getattr(meta, "description", "") or "")[:_BODY_CHAR_LIMIT]
            url = getattr(meta, "url", "") or getattr(meta, "og_url", "") or ""
        elif isinstance(item, SearchResultWeb):
            title = _clean_title(item.title or "")
            body = (item.description or "")[:_BODY_CHAR_LIMIT]
            url = item.url or ""
        else:
            logger.warning("Unknown Firecrawl result type: %s", type(item))
            return None

        if not title and not body:
            return None

        return {
            "title": title,
            "body": body,
            "url": url,
            "source": "reddit",
        }


def _clean_title(title: str) -> str:
    """Strip Reddit boilerplate suffixes from page titles."""
    return _REDDIT_TITLE_SUFFIX_RE.sub("", title).strip()
