"""FinvizNewsSource — Strategy A: authenticated CSV news export.

See §6 of the plan. Uses the Elite export endpoint with the auth token from env.
The response is CSV; we parse it with the stdlib and map rows to NewsItem.

NOTE: the exact endpoint path and CSV column names for Finviz Elite news export
are confirmed at integration time (the user is supplying the API details). The
column mapping below is centralized in ``_COLUMN_ALIASES`` and ``_row_to_item``
so it can be adjusted in one place without touching the rest of the system.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import time
from datetime import datetime
from typing import Optional

import requests

from src.models import NewsItem
from src.sources.base import Source

logger = logging.getLogger(__name__)

# Endpoint bases (see §6, Strategy A). Overridable via env for flexibility while
# the exact API is being finalized.
NEWS_EXPORT_URL = os.environ.get(
    "FINVIZ_NEWS_EXPORT_URL", "https://elite.finviz.com/news_export.ashx"
)

# Map possible CSV header names (lowercased) to our normalized fields. Finviz has
# used slightly different headers across exports; accept the common variants.
_COLUMN_ALIASES = {
    "headline": {"title", "headline", "news"},
    "url": {"url", "link"},
    "publisher": {"source", "publisher"},
    "published_at": {"date", "datetime", "time"},
    "ticker": {"ticker", "symbol"},
    "category": {"category"},
}


class FinvizNewsSource(Source):
    name = "finviz_news"

    def __init__(
        self,
        watchlist: Optional[list[str]] = None,
        mode: str = "news_export",
        max_items_per_fetch: int = 50,
        request_timeout: float = 15.0,
        polite_sleep: float = 0.5,
    ):
        self.watchlist = watchlist or []
        self.mode = mode
        self.max_items = max_items_per_fetch
        self.request_timeout = request_timeout
        self.polite_sleep = polite_sleep

    # --- public API -------------------------------------------------------

    def fetch(self) -> list[NewsItem]:
        token = os.environ.get("FINVIZ_AUTH_TOKEN")
        if not token:
            logger.error(
                "FINVIZ_AUTH_TOKEN not set — cannot fetch Finviz news. "
                "Set it in the environment (never in config.yaml)."
            )
            return []

        if self.mode == "html_fallback":
            logger.warning("html_fallback mode is a documented Strategy B stub; "
                           "returning [] until implemented.")
            return []

        items: list[NewsItem] = []
        seen_in_batch: set[str] = set()

        if self.watchlist:
            for ticker in self.watchlist:
                rows = self._fetch_csv(token, ticker=ticker)
                items.extend(self._rows_to_items(rows, default_ticker=ticker,
                                                 seen=seen_in_batch))
                time.sleep(self.polite_sleep)  # be polite between requests
        else:
            # No watchlist: pull the general news export once.
            rows = self._fetch_csv(token, ticker=None)
            items.extend(self._rows_to_items(rows, default_ticker=None,
                                             seen=seen_in_batch))

        return items[: self.max_items] if self.max_items else items

    # --- internals --------------------------------------------------------

    def _fetch_csv(self, token: str, ticker: Optional[str]) -> list[dict]:
        params = {"auth": token, "v": "3"}
        if ticker:
            params["t"] = ticker  # per-ticker filter (adjust at integration time)
        try:
            resp = requests.get(
                NEWS_EXPORT_URL, params=params, timeout=self.request_timeout
            )
        except requests.RequestException as e:
            logger.error("Finviz network error (ticker=%s): %s", ticker, e)
            return []

        if resp.status_code == 401 or resp.status_code == 403:
            logger.error(
                "Finviz auth failure (HTTP %s) for ticker=%s — token may be "
                "expired or invalid. Distinct from a network error.",
                resp.status_code, ticker,
            )
            return []
        if resp.status_code != 200:
            logger.error("Finviz non-200 (HTTP %s) for ticker=%s",
                         resp.status_code, ticker)
            return []

        text = resp.text
        # An expired/invalid token sometimes returns an HTML login page with 200.
        if text.lstrip().lower().startswith("<!doctype") or "<html" in text[:200].lower():
            logger.error(
                "Finviz returned HTML instead of CSV for ticker=%s — likely an "
                "auth failure (token expired). Distinct from a network error.",
                ticker,
            )
            return []

        try:
            reader = csv.DictReader(io.StringIO(text))
            return list(reader)
        except csv.Error as e:
            logger.error("Finviz CSV parse error for ticker=%s: %s", ticker, e)
            return []

    def _rows_to_items(
        self, rows: list[dict], default_ticker: Optional[str], seen: set[str]
    ) -> list[NewsItem]:
        items: list[NewsItem] = []
        for row in rows:
            item = self._row_to_item(row, default_ticker)
            if item is None:
                continue
            if item.id in seen:
                continue  # de-dupe within this fetch (same story per ticker)
            seen.add(item.id)
            items.append(item)
        return items

    def _row_to_item(self, row: dict, default_ticker: Optional[str]) -> Optional[NewsItem]:
        # Normalize headers to lowercase for alias matching.
        norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}

        headline = self._pick(norm, "headline")
        url = self._pick(norm, "url")
        if not headline:
            return None

        ticker = self._pick(norm, "ticker") or default_ticker
        publisher = self._pick(norm, "publisher") or None
        published_at = self._parse_date(self._pick(norm, "published_at"))

        # id must be STABLE across polls. Prefer the canonical URL; fall back to a
        # hash of (source + headline + date) when no URL is present.
        if url:
            item_id = url
        else:
            basis = f"{self.name}|{headline}|{published_at or ''}"
            item_id = "finviz:" + hashlib.sha256(basis.encode()).hexdigest()[:16]

        return NewsItem(
            id=item_id,
            source=self.name,
            headline=headline,
            url=url or "",
            ticker=ticker,
            publisher=publisher,
            published_at=published_at,
            meta={"category": self._pick(norm, "category")} if self._pick(norm, "category") else {},
        )

    @staticmethod
    def _pick(norm: dict, field: str) -> str:
        for alias in _COLUMN_ALIASES[field]:
            if alias in norm and norm[alias]:
                return norm[alias]
        return ""

    @staticmethod
    def _parse_date(value: str) -> Optional[datetime]:
        if not value:
            return None
        # Finviz date formats vary; try a few common ones, else leave None.
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S %p",
                    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None
