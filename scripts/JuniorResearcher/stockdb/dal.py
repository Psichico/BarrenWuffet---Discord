"""
Data Access Layer (read side).

This is the read counterpart to the ingest layer. It answers point-in-time
questions about a ticker and returns JSON-ready primitives (floats, ISO date
strings) so the output can be handed straight to an LLM agent or serialized.

THE ONE RULE (PLAN.md §1, §8): every query filters by `as_of_date`
  * price_bars.ts        <= as_of_date
  * news.published_at    <  as_of_date + 1 day   (include the whole day)
  * fundamentals.fiscal_date <= as_of_date
so a caller analyzing a historical date can NEVER see data published after it
(no look-ahead / future leakage).

`get_ticker_snapshot()` is the consolidated, size-bounded payload described in
PLAN.md §3 (windowed prices + indicators + latest fundamentals + recent news).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .db import connection
from .indicators import compute_indicators
from .symbols import normalize_symbol

# How many bars to load for indicator math (enough for SMA-200) even though the
# payload only returns `price_lookback` of them.
_INDICATOR_HISTORY = 260


def _f(value: Any) -> Optional[float]:
    """Decimal/None -> float/None (JSON-friendly)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _ticker_id(conn, symbol: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM tickers WHERE symbol = %s", (symbol,)).fetchone()
    return row[0] if row else None


def _overview(conn, ticker_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT data FROM company_overview WHERE ticker_id = %s", (ticker_id,)
    ).fetchone()
    return row[0] if row else None


def _bars(conn, ticker_id: int, as_of: date, limit: int) -> List[Dict[str, Any]]:
    """Return up to `limit` daily bars with ts <= as_of, OLDEST -> NEWEST."""
    rows = conn.execute(
        """
        SELECT ts, open, high, low, close, adj_close, volume
          FROM price_bars
         WHERE ticker_id = %s AND ts <= %s
         ORDER BY ts DESC
         LIMIT %s
        """,
        (ticker_id, as_of, limit),
    ).fetchall()
    # rows come newest-first; reverse to chronological for indicators/output.
    out = []
    for ts, o, h, low_, c, adj, vol in reversed(rows):
        out.append(
            {
                "date": ts.isoformat(),
                "open": _f(o), "high": _f(h), "low": _f(low_),
                "close": _f(c), "adj_close": _f(adj), "volume": vol,
            }
        )
    return out


def _latest_fundamentals(conn, ticker_id: int, as_of: date) -> List[Dict[str, Any]]:
    """The most recent statement per (statement_type, period) with fiscal_date <= as_of."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (statement_type, period)
               statement_type, period, fiscal_date, currency, data
          FROM fundamentals
         WHERE ticker_id = %s AND fiscal_date <= %s
         ORDER BY statement_type, period, fiscal_date DESC
        """,
        (ticker_id, as_of),
    ).fetchall()
    return [
        {
            "statement_type": st, "period": per,
            "fiscal_date": fd.isoformat(), "currency": cur, "data": data,
        }
        for st, per, fd, cur, data in rows
    ]


def _recent_news(
    conn, ticker_id: int, as_of: date, days: int, limit: int
) -> List[Dict[str, Any]]:
    """Recent articles for the ticker within [as_of - days, as_of], newest first."""
    start = as_of - timedelta(days=days)
    end_exclusive = as_of + timedelta(days=1)  # include all of as_of day
    rows = conn.execute(
        """
        SELECT n.headline, n.summary, n.url, n.published_at,
               n.sentiment_score, n.sentiment_label, nt.relevance, nt.sentiment
          FROM news n
          JOIN news_tickers nt ON nt.news_id = n.id
         WHERE nt.ticker_id = %s
           AND n.published_at >= %s
           AND n.published_at <  %s
         ORDER BY n.published_at DESC
         LIMIT %s
        """,
        (ticker_id, start, end_exclusive, limit),
    ).fetchall()
    return [
        {
            "headline": hl, "summary": summ, "url": url,
            "published_at": pub.isoformat() if pub else None,
            "sentiment_score": _f(ss), "sentiment_label": sl,
            "relevance": _f(rel), "ticker_sentiment": _f(tsent),
        }
        for hl, summ, url, pub, ss, sl, rel, tsent in rows
    ]


def _coverage(conn, ticker_id: int) -> Dict[str, Any]:
    rows = conn.execute(
        """
        SELECT data_type, high_water_date, last_fetched, last_status
          FROM data_coverage WHERE ticker_id = %s
        """,
        (ticker_id,),
    ).fetchall()
    return {
        dt: {
            "high_water": hw.isoformat() if hw else None,
            "last_fetched": lf.isoformat() if lf else None,
            "status": st,
        }
        for dt, hw, lf, st in rows
    }


def get_ticker_snapshot(
    symbol: str,
    as_of_date: date,
    *,
    price_lookback: int = 60,
    news_days: int = 14,
    news_limit: int = 15,
) -> Optional[Dict[str, Any]]:
    """
    Build the consolidated, size-bounded, point-in-time snapshot for one ticker.

    Returns a JSON-ready dict, or None if the ticker is not in the DB.
    """
    canon = normalize_symbol(symbol)
    with connection() as conn:
        tid = _ticker_id(conn, canon)
        if tid is None:
            return None

        # Load extra history for indicators; return only the requested window.
        history = _bars(conn, tid, as_of_date, _INDICATOR_HISTORY)
        closes = [b["close"] for b in history if b["close"] is not None]
        indicators = compute_indicators(closes)
        recent_bars = history[-price_lookback:] if price_lookback else history

        snapshot = {
            "ticker": canon,
            "as_of_date": as_of_date.isoformat(),
            "company": _overview(conn, tid),
            "prices": {
                "recent_bars": recent_bars,
                "indicators": indicators,
                "latest_close": closes[-1] if closes else None,
            },
            "fundamentals": _latest_fundamentals(conn, tid, as_of_date),
            "news": _recent_news(conn, tid, as_of_date, news_days, news_limit),
            "coverage": _coverage(conn, tid),
        }
    return snapshot


def build_text_summary(snap: Dict[str, Any]) -> str:
    """
    Render a compact human/LLM-readable summary of a snapshot.

    Provided alongside the structured data so an LLM agent can read prose while
    still having the JSON for precision (PLAN.md §3 delivers BOTH).
    """
    lines: List[str] = []
    company = snap.get("company") or {}
    name = company.get("Name") or snap["ticker"]
    lines.append(f"{name} ({snap['ticker']}) — data as of {snap['as_of_date']}")

    if company:
        sector = company.get("Sector")
        industry = company.get("Industry")
        mcap = company.get("MarketCapitalization")
        bits = [b for b in (sector, industry) if b]
        if bits:
            lines.append("Sector/Industry: " + " / ".join(bits))
        if mcap:
            lines.append(f"Market cap: {mcap}")

    prices = snap.get("prices") or {}
    ind = prices.get("indicators") or {}
    if prices.get("latest_close") is not None:
        lines.append(f"Latest close: {prices['latest_close']}")
    ind_bits = [f"{k}={v}" for k, v in ind.items() if v is not None]
    if ind_bits:
        lines.append("Indicators: " + ", ".join(ind_bits))

    news = snap.get("news") or []
    if news:
        lines.append(f"Recent news ({len(news)}):")
        for n in news[:5]:
            sent = n.get("sentiment_label") or ""
            lines.append(f"  - [{n.get('published_at','?')}] {n['headline']} {sent}".rstrip())
    else:
        lines.append("Recent news: none in window.")

    return "\n".join(lines)
