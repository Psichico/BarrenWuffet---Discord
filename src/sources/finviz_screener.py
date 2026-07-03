"""FinvizScreenerSource — designed-for, built LATER (v2). See §6.1.

This is a genuinely different data source: the "event" is *a ticker entering the
screener result set*, not a headline. It emits the same NewsItem type, so the
same filter and sinks handle it with zero changes. Left as a stub so the
extension point is visible and the architecture is proven plug-and-play.
"""

from __future__ import annotations

from src.models import NewsItem
from src.sources.base import Source


class FinvizScreenerSource(Source):
    name = "finviz_screener"

    def __init__(self, screener_url: str, max_items_per_fetch: int = 50):
        self.screener_url = screener_url
        self.max_items = max_items_per_fetch

    def fetch(self) -> list[NewsItem]:
        # v2 implementation:
        #   1. GET export.ashx for the saved screener (auth token from env)
        #   2. parse CSV -> current set of tickers
        #   3. emit one NewsItem per ticker with a STABLE id so dedupe treats
        #      "ticker X matched screener Y today" as a single event, e.g.
        #      id = f"screener:{screener_id}:{ticker}:{date}"
        #      headline = f"{ticker} now matches screener '{screener_name}'"
        #      source = "finviz_screener"
        raise NotImplementedError("FinvizScreenerSource is a v2 extension point")
