"""
Ingest package: turns provider data into durable, deduplicated DB rows.

    coverage.py     -> decide FULL / DELTA / SKIP per (ticker, source, data_type)
                       and record freshness + high-water marks afterwards.
    upsert.py       -> idempotent, transactional writes (ON CONFLICT) so re-runs
                       never duplicate.
    orchestrator.py -> ingest_ticker(): the one function the CLI / a future agent
                       calls to fetch + store everything for a symbol.
"""
