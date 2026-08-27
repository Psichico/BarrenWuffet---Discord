import sqlite3

from scripts.cadbury_bot.store import CadburyStore
from src.dedupe import SeenStore


def _rec(store, item_id, day, ticker="NVDA", headline="h", urgency="high"):
    store.record(item_id=item_id, day=day, ticker=ticker, headline=headline,
                 rewritten="one-liner", urgency=urgency, url="http://x")


def test_record_and_read_for_day(tmp_path):
    store = CadburyStore(str(tmp_path / "seen.db"))
    _rec(store, "a", "2026-08-26")
    _rec(store, "b", "2026-08-26")
    _rec(store, "c", "2026-08-27")

    day = store.kept_items_for_day("2026-08-26")
    assert [r["id"] for r in day] == ["a", "b"]  # insertion order
    assert day[0]["ticker"] == "NVDA"
    assert store.kept_items_for_day("2026-08-25") == []


def test_record_is_idempotent_on_id(tmp_path):
    store = CadburyStore(str(tmp_path / "seen.db"))
    _rec(store, "a", "2026-08-26", headline="first")
    _rec(store, "a", "2026-08-26", headline="second")  # INSERT OR IGNORE
    rows = store.kept_items_for_day("2026-08-26")
    assert len(rows) == 1
    assert rows[0]["headline"] == "first"


def test_record_skips_blank(tmp_path):
    store = CadburyStore(str(tmp_path / "seen.db"))
    store.record(item_id="", day="2026-08-26", ticker=None, headline="h",
                 rewritten=None, urgency="low", url="")
    store.record(item_id="x", day="2026-08-26", ticker=None, headline="",
                 rewritten=None, urgency="low", url="")
    assert store.kept_items_for_day("2026-08-26") == []


def test_run_marker(tmp_path):
    store = CadburyStore(str(tmp_path / "seen.db"))
    assert not store.has_run("2026-08-26")
    store.mark_run("2026-08-26")
    assert store.has_run("2026-08-26")
    store.mark_run("2026-08-26")  # upsert, no error
    assert store.has_run("2026-08-26")


def test_persists_across_reopen(tmp_path):
    path = str(tmp_path / "seen.db")
    store = CadburyStore(path)
    _rec(store, "a", "2026-08-26")
    store.mark_run("2026-08-26")
    store.close()

    reopened = CadburyStore(path)
    assert reopened.has_run("2026-08-26")
    assert len(reopened.kept_items_for_day("2026-08-26")) == 1


def test_prune_drops_old_rows(tmp_path):
    path = str(tmp_path / "seen.db")
    store = CadburyStore(path)
    _rec(store, "old", "2000-01-01")
    _rec(store, "new", "2026-08-26")
    store.mark_run("2000-01-01")
    store.mark_run("2026-08-26")

    conn = sqlite3.connect(path)
    conn.execute("UPDATE cadbury_kept_items SET created_at = ? WHERE id = ?",
                 ("2000-01-02T00:00:00+00:00", "old"))
    conn.execute("UPDATE cadbury_runs SET sent_at = ? WHERE day = ?",
                 ("2000-01-02T00:00:00+00:00", "2000-01-01"))
    conn.commit()
    conn.close()

    store2 = CadburyStore(path)
    store2.prune(kept_days=14, runs_days=30)
    assert [r["id"] for r in store2.kept_items_for_day("2000-01-01")] == []
    assert len(store2.kept_items_for_day("2026-08-26")) == 1
    assert not store2.has_run("2000-01-01")
    assert store2.has_run("2026-08-26")


def test_coexists_with_seenstore_on_same_file(tmp_path):
    path = str(tmp_path / "seen.db")
    seen = SeenStore(path)
    cad = CadburyStore(path)

    seen.mark_seen(["core-1"])
    _rec(cad, "cad-1", "2026-08-26")

    assert seen.has_seen("core-1")
    assert len(cad.kept_items_for_day("2026-08-26")) == 1
    # Neither store's tables clobber the other's.
    seen.mark_story_seen("story-x")
    assert seen.has_recent_story("story-x")
