"""SQLite-backed dedupe store — survives restarts if the DB file persists.

See §8 of the plan: on Render the default filesystem is ephemeral, so the DB
should live on a persistent disk (DEDUPE_PATH=/var/data/seen.db), with the
warm-start flag as a secondary safety net.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class SeenStore:
    def __init__(self, path: str):
        self.path = path
        # Ensure parent directory exists (e.g. /var/data) when a nested path is
        # configured; harmless for a bare "./seen.db".
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS seen ("
            "  id TEXT PRIMARY KEY,"
            "  first_seen TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    def has_seen(self, item_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM seen WHERE id = ?", (item_id,))
        return cur.fetchone() is not None

    def mark_seen(self, item_ids: list[str]) -> None:
        if not item_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._conn.executemany(
            "INSERT OR IGNORE INTO seen (id, first_seen) VALUES (?, ?)",
            [(item_id, now) for item_id in item_ids],
        )
        self._conn.commit()

    def is_empty(self) -> bool:
        cur = self._conn.execute("SELECT 1 FROM seen LIMIT 1")
        return cur.fetchone() is None

    def prune(self, older_than_days: int = 30) -> None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=older_than_days)
        ).isoformat()
        cur = self._conn.execute("DELETE FROM seen WHERE first_seen < ?", (cutoff,))
        self._conn.commit()
        if cur.rowcount:
            logger.info("Pruned %d seen entries older than %d days",
                        cur.rowcount, older_than_days)

    def close(self) -> None:
        self._conn.close()
