from src.dedupe import SeenStore


def test_mark_and_has_seen(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    assert store.is_empty()
    assert not store.has_seen("a")

    store.mark_seen(["a", "b"])
    assert store.has_seen("a")
    assert store.has_seen("b")
    assert not store.has_seen("c")
    assert not store.is_empty()


def test_mark_seen_is_idempotent(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    store.mark_seen(["a"])
    store.mark_seen(["a"])  # INSERT OR IGNORE — no error, no dup
    assert store.has_seen("a")


def test_mark_seen_empty_list_noop(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    store.mark_seen([])
    assert store.is_empty()


def test_persists_across_reopen(tmp_path):
    path = str(tmp_path / "seen.db")
    store = SeenStore(path)
    store.mark_seen(["x"])
    store.close()

    reopened = SeenStore(path)
    assert reopened.has_seen("x")  # survives "restart"


def test_prune_removes_old(tmp_path):
    import sqlite3

    path = str(tmp_path / "seen.db")
    store = SeenStore(path)
    store.mark_seen(["old", "new"])

    # Backdate "old" well past the cutoff.
    conn = sqlite3.connect(path)
    conn.execute("UPDATE seen SET first_seen = ? WHERE id = ?",
                 ("2000-01-01T00:00:00+00:00", "old"))
    conn.commit()
    conn.close()

    store2 = SeenStore(path)
    store2.prune(older_than_days=30)
    assert not store2.has_seen("old")
    assert store2.has_seen("new")


def test_story_seen_within_window(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    assert not store.has_recent_story("rddt-sp500-inclusion")

    store.mark_story_seen("rddt-sp500-inclusion")
    assert store.has_recent_story("rddt-sp500-inclusion")
    assert store.has_recent_story("rddt-sp500-inclusion", window_hours=48)


def test_story_seen_outside_window_not_matched(tmp_path):
    import sqlite3

    path = str(tmp_path / "seen.db")
    store = SeenStore(path)
    store.mark_story_seen("old-story")

    conn = sqlite3.connect(path)
    conn.execute("UPDATE story_seen SET last_seen = ? WHERE story_key = ?",
                 ("2000-01-01T00:00:00+00:00", "old-story"))
    conn.commit()
    conn.close()

    store2 = SeenStore(path)
    assert not store2.has_recent_story("old-story", window_hours=24)


def test_has_recent_story_blank_key_is_false(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    assert not store.has_recent_story("")
    store.mark_story_seen("")  # no-op, must not raise
    assert not store.has_recent_story("")


def test_mark_story_seen_updates_timestamp(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    store.mark_story_seen("a-story")
    store.mark_story_seen("a-story")  # re-mark shouldn't error (upsert)
    assert store.has_recent_story("a-story")


def test_prune_stories_removes_old(tmp_path):
    import sqlite3

    path = str(tmp_path / "seen.db")
    store = SeenStore(path)
    store.mark_story_seen("old-story")
    store.mark_story_seen("new-story")

    conn = sqlite3.connect(path)
    conn.execute("UPDATE story_seen SET last_seen = ? WHERE story_key = ?",
                 ("2000-01-01T00:00:00+00:00", "old-story"))
    conn.commit()
    conn.close()

    store2 = SeenStore(path)
    store2.prune_stories(older_than_days=7)
    assert not store2.has_recent_story("old-story", window_hours=24 * 365 * 30)
    assert store2.has_recent_story("new-story")
