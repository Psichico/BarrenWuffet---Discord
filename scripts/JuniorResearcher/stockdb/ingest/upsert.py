"""
Idempotent write helpers.

EVERY write here uses ON CONFLICT DO UPDATE against a natural key, so calling any
of these twice with the same data results in ONE row, not two. This is the core
of the "no duplicates, no corruption" guarantee — dedup is enforced by the
database, not by hopeful application logic.

Callers are expected to wrap related writes in a single db.transaction() so a
failure rolls the whole data type back atomically.

JSONB values are wrapped in psycopg's Json() adapter so dicts serialize safely.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional

from psycopg.types.json import Json

from ..errors import InvalidSymbolError
from ..symbols import normalize_symbol
from ..providers.base import (
    FundamentalStatement,
    NewsItem,
    Overview,
    PriceBar,
)

log = logging.getLogger(__name__)


# --- Reference rows --------------------------------------------------------

def get_or_create_source(conn, name: str, kind: str) -> int:
    """Return the id of a source, inserting it if needed."""
    row = conn.execute(
        """
        INSERT INTO sources (name, kind) VALUES (%s, %s)
        ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind
        RETURNING id
        """,
        (name, kind),
    ).fetchone()
    return row[0]


def get_or_create_ticker(conn, symbol: str, overview: Optional[Overview] = None) -> int:
    """
    Return the ticker id for a canonical symbol, creating/enriching the row.

    `symbol` MUST already be normalized. Company fields from `overview` are
    filled in when available but never overwritten with NULLs (COALESCE).
    """
    name = overview.name if overview else None
    exchange = overview.exchange if overview else None
    asset_type = overview.asset_type if overview else None
    row = conn.execute(
        """
        INSERT INTO tickers (symbol, name, exchange, asset_type)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (symbol) DO UPDATE SET
            name       = COALESCE(EXCLUDED.name, tickers.name),
            exchange   = COALESCE(EXCLUDED.exchange, tickers.exchange),
            asset_type = COALESCE(EXCLUDED.asset_type, tickers.asset_type),
            updated_at = now()
        RETURNING id
        """,
        (symbol, name, exchange, asset_type),
    ).fetchone()
    return row[0]


# --- Overview --------------------------------------------------------------

def upsert_overview(conn, ticker_id: int, overview: Overview) -> None:
    """Store the company overview (one row per ticker, replaced wholesale)."""
    conn.execute(
        """
        INSERT INTO company_overview (ticker_id, data, raw, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (ticker_id) DO UPDATE SET
            data = EXCLUDED.data, raw = EXCLUDED.raw, updated_at = now()
        """,
        (ticker_id, Json(overview.data), Json(overview.raw)),
    )


# --- Prices ----------------------------------------------------------------

def upsert_prices(conn, ticker_id: int, bars: Iterable[PriceBar]) -> int:
    """Upsert daily bars. Returns the number of rows written/updated."""
    rows = [
        (
            ticker_id, b.ts, b.open, b.high, b.low, b.close, b.adj_close,
            b.volume, b.split_coef, b.dividend, Json(b.raw),
        )
        for b in bars
    ]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO price_bars
                (ticker_id, ts, open, high, low, close, adj_close, volume,
                 split_coef, dividend, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker_id, ts) DO UPDATE SET
                open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                close=EXCLUDED.close, adj_close=EXCLUDED.adj_close,
                volume=EXCLUDED.volume, split_coef=EXCLUDED.split_coef,
                dividend=EXCLUDED.dividend, raw=EXCLUDED.raw
            """,
            rows,
        )
    return len(rows)


# --- News ------------------------------------------------------------------

def upsert_news(
    conn,
    source_id: int,
    items: List[NewsItem],
    requested_symbol: str,
) -> int:
    """
    Upsert news articles + their (article, ticker) links.

    For each linked ticker symbol we normalize it and get/create a ticker row;
    non-US / crypto / forex symbols that fail normalization are skipped as links
    (the article itself is still stored). The `requested_symbol` is always linked
    so the article is guaranteed discoverable under the ticker we fetched for.

    Returns the number of articles written/updated.
    """
    count = 0
    for item in items:
        news_id = conn.execute(
            """
            INSERT INTO news
                (source_id, external_id, headline, summary, content, url, author,
                 published_at, sentiment_score, sentiment_label, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id, external_id) DO UPDATE SET
                headline=EXCLUDED.headline, summary=EXCLUDED.summary,
                content=EXCLUDED.content, url=EXCLUDED.url, author=EXCLUDED.author,
                published_at=EXCLUDED.published_at,
                sentiment_score=EXCLUDED.sentiment_score,
                sentiment_label=EXCLUDED.sentiment_label, raw=EXCLUDED.raw
            RETURNING id
            """,
            (
                source_id, item.external_id, item.headline, item.summary,
                item.content, item.url, item.author, item.published_at,
                item.sentiment_score, item.sentiment_label, Json(item.raw),
            ),
        ).fetchone()
        news_db_id = news_id[0]

        # Build the set of ticker links (normalized). Use a dict so the requested
        # symbol and any duplicate links collapse cleanly.
        links = {}
        for link in item.tickers:
            try:
                canon = normalize_symbol(link.symbol)
            except InvalidSymbolError:
                continue  # skip non-US / crypto / forex tickers as links
            links[canon] = (link.relevance, link.sentiment)
        # Guarantee the requested symbol is linked even if AV omitted it.
        links.setdefault(requested_symbol, (None, None))

        for canon, (relevance, sentiment) in links.items():
            ticker_id = get_or_create_ticker(conn, canon)
            conn.execute(
                """
                INSERT INTO news_tickers (news_id, ticker_id, relevance, sentiment)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (news_id, ticker_id) DO UPDATE SET
                    relevance=EXCLUDED.relevance, sentiment=EXCLUDED.sentiment
                """,
                (news_db_id, ticker_id, relevance, sentiment),
            )
        count += 1
    return count


# --- Fundamentals ----------------------------------------------------------

def upsert_fundamentals(conn, ticker_id: int, statements: Iterable[FundamentalStatement]) -> int:
    """Upsert financial statements. Returns rows written/updated."""
    rows = [
        (
            ticker_id, s.statement_type, s.fiscal_date, s.period,
            s.currency, Json(s.data), Json(s.raw),
        )
        for s in statements
    ]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO fundamentals
                (ticker_id, statement_type, fiscal_date, period, currency, data, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker_id, statement_type, fiscal_date, period) DO UPDATE SET
                currency=EXCLUDED.currency, data=EXCLUDED.data,
                raw=EXCLUDED.raw, fetched_at=now()
            """,
            rows,
        )
    return len(rows)


# --- Tags (freeform labels) ------------------------------------------------

def get_or_create_tag(conn, name: str) -> int:
    """Return the id of a tag, inserting it if needed."""
    row = conn.execute(
        """
        INSERT INTO tags (name) VALUES (%s)
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        (name,),
    ).fetchone()
    return row[0]


def tag_news(conn, news_id: int, tag_name: str) -> None:
    """Attach a freeform tag to a news article (idempotent)."""
    tag_id = get_or_create_tag(conn, tag_name)
    conn.execute(
        """
        INSERT INTO news_tags (news_id, tag_id) VALUES (%s, %s)
        ON CONFLICT (news_id, tag_id) DO NOTHING
        """,
        (news_id, tag_id),
    )
