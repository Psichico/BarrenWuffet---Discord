"""The poll loop: wires sources -> dedupe -> filter -> sinks. See §12.

The orchestrator only knows the Source/Filter/Sink interfaces, never the concrete
implementations — that decoupling is the whole extensibility story.
"""

from __future__ import annotations

import logging
import signal
import time
from typing import Iterable

from src.dedupe import SeenStore
from src.filter.base import Filter
from src.models import NewsItem
from src.sinks.base import Sink
from src.sources.base import Source

logger = logging.getLogger(__name__)


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


class Orchestrator:
    def __init__(
        self,
        sources: list[Source],
        filter_: Filter,
        sinks: list[Sink],
        store: SeenStore,
        poll_interval_seconds: int,
        batch_size: int,
        poll_interval_weekend_seconds: int = 10800,
        prune_older_than_days: int = 30,
        warm_start: bool = True,
        on_item_kept=None,
        post_iteration_hook=None,
    ):
        self.sources = sources
        self.filter = filter_
        self.sinks = sinks
        self.store = store
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_interval_weekend_seconds = poll_interval_weekend_seconds
        self.batch_size = batch_size
        self.prune_older_than_days = prune_older_than_days
        self.warm_start = warm_start
        # Optional, feature-agnostic extension points (both no-ops when unset):
        #   on_item_kept(item)       — called once per kept, non-duplicate item
        #   post_iteration_hook()    — called once after each poll iteration
        # Used by the optional cadbury_bot bolt-on; safe to leave in place.
        self.on_item_kept = on_item_kept
        self.post_iteration_hook = post_iteration_hook
        self._stop = False

    # --- lifecycle --------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def handle(signum, frame):
            logger.info("Received signal %s — finishing current iteration then "
                        "exiting.", signum)
            self._stop = True
        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)

    def run_forever(self) -> None:
        self._install_signal_handlers()

        if self.warm_start and self.store.is_empty():
            self._warm_start()

        while not self._stop:
            try:
                self._run_once()
            except Exception as e:  # one bad iteration must not kill the worker
                logger.exception("Iteration failed, continuing: %s", e)

            if self.post_iteration_hook is not None:
                try:
                    self.post_iteration_hook()
                except Exception as e:  # a hook must never kill the worker
                    logger.exception("post-iteration hook failed, continuing: %s", e)

            if self._stop:
                break
            sleep_duration = self._get_sleep_duration()
            self._sleep(sleep_duration)

        logger.info("Orchestrator stopped cleanly.")
        self.store.close()

    # --- one poll ---------------------------------------------------------

    def _fetch_all(self) -> list[NewsItem]:
        all_items: list[NewsItem] = []
        for source in self.sources:
            try:
                all_items += source.fetch()  # never raises by contract
            except Exception as e:  # defensive: honor "never crash the loop"
                logger.error("Source %s raised (contract violation): %s",
                             getattr(source, "name", source), e)
        return all_items

    def _warm_start(self) -> None:
        logger.info("Warm start: marking current items as seen WITHOUT "
                    "delivering (avoids dumping backlog to Discord).")
        all_items = self._fetch_all()
        self.store.mark_seen([i.id for i in all_items])
        logger.info("Warm start complete: %d items marked seen.", len(all_items))

    def _run_once(self) -> None:
        all_items = self._fetch_all()
        new_items = [i for i in all_items if not self.store.has_seen(i.id)]
        logger.info("Fetched %d items, %d new.", len(all_items), len(new_items))

        if new_items:
            for batch in _chunks(new_items, self.batch_size):
                self.filter.process(batch)  # sets keep/urgency/reason/rewritten
                for item in batch:
                    if not item.keep:
                        logger.info("DROPPED %s (%s)", item.headline, item.reason)
                        continue
                    story_key = item.meta.get("story_key")
                    if story_key and self.store.has_recent_story(story_key):
                        # Same real-world event already delivered (possibly via a
                        # different source/id/poll cycle) — don't re-post it.
                        logger.info("SKIPPED duplicate story %r for %s",
                                    story_key, item.headline)
                        continue
                    if self.on_item_kept is not None:
                        # Fires for kept, non-duplicate items regardless of
                        # whether delivery below succeeds. Must never raise.
                        try:
                            self.on_item_kept(item)
                        except Exception as e:
                            logger.exception("on_item_kept hook failed, "
                                             "continuing: %s", e)
                    delivered = self._deliver(item)
                    # Only remember the story as "seen" if it actually reached at
                    # least one sink — a webhook outage / 5xx must not poison the
                    # cross-source dedupe cache with a story nobody ever saw.
                    if story_key and delivered:
                        self.store.mark_story_seen(story_key)
                # mark_seen AFTER processing so a crash mid-filter doesn't
                # permanently swallow an unprocessed item.
                self.store.mark_seen([i.id for i in batch])

        self.store.prune(self.prune_older_than_days)
        self.store.prune_stories(self.prune_older_than_days)

    def _deliver(self, item: NewsItem) -> bool:
        delivered_to = []
        for sink in self.sinks:
            if sink.accepts(item):  # routing happens here
                if sink.deliver(item):
                    delivered_to.append(sink.name)
        logger.info("KEPT [%s] %s -> %s", item.urgency, item.headline, delivered_to)
        return bool(delivered_to)

    # --- helpers (overridable for tests) ----------------------------------

    def _get_sleep_duration(self) -> int:
        import datetime
        from datetime import timezone, timedelta
        
        # US Eastern Time (ET) is roughly UTC-4 or UTC-5. Using a fixed UTC-4 offset
        # covers the weekday/weekend transition days accurately.
        et_tz = timezone(timedelta(hours=-4))
        now_et = datetime.datetime.now(et_tz)
        
        weekday = now_et.weekday()  # 0=Monday, ..., 5=Saturday, 6=Sunday
        hour = now_et.hour
        
        # Friday after 20:00 (8 PM ET, when post-market ends) to Sunday before 18:00 (6 PM ET, when futures reopen)
        is_weekend = False
        if weekday == 5:  # Saturday
            is_weekend = True
        elif weekday == 6:  # Sunday
            if hour < 18:
                is_weekend = True
        elif weekday == 4:  # Friday
            if hour >= 20:
                is_weekend = True
                
        if is_weekend:
            # Poll every 3 hours (10800 seconds) during the weekend
            return self.poll_interval_weekend_seconds
        else:
            # Poll every 5 minutes (or as configured) during the week
            return self.poll_interval_seconds

    def _sleep(self, seconds: int) -> None:
        # Sleep in short slices so SIGTERM is honored promptly.
        remaining = seconds
        while remaining > 0 and not self._stop:
            step = min(1, remaining)
            time.sleep(step)
            remaining -= step
