"""
The Provider interface + the normalized data shapes every provider returns.

WHY NORMALIZED SHAPES: the ingest/upsert layer must not care whether data came
from Alpha Vantage, Benzinga, or Reddit. Each provider is responsible for
translating its own raw payload into these neutral dataclasses. The upsert layer
then writes them the same way regardless of source. This is what makes the
system pluggable (PLAN.md §13).

Every dataclass also carries `raw` — the untouched original payload — which is
stored in a JSONB column so no original information is ever lost, even if we
later drop or rename a structured field.

Money is represented with Decimal (never float) to match the NUMERIC columns and
avoid floating-point drift.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional


# --- Normalized records ----------------------------------------------------

@dataclass
class PriceBar:
    """One daily OHLCV bar. Maps 1:1 to a price_bars row."""
    ts: date
    open: Optional[Decimal]
    high: Optional[Decimal]
    low: Optional[Decimal]
    close: Optional[Decimal]
    adj_close: Optional[Decimal]
    volume: Optional[int]
    split_coef: Decimal
    dividend: Decimal
    raw: Dict[str, Any]


@dataclass
class NewsTickerLink:
    """A single (article -> ticker) association with per-ticker scores."""
    symbol: str                        # RAW symbol as the provider reports it
    relevance: Optional[Decimal]
    sentiment: Optional[Decimal]


@dataclass
class NewsItem:
    """One news article + its ticker associations. Maps to news (+ news_tickers)."""
    external_id: str                   # stable id from the provider (or URL/hash)
    headline: str
    summary: Optional[str]
    content: Optional[str]
    url: Optional[str]
    author: Optional[str]
    published_at: Optional[datetime]
    sentiment_score: Optional[Decimal]
    sentiment_label: Optional[str]
    tickers: List[NewsTickerLink]
    raw: Dict[str, Any]


@dataclass
class FundamentalStatement:
    """One financial statement for one fiscal period. Maps to a fundamentals row."""
    statement_type: str                # 'income'|'balance'|'cashflow'|'earnings'
    fiscal_date: date
    period: str                        # 'annual'|'quarterly'
    currency: Optional[str]
    data: Dict[str, Any]
    raw: Dict[str, Any]


@dataclass
class Overview:
    """Company profile. Maps to a company_overview row (+ enriches tickers)."""
    name: Optional[str]
    exchange: Optional[str]
    asset_type: Optional[str]
    data: Dict[str, Any]
    raw: Dict[str, Any]


# --- The interface ---------------------------------------------------------

class Provider(abc.ABC):
    """
    Base class for a data provider.

    A provider does exactly two things:
      * knows its `name` (must match a row in the `sources` table), and
      * fetches each data type, returning the normalized shapes above.

    A provider that cannot supply a given data type may raise
    NotImplementedError; the orchestrator treats that as "this source has no
    data of that type" and moves on.
    """

    #: Must equal the `sources.name` value for this provider.
    name: str

    @abc.abstractmethod
    def fetch_overview(self, symbol: str) -> Optional[Overview]:
        """Company profile for `symbol` (provider's own symbol form)."""

    @abc.abstractmethod
    def fetch_prices(self, symbol: str, full: bool) -> List[PriceBar]:
        """
        Daily bars for `symbol`.
        `full=True`  -> entire available history (used for first pull / re-adjust).
        `full=False` -> a recent window sufficient for an incremental delta.
        """

    @abc.abstractmethod
    def fetch_news(
        self,
        symbol: str,
        time_from: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[NewsItem]:
        """
        News for `symbol`. If `time_from` is given, only items at/after it
        (used for incremental delta fetches)."""

    @abc.abstractmethod
    def fetch_fundamentals(self, symbol: str) -> List[FundamentalStatement]:
        """All financial statements (income/balance/cashflow/earnings, annual+quarterly)."""
