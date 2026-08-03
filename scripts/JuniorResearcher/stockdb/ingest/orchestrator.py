"""
The ingestion orchestrator: ingest_ticker() is THE entrypoint the CLI (and,
later, the TradingAgents data agent) calls.

Flow for one ticker (PLAN.md §8):
  1. normalize the symbol (reject anything non-US up front).
  2. take a per-ticker advisory lock so two processes can't ingest it at once.
  3. ensure the source + ticker rows exist (committed first — other tables FK to
     the ticker).
  4. for each data type (overview, prices, news, fundamentals), INDEPENDENTLY:
       - decide FULL / DELTA / SKIP from data_coverage,
       - fetch from the provider (rate-limited + retried),
       - write inside its own transaction (atomic),
       - record success/failure in data_coverage.
     Each data type is isolated: if news fails, prices/fundamentals still commit
     (graceful degradation).

Special handling:
  * PRICES + split detection: on a DELTA fetch, if any returned bar has a split
    coefficient != 1, we re-pull the FULL history because a split retroactively
    changes every adjusted close (PLAN.md §6).
  * NEWS delta: we pass time_from = the stored high-water date so only newer
    articles are fetched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import List, Optional, Sequence

from ..db import advisory_lock, connection, transaction
from ..symbols import normalize_symbol
from ..providers.base import Provider
from ..config import Settings
from . import upsert
from .coverage import DELTA, FULL, SKIP, decide, get_coverage, update_coverage

log = logging.getLogger(__name__)

# The data types we manage, in dependency-safe order (overview first so the
# ticker row is enriched before anything else).
DATA_TYPES: Sequence[str] = ("overview", "prices", "news", "fundamentals")


@dataclass
class TypeResult:
    """Outcome of ingesting one data type."""
    data_type: str
    decision: str          # full | delta | skip
    status: str            # ok | skip | error
    rows: int = 0
    error: Optional[str] = None


@dataclass
class IngestReport:
    """Aggregate outcome for one ticker, returned to the caller."""
    symbol: str
    results: List[TypeResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if no data type errored."""
        return all(r.status != "error" for r in self.results)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def ingest_ticker(
    symbol: str,
    provider: Provider,
    settings: Settings,
    *,
    only: Optional[Sequence[str]] = None,
    force_full: bool = False,
    no_write: bool = False,
) -> IngestReport:
    """
    Fetch and store all data for one ticker.

    Args:
      symbol      : raw symbol (will be normalized).
      provider    : a Provider implementation (e.g. AlphaVantageProvider).
      settings    : validated Settings (ttl_days, news_limit, ...).
      only        : restrict to these data types (default: all).
      force_full  : ignore freshness and force a FULL pull of everything.
      no_write    : dry run — fetch but do not write to the DB or coverage.

    Returns an IngestReport with a per-data-type outcome.
    """
    canon = normalize_symbol(symbol)
    report = IngestReport(symbol=canon)
    types = list(only) if only else list(DATA_TYPES)

    # Serialize ingestion of this specific ticker across processes/threads.
    with advisory_lock(f"ingest:{canon}"):
        # Ensure source + ticker rows exist and are committed before we touch
        # any table that references the ticker via a foreign key.
        with transaction() as conn:
            source_id = upsert.get_or_create_source(conn, provider.name, "market_data")
            ticker_id = upsert.get_or_create_ticker(conn, canon)

        for dt in types:
            report.results.append(
                _process_type(
                    dt, canon, ticker_id, source_id, provider, settings,
                    force_full=force_full, no_write=no_write,
                )
            )

    return report


def _process_type(
    data_type: str,
    canon: str,
    ticker_id: int,
    source_id: int,
    provider: Provider,
    settings: Settings,
    *,
    force_full: bool,
    no_write: bool,
) -> TypeResult:
    """Decide, fetch, store, and record coverage for a single data type."""
    # Read current freshness state.
    with connection() as conn:
        cov = get_coverage(conn, ticker_id, source_id, data_type)

    decision = FULL if force_full else decide(cov, settings.ttl_days)
    if decision == SKIP:
        return TypeResult(data_type, decision, "skip")

    try:
        rows, high_water_date, high_water_ts = _fetch_and_store(
            data_type, canon, ticker_id, source_id, provider, settings,
            decision=decision, cov=cov, no_write=no_write,
        )
        # Record success (skip in dry-run).
        if not no_write:
            with transaction() as conn:
                update_coverage(
                    conn, ticker_id, source_id, data_type,
                    high_water_date=high_water_date,
                    high_water_ts=high_water_ts,
                    status="ok",
                )
        return TypeResult(data_type, decision, "ok", rows=rows)

    except Exception as exc:  # noqa: BLE001 - isolate this data type's failure
        log.exception("Ingest failed for %s / %s", canon, data_type)
        if not no_write:
            # Persist the failure so a rerun knows to retry just this type.
            with transaction() as conn:
                update_coverage(
                    conn, ticker_id, source_id, data_type,
                    high_water_date=None, status="error", error=str(exc),
                )
        return TypeResult(data_type, decision, "error", error=str(exc))


def _fetch_and_store(
    data_type: str,
    canon: str,
    ticker_id: int,
    source_id: int,
    provider: Provider,
    settings: Settings,
    *,
    decision: str,
    cov,
    no_write: bool,
):
    """
    Fetch one data type from the provider and write it.

    Returns (rows_written, high_water_date, high_water_ts). Date-based types set
    only high_water_date; time-based types (news) also set high_water_ts for a
    precise incremental resume. Network fetch happens OUTSIDE the DB
    transaction; only the writes are transactional.
    """
    if data_type == "overview":
        ov = provider.fetch_overview(canon)
        if ov is None:
            return 0, None, None
        if not no_write:
            with transaction() as conn:
                # Enrich the ticker row (name/exchange) and store the overview.
                upsert.get_or_create_ticker(conn, canon, ov)
                upsert.upsert_overview(conn, ticker_id, ov)
        return 1, _today_utc(), None

    if data_type == "prices":
        full = decision == FULL
        bars = provider.fetch_prices(canon, full=full)
        # Split detection: a split retroactively changes historical adj_close,
        # so a delta that reveals a split forces a full re-pull.
        if decision == DELTA and any(
            b.split_coef is not None and b.split_coef != 1 for b in bars
        ):
            log.info("Split detected for %s; re-pulling full price history", canon)
            bars = provider.fetch_prices(canon, full=True)
        n = len(bars)
        if not no_write:
            with transaction() as conn:
                n = upsert.upsert_prices(conn, ticker_id, bars)
        high_water = max((b.ts for b in bars), default=None)
        return n, high_water, None

    if data_type == "news":
        time_from = None
        if decision == DELTA and cov is not None:
            # Prefer the exact timestamp of the newest article we hold; fall back
            # to the date-only high-water (midnight) for rows written before the
            # high_water_ts column existed.
            if cov.high_water_ts is not None:
                time_from = cov.high_water_ts
            elif cov.high_water_date is not None:
                time_from = datetime(
                    cov.high_water_date.year,
                    cov.high_water_date.month,
                    cov.high_water_date.day,
                    tzinfo=timezone.utc,
                )
        items = provider.fetch_news(canon, time_from=time_from, limit=settings.news_limit)
        n = len(items)
        if not no_write:
            with transaction() as conn:
                n = upsert.upsert_news(conn, source_id, items, canon)
        published = [i.published_at for i in items if i.published_at]
        high_water_ts = max(published, default=None)
        high_water_date = high_water_ts.date() if high_water_ts else None
        return n, high_water_date, high_water_ts

    if data_type == "fundamentals":
        statements = provider.fetch_fundamentals(canon)
        n = len(statements)
        if not no_write:
            with transaction() as conn:
                n = upsert.upsert_fundamentals(conn, ticker_id, statements)
        high_water = max((s.fiscal_date for s in statements), default=None)
        return n, high_water, None

    raise ValueError(f"Unknown data_type: {data_type!r}")
