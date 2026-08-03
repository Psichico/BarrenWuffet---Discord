"""
Migration runner for the plain numbered SQL files in schema/.

Behavior (idempotent + safe):
  1. Ensure schema_migrations exists (by always applying 001 first; it uses
     CREATE TABLE IF NOT EXISTS).
  2. List schema/*.sql sorted by filename.
  3. For each file NOT already recorded in schema_migrations:
        - apply the whole file inside ONE transaction
        - record it in schema_migrations
     If a file errors, its transaction rolls back and the runner stops — the DB
     is never left partially migrated for that file.

This is intentionally tiny and dependency-free: no Alembic, no ORM. What you
see in schema/ is exactly what runs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from .db import connection

log = logging.getLogger(__name__)

# schema/ lives next to the project root (one level up from this package).
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


def _migration_files() -> List[Path]:
    """Return all migration .sql files, sorted so 001 < 002 < ... < 010."""
    if not SCHEMA_DIR.is_dir():
        raise FileNotFoundError(f"Schema directory not found: {SCHEMA_DIR}")
    return sorted(SCHEMA_DIR.glob("*.sql"))


def _already_applied(conn) -> set:
    """Return the set of filenames already recorded as applied (empty if the
    tracking table itself does not exist yet)."""
    try:
        rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
        return {r[0] for r in rows}
    except Exception:
        # Table doesn't exist yet on a brand-new DB; 001 will create it.
        conn.rollback()
        return set()


def run_migrations() -> List[str]:
    """
    Apply all pending migrations in order. Returns the list of filenames that
    were applied during this call (empty if the DB was already up to date).
    """
    applied_now: List[str] = []
    files = _migration_files()

    with connection() as conn:
        done = _already_applied(conn)

        for path in files:
            name = path.name
            if name in done:
                log.debug("Skipping already-applied migration %s", name)
                continue

            sql = path.read_text(encoding="utf-8")
            log.info("Applying migration %s", name)

            # One transaction per file: it either fully applies + gets recorded,
            # or fully rolls back. No partial migrations.
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s) "
                    "ON CONFLICT (filename) DO NOTHING",
                    (name,),
                )
            applied_now.append(name)

    if applied_now:
        log.info("Applied %d migration(s): %s", len(applied_now), ", ".join(applied_now))
    else:
        log.info("Database schema already up to date")
    return applied_now
