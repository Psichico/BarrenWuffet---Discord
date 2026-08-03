"""
Agent-facing API — the clean surface the future TradingAgents "Local Data
Analyst" agent will call.

`get_snapshot()` is the ONE function that agent needs:
  * normalizes the symbol,
  * returns the consolidated, point-in-time snapshot for `as_of_date`,
  * and (optionally) fetch-on-miss: if the ticker isn't in the DB yet, it
    ingests it first, then returns the snapshot.

This keeps the agent trivial: it hands over factual, size-bounded data (JSON +
a text summary) and never has to know about providers, SQL, or freshness.

Point-in-time note: fetch-on-miss does a full history pull, so it correctly
serves historical `as_of_date` requests too. We do NOT auto-refresh on staleness
here by default — historical analyses must stay reproducible; pass
`refresh_if_stale=True` for live/today use if you want the freshest data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from . import dal
from .config import Settings, get_settings
from .factory import build_provider
from .ingest.orchestrator import ingest_ticker
from .providers.base import Provider
from .symbols import normalize_symbol


def get_snapshot(
    symbol: str,
    as_of_date: Optional[date] = None,
    *,
    provider: Optional[Provider] = None,
    settings: Optional[Settings] = None,
    ingest_if_missing: bool = True,
    refresh_if_stale: bool = False,
    price_lookback: int = 60,
    news_days: int = 14,
    news_limit: int = 15,
) -> Optional[Dict[str, Any]]:
    """
    Return the consolidated snapshot for `symbol` as of `as_of_date`.

    Args:
      as_of_date        : point-in-time date (defaults to today, UTC).
      provider          : override the data provider (defaults to Alpha Vantage).
      settings          : override settings (defaults to environment).
      ingest_if_missing : if the ticker is absent, ingest it then retry.
      refresh_if_stale  : if the ticker is present but stale, run an incremental
                          refresh before building the snapshot. Only meaningful
                          for live/today requests.
      price_lookback/news_days/news_limit : payload window knobs.

    Returns a JSON-ready dict (with an added `summary_text`), or None if the
    ticker cannot be found or fetched.
    """
    settings = settings or get_settings()
    as_of = as_of_date or datetime.now(timezone.utc).date()
    canon = normalize_symbol(symbol)

    def _read() -> Optional[Dict[str, Any]]:
        return dal.get_ticker_snapshot(
            canon, as_of,
            price_lookback=price_lookback, news_days=news_days, news_limit=news_limit,
        )

    snap = _read()

    # Fetch-on-miss: ingest the full history, then read again.
    if snap is None and ingest_if_missing:
        prov = provider or build_provider(settings)
        ingest_ticker(canon, prov, settings)
        snap = _read()
    # Optional refresh-on-stale for live use (incremental; orchestrator decides).
    elif snap is not None and refresh_if_stale:
        prov = provider or build_provider(settings)
        ingest_ticker(canon, prov, settings)
        snap = _read()

    if snap is None:
        return None

    snap["summary_text"] = dal.build_text_summary(snap)
    return snap
