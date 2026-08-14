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
from typing import Optional

logger = logging.getLogger(__name__)


class SeenStore:
    """Two independent keyed-TTL tables sharing one mechanism:

    - ``seen(id, first_seen)``: per-item id dedupe, checked forever (no TTL
      lookup window — only `prune()` ages it out on a schedule).
    - ``story_seen(story_key, last_seen)``: per-story content dedupe (see
      CLAUDE.md §10) — catches the same real-world event arriving via a
      different source/id in a different poll cycle, checked within a
      rolling window via `has_recent_story()`.

    Both are thin wrappers around the same _has_key/_mark_key/_prune_table
    helpers; only the table/column names and whether a window is applied
    differ. Table and column names below are fixed string literals set at
    each call site (never external input), so building SQL with them is safe.
    """

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
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS story_seen ("
            "  story_key TEXT PRIMARY KEY,"
            "  last_seen TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    # --- generic keyed-TTL mechanism ---------------------------------------

    def _has_key(self, table: str, key_col: str, ts_col: str, key: str,
                 window_hours: Optional[float] = None) -> bool:
        if not key:
            return False
        if window_hours is None:
            cur = self._conn.execute(
                f"SELECT 1 FROM {table} WHERE {key_col} = ?", (key,)
            )
        else:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=window_hours)
            ).isoformat()
            cur = self._conn.execute(
                f"SELECT 1 FROM {table} WHERE {key_col} = ? AND {ts_col} >= ?",
                (key, cutoff),
            )
        return cur.fetchone() is not None

    def _mark_key(self, table: str, key_col: str, ts_col: str, key: str) -> None:
        """Upsert: insert the key with now(), or bump its timestamp if present."""
        if not key:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            f"INSERT INTO {table} ({key_col}, {ts_col}) VALUES (?, ?) "
            f"ON CONFLICT({key_col}) DO UPDATE SET {ts_col} = excluded.{ts_col}",
            (key, now),
        )
        self._conn.commit()

    def _mark_keys_if_absent(self, table: str, key_col: str, ts_col: str,
                              keys: list[str]) -> None:
        """Batch insert, leaving any already-present row's timestamp untouched."""
        if not keys:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._conn.executemany(
            f"INSERT OR IGNORE INTO {table} ({key_col}, {ts_col}) VALUES (?, ?)",
            [(key, now) for key in keys],
        )
        self._conn.commit()

    def _prune_table(self, table: str, ts_col: str, older_than_days: int) -> None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=older_than_days)
        ).isoformat()
        cur = self._conn.execute(
            f"DELETE FROM {table} WHERE {ts_col} < ?", (cutoff,)
        )
        self._conn.commit()
        if cur.rowcount:
            logger.info("Pruned %d %s entries older than %d days",
                        cur.rowcount, table, older_than_days)

    # --- id-level dedupe (`seen`) -------------------------------------------

    def has_seen(self, item_id: str) -> bool:
        return self._has_key("seen", "id", "first_seen", item_id)

    def mark_seen(self, item_ids: list[str]) -> None:
        self._mark_keys_if_absent("seen", "id", "first_seen", item_ids)

    def is_empty(self) -> bool:
        cur = self._conn.execute("SELECT 1 FROM seen LIMIT 1")
        return cur.fetchone() is None

    def prune(self, older_than_days: int = 30) -> None:
        self._prune_table("seen", "first_seen", older_than_days)

    # --- story-level dedupe (`story_seen`) ----------------------------------

    def has_recent_story(self, story_key: str, window_hours: float = 24.0) -> bool:
        """True if this story_key was marked seen within the last window_hours."""
        return self._has_key("story_seen", "story_key", "last_seen", story_key,
                              window_hours=window_hours)

    def mark_story_seen(self, story_key: str) -> None:
        self._mark_key("story_seen", "story_key", "last_seen", story_key)

    def prune_stories(self, older_than_days: int = 7) -> None:
        self._prune_table("story_seen", "last_seen", older_than_days)

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._conn.close()
