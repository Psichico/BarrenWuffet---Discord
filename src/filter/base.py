"""The Filter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import NewsItem


class Filter(ABC):
    @abstractmethod
    def process(self, items: list[NewsItem]) -> list[NewsItem]:
        """Set .keep, .reason, .urgency, and optionally .rewritten on each item.

        Return the same list (mutated). Batches internally as needed.
        """
        ...
