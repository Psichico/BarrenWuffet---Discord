from __future__ import annotations

import csv
import io
import logging
import os
from datetime import datetime
from typing import Optional

import requests

from src.models import NewsItem
from src.sources.base import Source

logger = logging.getLogger(__name__)


class FinvizInsiderSource(Source):
    def __init__(
        self,
        name: str = "finviz_insiders",
        pid: str = "",
        min_value: float = 1000000.0,
        request_timeout: float = 15.0,
    ):
        self.name = name
        self.pid = pid
        self.min_value = min_value
        self.request_timeout = request_timeout

    def fetch(self) -> list[NewsItem]:
        token = os.environ.get("FINVIZ_AUTH_TOKEN")
        if not token:
            logger.error(
                "FINVIZ_AUTH_TOKEN not set — cannot fetch Finviz portfolio or insiders."
            )
            return []

        if not self.pid:
            logger.error("Portfolio PID is not configured. Cannot filter insider trades.")
            return []

        # 1. Fetch watchlist from portfolio PID
        watchlist = self._fetch_watchlist(token)
        if not watchlist:
            logger.info("Watchlist is empty; no insider trades to filter.")
            return []

        # 2. Fetch insider transactions
        items = self._fetch_insider_trades(token, watchlist)
        return items

    def _fetch_watchlist(self, token: str) -> set[str]:
        url = "https://elite.finviz.com/export/portfolio"
        params = {
            "pid": self.pid,
            "auth": token,
        }
        try:
            resp = requests.get(url, params=params, timeout=self.request_timeout)
        except requests.RequestException as e:
            logger.error("Error fetching portfolio watchlist for PID %s: %s", self.pid, e)
            return set()

        if resp.status_code != 200:
            logger.error("Non-200 status code fetching portfolio watchlist: HTTP %s", resp.status_code)
            return set()

        resp.encoding = "utf-8"
        text = resp.text
        if text.lstrip().lower().startswith("<!doctype") or "<html" in text[:200].lower():
            logger.error("Portfolio export returned HTML — auth failure or invalid PID.")
            return set()

        tickers = set()
        try:
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                ticker = row.get("Ticker") or row.get("ticker")
                if ticker:
                    tickers.add(ticker.strip().upper())
        except csv.Error as e:
            logger.error("CSV parse error on portfolio export: %s", e)

        return tickers

    def _fetch_insider_trades(self, token: str, watchlist: set[str]) -> list[NewsItem]:
        url = "https://elite.finviz.com/export/insiders"
        params = {
            "tc": "7",  # fetch all types so we can filter buys and sales ourselves
            "auth": token,
        }
        try:
            resp = requests.get(url, params=params, timeout=self.request_timeout)
        except requests.RequestException as e:
            logger.error("Error fetching insider trades: %s", e)
            return []

        if resp.status_code != 200:
            logger.error("Non-200 status code fetching insider trades: HTTP %s", resp.status_code)
            return []

        resp.encoding = "utf-8"
        text = resp.text
        if text.lstrip().lower().startswith("<!doctype") or "<html" in text[:200].lower():
            logger.error("Insider trades export returned HTML — auth failure.")
            return []

        items: list[NewsItem] = []
        try:
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                # Normalize column lookups
                ticker = (row.get("Ticker") or "").strip().upper()
                if not ticker or ticker not in watchlist:
                    continue

                transaction = (row.get("Transaction") or "").strip()
                if transaction not in ("Buy", "Sale"):
                    continue

                value_str = (row.get("Value ($)") or "0").replace(",", "").strip()
                try:
                    value = float(value_str)
                except ValueError:
                    continue

                if value < self.min_value:
                    continue

                owner = (row.get("Owner") or "").strip()
                relationship = (row.get("Relationship") or "").strip()
                shares_str = (row.get("#Shares") or "0").replace(",", "").strip()
                try:
                    shares_formatted = f"{int(shares_str):,}"
                except ValueError:
                    shares_formatted = shares_str
                sec_link = (row.get("SEC Form 4 Link") or "").strip()
                sec_date_str = (row.get("SEC Form 4") or "").strip()

                published_at = self._parse_date(sec_date_str)

                # Formulate a stable unique ID
                item_id = sec_link or f"insider:{ticker}:{owner}:{sec_date_str}"

                headline = f"{ticker} | Insider {transaction} of ${int(value):,} ({shares_formatted} shares) by {owner} ({relationship})"

                items.append(
                    NewsItem(
                        id=item_id,
                        source=self.name,
                        headline=headline,
                        url=sec_link,
                        ticker=ticker,
                        publisher=owner,
                        published_at=published_at,
                        keep=True,
                        urgency="high",
                        reason=f"Significant insider transaction value (${value:,.0f} >= {self.min_value:,.0f})",
                    )
                )
        except csv.Error as e:
            logger.error("CSV parse error on insider trades: %s", e)

        return items

    @staticmethod
    def _parse_date(value: str) -> Optional[datetime]:
        if not value:
            return None
        # SEC Form 4 dates are typically "MM/DD/YYYY HH:MM"
        for fmt in ("%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None
