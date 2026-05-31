"""Abstract data source interface.

New sources (Hacker News, Twitter/X, Product Hunt, …) must subclass
DataSource and implement search(). Business logic never imports a concrete
source directly — it goes through app.main, which wires the active source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class DataSource(ABC):
    @abstractmethod
    def search(self, keyword: str, limit: int = 25) -> list[dict]:
        """Search for discussions matching *keyword*.

        Returns a list of normalized post dicts, each containing:
            title   (str)  — post/thread title
            body    (str)  — post text content
            url     (str)  — canonical URL
            source  (str)  — platform identifier, e.g. "reddit"
        """
