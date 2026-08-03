"""
stockdb
=======

A modular, provider-agnostic pipeline that fetches US stock data (prices, news,
fundamentals, company overview) from external providers and stores it in a
single normalized PostgreSQL database, organized per ticker.

High-level map (read in this order to understand the system):

    config.py       -> loads & validates settings from the environment
    errors.py       -> typed exceptions used across the package
    db.py           -> connection pool + transaction / advisory-lock helpers
    migrate.py      -> applies numbered SQL files in schema/ idempotently
    symbols.py      -> normalizes raw symbols to a canonical US-only form
    ratelimit.py    -> token-bucket limiter shared by all provider calls

    providers/base.py         -> the Provider interface (plug in new sources here)
    providers/alphavantage.py -> the Alpha Vantage implementation

    ingest/coverage.py     -> decides FULL vs DELTA vs SKIP per data type
    ingest/upsert.py       -> idempotent, transactional writes (dedup)
    ingest/orchestrator.py -> ties it together: ingest_ticker(symbol)

    cli.py          -> command-line entrypoint (migrate/ingest/refresh/status)

Design goals (see PLAN.md):
  * Idempotent   - re-running never duplicates (DB UNIQUE + ON CONFLICT).
  * Incremental  - refreshes fetch only the delta (data_coverage high-water).
  * Correct      - reads filter by as_of_date (no look-ahead).
  * Extensible   - a new source = a new Provider subclass + a `sources` row.
  * Safe         - transactions, constraints, advisory locks -> no corruption.
"""

__version__ = "0.1.0"
