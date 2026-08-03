"""
Freshness / incremental-refresh bookkeeping (the data_coverage table).

The decision function answers, per data type: do we need a FULL pull, an
incremental DELTA, or can we SKIP (already fresh)?  See PLAN.md §8.

Decision inputs:
  * last_fetched    - when we last SUCCESSFULLY fetched this data type.
  * high_water_date - the newest data point we hold (delta resume point).
  * last_status     - 'ok' | 'partial' | 'error'.

Rules:
  * never fetched (no row / no last_fetched)      -> FULL
  * previous attempt not 'ok' (partial/error)     -> re-fetch (DELTA if we have a
                                                     high-water mark, else FULL)
  * fresh (age <= ttl_days)                        -> SKIP
  * stale (age > ttl_days)                         -> DELTA if high-water exists,
                                                     else FULL
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

# The three possible decisions.
FULL = "full"
DELTA = "delta"
SKIP = "skip"


@dataclass
class Coverage:
    """In-memory view of one data_coverage row."""
    high_water_date: Optional[date]
    last_fetched: Optional[datetime]
    last_status: str = "ok"
    # Exact timestamp of the newest data point (time-based sources like news);
    # None for date-based types (prices, fundamentals).
    high_water_ts: Optional[datetime] = None


def get_coverage(conn, ticker_id: int, source_id: int, data_type: str) -> Optional[Coverage]:
    """Load the coverage row, or None if this data type was never ingested."""
    row = conn.execute(
        """
        SELECT high_water_date, last_fetched, last_status, high_water_ts
          FROM data_coverage
         WHERE ticker_id = %s AND source_id = %s AND data_type = %s
        """,
        (ticker_id, source_id, data_type),
    ).fetchone()
    if row is None:
        return None
    return Coverage(
        high_water_date=row[0],
        last_fetched=row[1],
        last_status=row[2],
        high_water_ts=row[3],
    )


def decide(cov: Optional[Coverage], ttl_days: int, now: Optional[datetime] = None) -> str:
    """Return FULL, DELTA, or SKIP for the given coverage state."""
    now = now or datetime.now(timezone.utc)

    # Never fetched -> must do a full pull.
    if cov is None or cov.last_fetched is None:
        return FULL

    # A prior partial/error attempt: resume from high-water if possible.
    if cov.last_status != "ok":
        return DELTA if cov.high_water_date else FULL

    # Fresh enough? (TTL applies to recent data; historical is served from DB.)
    age_days = (now - cov.last_fetched).total_seconds() / 86400.0
    if age_days <= ttl_days:
        return SKIP

    # Stale: incremental if we know where to resume, else full.
    return DELTA if cov.high_water_date else FULL


def update_coverage(
    conn,
    ticker_id: int,
    source_id: int,
    data_type: str,
    *,
    high_water_date: Optional[date],
    status: str,
    high_water_ts: Optional[datetime] = None,
    error: Optional[str] = None,
) -> None:
    """
    Upsert the coverage row after an attempt.

    Both high-water marks (date and timestamp) are advanced monotonically
    (GREATEST) so a delta fetch that returns older-than-current data can never
    move a mark backwards. last_fetched is set to now() only on a successful
    ('ok') attempt, so a failed attempt does not reset the staleness clock.
    """
    conn.execute(
        """
        INSERT INTO data_coverage
            (ticker_id, source_id, data_type, high_water_date, high_water_ts,
             last_fetched, last_status, last_error)
        VALUES
            (%s, %s, %s, %s, %s, CASE WHEN %s = 'ok' THEN now() ELSE NULL END, %s, %s)
        ON CONFLICT (ticker_id, source_id, data_type) DO UPDATE SET
            high_water_date = GREATEST(
                data_coverage.high_water_date,
                COALESCE(EXCLUDED.high_water_date, data_coverage.high_water_date)
            ),
            high_water_ts = GREATEST(
                data_coverage.high_water_ts,
                COALESCE(EXCLUDED.high_water_ts, data_coverage.high_water_ts)
            ),
            last_fetched = CASE WHEN EXCLUDED.last_status = 'ok'
                                THEN now() ELSE data_coverage.last_fetched END,
            last_status  = EXCLUDED.last_status,
            last_error   = EXCLUDED.last_error
        """,
        (ticker_id, source_id, data_type, high_water_date, high_water_ts,
         status, status, error),
    )
