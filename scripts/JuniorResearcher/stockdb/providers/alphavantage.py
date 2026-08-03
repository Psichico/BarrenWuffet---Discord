"""
Alpha Vantage provider implementation.

Responsibilities:
  * Make rate-limited, retried HTTP calls to the AV REST API.
  * Detect AV's "soft" errors (it returns HTTP 200 with a Note/Information/Error
    Message field) and raise typed exceptions instead of storing garbage.
  * Translate AV's raw JSON into the normalized dataclasses from base.py.

Endpoints used (see PLAN.md §6):
  OVERVIEW, TIME_SERIES_DAILY_ADJUSTED, NEWS_SENTIMENT,
  INCOME_STATEMENT, BALANCE_SHEET, CASH_FLOW, EARNINGS.

GUARDRAILS:
  * Every request goes through the shared RateLimiter.
  * tenacity retries transient failures (network errors, 5xx, rate-limit notes)
    with exponential backoff + jitter.
  * Numeric parsing is defensive: AV emits the string "None"/"-" for missing
    values; helpers convert those to Python None rather than crashing.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ..errors import ProviderResponseError, RateLimitError
from ..ratelimit import RateLimiter
from .base import (
    FundamentalStatement,
    NewsItem,
    NewsTickerLink,
    Overview,
    PriceBar,
    Provider,
)

log = logging.getLogger(__name__)

_BASE_URL = "https://www.alphavantage.co/query"
_HTTP_TIMEOUT = (10, 30)  # (connect, read) seconds


# --- Defensive parsing helpers ---------------------------------------------

def _dec(value: Any) -> Optional[Decimal]:
    """Parse AV's stringy numbers to Decimal, or None if missing/unparseable.

    AV represents missing values as "None", "-", or "" — all become None."""
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() == "none" or s == "-":
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    """Parse to int via Decimal (handles "1234.0"), or None."""
    d = _dec(value)
    return int(d) if d is not None else None


def _iso_date(value: Any) -> Optional[date]:
    """Parse 'YYYY-MM-DD' to date, or None."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _av_time(value: Any) -> Optional[datetime]:
    """Parse AV news timestamp 'YYYYMMDDTHHMMSS' to an aware UTC datetime."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class AlphaVantageProvider(Provider):
    """Concrete Provider backed by the Alpha Vantage API."""

    name = "alphavantage"

    def __init__(self, api_key: str, rate_limiter: RateLimiter, news_limit: int = 1000) -> None:
        self._api_key = api_key
        self._rl = rate_limiter
        self._news_limit = news_limit
        self._session = requests.Session()

    # --- low-level request with rate limiting + retry + soft-error handling --

    @retry(
        retry=retry_if_exception_type((requests.RequestException, RateLimitError)),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _request(self, params: Dict[str, str]) -> Dict[str, Any]:
        """
        Perform one AV API call and return parsed JSON.

        Raises:
          RateLimitError        - AV signalled throttling (retried by @retry).
          ProviderResponseError - AV returned an error body or unparseable JSON.
        """
        query = dict(params)
        query["apikey"] = self._api_key

        # Proactive throttle so we stay within the per-minute budget.
        self._rl.acquire()

        resp = self._session.get(_BASE_URL, params=query, timeout=_HTTP_TIMEOUT)
        # 5xx -> RequestException via raise_for_status -> retried.
        resp.raise_for_status()

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderResponseError("Alpha Vantage returned non-JSON body") from exc

        # AV soft errors: HTTP 200 but a message field in the body.
        # NEVER store these as data.
        if isinstance(payload, dict):
            if "Note" in payload or "Information" in payload:
                # Both are used for rate-limit / premium-endpoint throttling.
                msg = payload.get("Note") or payload.get("Information")
                raise RateLimitError(f"Alpha Vantage throttled: {msg}")
            if "Error Message" in payload:
                raise ProviderResponseError(
                    f"Alpha Vantage error: {payload['Error Message']}"
                )
        return payload

    # --- Overview -----------------------------------------------------------

    def fetch_overview(self, symbol: str) -> Optional[Overview]:
        data = self._request({"function": "OVERVIEW", "symbol": symbol})
        # AV returns {} for an unknown symbol on this endpoint.
        if not data or "Symbol" not in data:
            return None
        return Overview(
            name=data.get("Name"),
            exchange=data.get("Exchange"),
            asset_type=data.get("AssetType"),
            data=data,
            raw=data,
        )

    # --- Prices -------------------------------------------------------------

    def fetch_prices(self, symbol: str, full: bool) -> List[PriceBar]:
        data = self._request(
            {
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": "full" if full else "compact",
            }
        )
        series = data.get("Time Series (Daily)")
        if not series:
            return []

        bars: List[PriceBar] = []
        for day, fields in series.items():
            ts = _iso_date(day)
            if ts is None:
                continue  # skip a malformed date key rather than crash
            bars.append(
                PriceBar(
                    ts=ts,
                    open=_dec(fields.get("1. open")),
                    high=_dec(fields.get("2. high")),
                    low=_dec(fields.get("3. low")),
                    close=_dec(fields.get("4. close")),
                    adj_close=_dec(fields.get("5. adjusted close")),
                    volume=_int(fields.get("6. volume")),
                    dividend=_dec(fields.get("7. dividend amount")) or Decimal(0),
                    split_coef=_dec(fields.get("8. split coefficient")) or Decimal(1),
                    raw={"date": day, **fields},
                )
            )
        return bars

    # --- News ---------------------------------------------------------------

    def fetch_news(
        self,
        symbol: str,
        time_from: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[NewsItem]:
        params: Dict[str, str] = {
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "limit": str(min(limit, self._news_limit)),
            "sort": "EARLIEST",
        }
        if time_from is not None:
            # AV expects YYYYMMDDTHHMM.
            params["time_from"] = time_from.astimezone(timezone.utc).strftime("%Y%m%dT%H%M")

        data = self._request(params)
        feed = data.get("feed")
        if not feed:
            return []

        items: List[NewsItem] = []
        for art in feed:
            url = art.get("url")
            # AV provides no stable id; use the URL, else a hash of title+time.
            external_id = url or hashlib.sha256(
                f"{art.get('title','')}|{art.get('time_published','')}".encode("utf-8")
            ).hexdigest()

            authors = art.get("authors") or []
            ticker_links = [
                NewsTickerLink(
                    symbol=ts.get("ticker", ""),
                    relevance=_dec(ts.get("relevance_score")),
                    sentiment=_dec(ts.get("ticker_sentiment_score")),
                )
                for ts in (art.get("ticker_sentiment") or [])
                if ts.get("ticker")
            ]

            items.append(
                NewsItem(
                    external_id=external_id,
                    headline=art.get("title") or "(no title)",
                    summary=art.get("summary"),
                    content=None,  # AV does not return full body text
                    url=url,
                    author=", ".join(authors) if authors else None,
                    published_at=_av_time(art.get("time_published")),
                    sentiment_score=_dec(art.get("overall_sentiment_score")),
                    sentiment_label=art.get("overall_sentiment_label"),
                    tickers=ticker_links,
                    raw=art,
                )
            )
        return items

    # --- Fundamentals -------------------------------------------------------

    def fetch_fundamentals(self, symbol: str) -> List[FundamentalStatement]:
        """Fetch all four statement types, annual + quarterly, and flatten them."""
        out: List[FundamentalStatement] = []
        # (AV function, our statement_type, annual key, quarterly key)
        specs = [
            ("INCOME_STATEMENT", "income", "annualReports", "quarterlyReports"),
            ("BALANCE_SHEET", "balance", "annualReports", "quarterlyReports"),
            ("CASH_FLOW", "cashflow", "annualReports", "quarterlyReports"),
            ("EARNINGS", "earnings", "annualEarnings", "quarterlyEarnings"),
        ]
        for func, stype, annual_key, quarterly_key in specs:
            data = self._request({"function": func, "symbol": symbol})
            for period, key in (("annual", annual_key), ("quarterly", quarterly_key)):
                for report in data.get(key, []) or []:
                    fiscal = _iso_date(report.get("fiscalDateEnding"))
                    if fiscal is None:
                        continue  # a period with no valid fiscal date is unusable
                    out.append(
                        FundamentalStatement(
                            statement_type=stype,
                            fiscal_date=fiscal,
                            period=period,
                            currency=report.get("reportedCurrency"),
                            data=report,
                            raw=report,
                        )
                    )
        return out
