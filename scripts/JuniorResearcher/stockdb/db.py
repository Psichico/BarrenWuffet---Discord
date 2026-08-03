"""
Database access layer: a shared connection pool plus small helpers that make
correct, corruption-resistant usage the DEFAULT.

Key guardrails provided here:
  * A bounded connection pool (psycopg_pool) — no unbounded connection growth.
  * transaction() context manager — commit on success, ROLLBACK on any error,
    so a partial write can never be left committed.
  * advisory_lock() context manager — a Postgres session-level advisory lock
    keyed to a ticker, so two processes cannot ingest the same ticker at once
    (prevents duplicate API spend AND write races).

We use psycopg (v3). Autocommit is OFF on pooled connections; we control
transactions explicitly via the helpers below.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional
from zlib import crc32

import psycopg
from psycopg_pool import ConnectionPool

from .config import get_settings

log = logging.getLogger(__name__)

# Module-level singleton pool. Created lazily on first use so importing this
# module never triggers a DB connection (important for tests / --help).
_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    """Return the process-wide connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        settings = get_settings()
        # min_size/max_size bound concurrency. open=True connects eagerly so a
        # bad DATABASE_URL fails fast here rather than on first query.
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=5,
            open=True,
            kwargs={"autocommit": False},
        )
        log.debug("Opened PostgreSQL connection pool")
    return _pool


def close_pool() -> None:
    """Close the pool (call on shutdown; safe if never opened)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Borrow a connection from the pool and return it automatically."""
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """
    Run a block inside a single DB transaction.

    Commits if the block succeeds; ROLLS BACK if it raises. This is the unit of
    atomicity for ingestion: all rows for one (ticker, data_type) are written
    inside one transaction() so a mid-write crash leaves NO half-applied state.

        with transaction() as conn:
            conn.execute(...)
            conn.execute(...)
        # committed here; or rolled back if an exception propagated
    """
    pool = get_pool()
    with pool.connection() as conn:
        # psycopg's `with conn.transaction()` commits on exit / rolls back on error.
        with conn.transaction():
            yield conn


def _lock_key(name: str) -> int:
    """
    Map an arbitrary string (e.g. a ticker symbol) to the signed 32-bit int that
    pg_advisory_lock expects. crc32 is stable and collisions are harmless here
    (worst case: two unrelated tickers serialize briefly).
    """
    # crc32 is unsigned 32-bit; shift into signed range for pg_advisory_lock(int).
    return crc32(name.encode("utf-8")) - 2**31


@contextmanager
def advisory_lock(name: str) -> Iterator[None]:
    """
    Acquire a Postgres session-level advisory lock for `name` for the duration
    of the block, then release it. Used to serialize ingestion of a single
    ticker across processes/threads.

        with advisory_lock(f"ingest:{symbol}"):
            ... ingest ...

    NOTE: uses its own dedicated connection so the lock's lifetime is tied to
    this block and not to any surrounding transaction.
    """
    key = _lock_key(name)
    pool = get_pool()
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (key,))
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
