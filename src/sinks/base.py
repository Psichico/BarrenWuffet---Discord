"""The Sink interface every sink implements (delivery + routing predicate)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import NewsItem


class Sink(ABC):
    name: str  # e.g. "discord_news", "discord_urgent"

    def accepts(self, item: NewsItem) -> bool:
        """Routing predicate: should THIS sink deliver THIS item?

        Default: accept everything kept. Override to route by urgency, ticker,
        source, etc. The orchestrator calls accepts() before deliver(), so one
        kept item can go to zero, one, or many sinks. This is how the #urgent
        channel gets only high-urgency items WITHOUT any new source or filter.
        """
        return True

    @abstractmethod
    def deliver(self, item: NewsItem) -> bool:
        """Deliver one kept item. Return True on success.

        Must NOT raise on transient errors — log and return False.
        """
        ...
