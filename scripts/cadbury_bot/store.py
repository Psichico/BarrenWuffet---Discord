"""SQLite persistence for cadbury_bot.

Opens its OWN connection to the same DB file the core's ``SeenStore`` uses, and
owns only the ``cadbury_*`` tables. ``src/dedupe.py`` is never imported or
modified — removing this package leaves the core store untouched (drop the two
``cadbury_*`` tables to reclaim the space).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class CadburyStore:
    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path)
        # Play nice with the core's connection to the same file.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cadbury_kept_items ("
            "  id TEXT PRIMARY KEY,"
            "  day TEXT NOT NULL,"
            "  ticker TEXT,"
            "  headline TEXT NOT NULL,"
            "  rewritten TEXT,"
            "  urgency TEXT NOT NULL,"
            "  url TEXT,"
            "  created_at TEXT NOT NULL"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS cadbury_kept_items_day "
            "ON cadbury_kept_items(day)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cadbury_runs ("
            "  day TEXT PRIMARY KEY,"
            "  sent_at TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    # --- kept items ------------------------------------------------------------

    def record(self, *, item_id: str, day: str, ticker, headline: str,
               rewritten, urgency: str, url: str) -> None:
        """Insert one kept item. Idempotent on ``item_id`` (INSERT OR IGNORE),
        so re-seeing the same story on a later poll is a no-op."""
        if not item_id or not headline:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO cadbury_kept_items "
            "(id, day, ticker, headline, rewritten, urgency, url, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, day, ticker, headline, rewritten, urgency, url, now),
        )
        self._conn.commit()

    def kept_items_for_day(self, day: str) -> list[dict]:
        """All items flagged on ``day``, in the order they crossed (insert order)."""
        cur = self._conn.execute(
            "SELECT id, day, ticker, headline, rewritten, urgency, url, created_at "
            "FROM cadbury_kept_items WHERE day = ? ORDER BY rowid ASC",
            (day,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # --- run marker ----------------------------------------------------------

    def has_run(self, day: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM cadbury_runs WHERE day = ?", (day,)
        )
        return cur.fetchone() is not None

    def mark_run(self, day: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO cadbury_runs (day, sent_at) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET sent_at = excluded.sent_at",
            (day, now),
        )
        self._conn.commit()

    # --- maintenance -------------------------------------------------------

    def prune(self, kept_days: int = 14, runs_days: int = 30) -> None:
        kept_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=kept_days)
        ).isoformat()
        runs_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=runs_days)
        ).isoformat()
        c1 = self._conn.execute(
            "DELETE FROM cadbury_kept_items WHERE created_at < ?", (kept_cutoff,)
        )
        c2 = self._conn.execute(
            "DELETE FROM cadbury_runs WHERE sent_at < ?", (runs_cutoff,)
        )
        self._conn.commit()
        if c1.rowcount or c2.rowcount:
            logger.info("cadbury_bot: pruned %d kept rows, %d run rows",
                        c1.rowcount, c2.rowcount)

    def close(self) -> None:
        self._conn.close()
