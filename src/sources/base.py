"""The Source interface every source implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import NewsItem


class Source(ABC):
    name: str  # unique short id, e.g. "finviz_news"

    @abstractmethod
    def fetch(self) -> list[NewsItem]:
        """Return the current batch of items from this source.

        Must be side-effect free (no dedupe, no delivery here).
        Must NOT raise on transient network errors — log and return [].
        """
        ...
