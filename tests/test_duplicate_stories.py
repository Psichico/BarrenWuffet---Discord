"""Cross-source / cross-poll duplicate-story suppression.

Reproduces the reported bug: the same real-world event, fetched from two
different sources (different NewsItem.id each) in two separate poll cycles,
should only be delivered once — via the filter-derived story_key, not the
per-item id.
"""

from src.dedupe import SeenStore
from src.filter.base import Filter
from src.models import NewsItem
from src.orchestrator import Orchestrator
from src.sinks.base import Sink


class _FakeSource:
    def __init__(self, name, items):
        self.name = name
        self._items = items

    def fetch(self):
        return self._items


class _StoryTaggingFilter(Filter):
    """Stands in for ClaudeFilter: marks every item keep=True and assigns the
    story_key the test wired onto it via item.meta (simulating what the real
    Claude call would infer from content)."""

    def process(self, items):
        for item in items:
            item.keep = True
            item.urgency = "medium"
        return items


class _RecordingSink(Sink):
    def __init__(self, name):
        self.name = name
        self.delivered = []

    def accepts(self, item):
        return True

    def deliver(self, item):
        self.delivered.append(item.id)
        return True


def _make_orchestrator(source, store, sink):
    return Orchestrator(
        sources=[source],
        filter_=_StoryTaggingFilter(),
        sinks=[sink],
        store=store,
        poll_interval_seconds=300,
        batch_size=20,
        warm_start=False,
    )


def test_same_story_different_source_and_poll_delivered_once(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    sink = _RecordingSink("discord_news")

    # Poll 1: finviz_market delivers item A with story_key "rddt-sp500-inclusion".
    item_a = NewsItem(id="url-a", source="finviz_market",
                       headline="RDDT joins the S&P 500. Analysts have concerns.",
                       url="url-a", ticker="RDDT",
                       meta={"story_key": "rddt-sp500-inclusion"})
    orch = _make_orchestrator(_FakeSource("finviz_market", [item_a]), store, sink)
    orch._run_once()
    assert sink.delivered == ["url-a"]

    # Poll 2 (different id, different source, same underlying event — the bug
    # scenario): should be suppressed by story_key, not delivered again.
    item_b = NewsItem(id="url-b", source="finviz_stocks",
                       headline="Reddit shares surge on S&P 500 inclusion.",
                       url="url-b", ticker="RDDT",
                       meta={"story_key": "rddt-sp500-inclusion"})
    orch2 = _make_orchestrator(_FakeSource("finviz_stocks", [item_b]), store, sink)
    orch2._run_once()

    assert sink.delivered == ["url-a"]  # unchanged — duplicate suppressed
    assert store.has_seen("url-b")      # still marked seen so it won't retry forever


def test_different_story_keys_both_delivered(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    sink = _RecordingSink("discord_news")

    item_a = NewsItem(id="url-a", source="finviz_market", headline="A",
                       url="url-a", meta={"story_key": "rddt-sp500-inclusion"})
    item_b = NewsItem(id="url-b", source="finviz_stocks", headline="B",
                       url="url-b", meta={"story_key": "nvda-earnings-beat"})

    orch = _make_orchestrator(_FakeSource("finviz_market", [item_a, item_b]),
                              store, sink)
    orch._run_once()

    assert sink.delivered == ["url-a", "url-b"]


class _FailingSink(Sink):
    """Simulates a webhook outage: accepts everything, delivers nothing."""
    def __init__(self, name):
        self.name = name

    def accepts(self, item):
        return True

    def deliver(self, item):
        return False


def test_failed_delivery_does_not_poison_story_cache(tmp_path):
    """A story that never actually reached Discord (sink down) must not be
    treated as 'already delivered' — a later poll with a working sink should
    still be able to deliver the same real-world event."""
    store = SeenStore(str(tmp_path / "seen.db"))
    failing_sink = _FailingSink("discord_news")

    item_a = NewsItem(id="url-a", source="finviz_market", headline="A",
                       url="url-a", meta={"story_key": "rddt-sp500-inclusion"})
    orch = _make_orchestrator(_FakeSource("finviz_market", [item_a]), store,
                              failing_sink)
    orch._run_once()
    assert not store.has_recent_story("rddt-sp500-inclusion")

    # Same story, different id/source, now via a working sink — must deliver.
    working_sink = _RecordingSink("discord_news")
    item_b = NewsItem(id="url-b", source="finviz_stocks", headline="B",
                       url="url-b", meta={"story_key": "rddt-sp500-inclusion"})
    orch2 = _make_orchestrator(_FakeSource("finviz_stocks", [item_b]), store,
                               working_sink)
    orch2._run_once()
    assert working_sink.delivered == ["url-b"]


def test_missing_story_key_never_suppressed(tmp_path):
    store = SeenStore(str(tmp_path / "seen.db"))
    sink = _RecordingSink("discord_news")

    # No story_key at all (e.g. filter parse failure path never set one) —
    # must still deliver normally, never fail closed on this check.
    item_a = NewsItem(id="url-a", source="finviz_market", headline="A", url="url-a")
    item_b = NewsItem(id="url-b", source="finviz_market", headline="B", url="url-b")

    orch = _make_orchestrator(_FakeSource("finviz_market", [item_a, item_b]),
                              store, sink)
    orch._run_once()

    assert sink.delivered == ["url-a", "url-b"]
