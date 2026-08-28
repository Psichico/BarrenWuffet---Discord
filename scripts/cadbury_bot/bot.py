"""The scheduler gate: decide when to build+send the recap, once per weekday.

``tick()`` is handed to the core as the ``post_iteration_hook`` — it's called
once per poll iteration and is cheap when there's nothing to do. All the
gating (weekday, wall-clock minute, once-per-day) lives here.
"""

from __future__ import annotations

import datetime as _dt
import logging

from scripts.cadbury_bot.config import CadburyConfig
from scripts.cadbury_bot.mailer import EmailMailer
from scripts.cadbury_bot.recorder import KeptItemRecorder
from scripts.cadbury_bot.store import CadburyStore
from scripts.cadbury_bot.timeutil import et_now

logger = logging.getLogger(__name__)


class CadburyBot:
    def __init__(self, *, cfg: CadburyConfig, store: CadburyStore,
                 recorder: KeptItemRecorder, composer, mailer: EmailMailer,
                 client=None):
        self.cfg = cfg
        self.store = store
        self.recorder = recorder      # exposed for the on_item_kept hook
        self.composer = composer
        self.mailer = mailer
        self._client = client         # None -> composer builds the real client

    # --- the loop hook ---------------------------------------------------------

    def tick(self, now=None) -> None:
        """Send today's recap if it's time and it hasn't gone out yet."""
        cfg = self.cfg
        if not cfg.enabled:
            return
        now = now or et_now(cfg.timezone)
        if now.weekday() >= 5:  # Sat / Sun
            return
        if (now.hour, now.minute) < (cfg.send_hour, cfg.send_minute):
            return
        day = now.date().isoformat()
        if self.store.has_run(day):
            return
        self._send_for(day, now)
        self.store.prune(cfg.prune_kept_days, cfg.prune_runs_days)

    # --- manual trigger (python -m scripts.cadbury_bot) --------------------

    def run_now(self, *, force: bool = False, day: "str | None" = None) -> bool:
        """Build+send immediately. Returns True if an email was sent."""
        now = et_now(self.cfg.timezone)
        if day:
            # Re-send of a specific day: render the recap as of that date.
            try:
                d = _dt.date.fromisoformat(day)
                now = now.replace(year=d.year, month=d.month, day=d.day)
            except ValueError:
                logger.warning("cadbury_bot: bad --date %r, using today", day)
                day = now.date().isoformat()
        else:
            day = now.date().isoformat()
        if self.store.has_run(day) and not force:
            logger.info("cadbury_bot: recap already sent for %s (use --force)", day)
            return False
        sent = self._send_for(day, now)
        self.store.prune(self.cfg.prune_kept_days, self.cfg.prune_runs_days)
        return sent

    # --- internals --------------------------------------------------------

    def _send_for(self, day: str, now) -> bool:
        total = self.store.count_for_day(day)
        items = self.store.kept_items_for_day(day, limit=self.cfg.digest_max_items)
        body = self.composer(self.cfg, items, now, client=self._client, total=total)
        subject = f"{self.cfg.subject_prefix} — {now.strftime('%a, %b %d %Y')}"
        if self.mailer.send(subject, body):
            self.store.mark_run(day)  # only mark on a real send -> retry next loop
            logger.info("cadbury_bot: recap sent for %s (%d of %d flagged)",
                        day, len(items), total)
            return True
        logger.warning("cadbury_bot: recap send failed for %s - will retry", day)
        return False
