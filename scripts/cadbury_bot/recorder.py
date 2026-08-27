"""Capture side of the feature.

``KeptItemRecorder.record`` is handed to the core as the ``on_item_kept`` hook.
It is a plain callable (NOT a ``Sink``) so it can't inflate the orchestrator's
"delivered to N sinks" tally or interfere with story-dedupe bookkeeping.

Contract (same spirit as ``Sink``): never raise — log and swallow.
"""

from __future__ import annotations

import logging

from scripts.cadbury_bot.store import CadburyStore
from scripts.cadbury_bot.timeutil import et_today
from src.models import NewsItem

logger = logging.getLogger(__name__)


class KeptItemRecorder:
    def __init__(self, store: CadburyStore, timezone: str = "America/New_York"):
        self._store = store
        self._tz = timezone

    def record(self, item: NewsItem) -> None:
        try:
            if not getattr(item, "keep", False):
                return  # defensive: the core only calls us for kept items
            self._store.record(
                item_id=item.id,
                day=et_today(self._tz),
                ticker=item.ticker,
                headline=item.headline,
                rewritten=item.rewritten,
                urgency=item.urgency or "low",
                url=item.url or "",
            )
        except Exception as e:  # never disturb the poll loop
            logger.exception("cadbury_bot: failed to record kept item %r: %s",
                             getattr(item, "id", "?"), e)
