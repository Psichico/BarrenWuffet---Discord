"""End-to-end routing: high -> both sinks, low/medium -> news only."""

from src.models import NewsItem
from src.sinks.base import Sink


class _RecordingSink(Sink):
    def __init__(self, name, min_urgency):
        self.name = name
        self.min_urgency = min_urgency
        self.delivered = []

    _RANK = {"low": 0, "medium": 1, "high": 2}

    def accepts(self, item):
        return self._RANK[item.urgency] >= self._RANK[self.min_urgency]

    def deliver(self, item):
        self.delivered.append(item.id)
        return True


def _route(item, sinks):
    for sink in sinks:
        if sink.accepts(item):
            sink.deliver(item)


def test_high_goes_to_both_low_goes_to_news_only():
    news = _RecordingSink("discord_news", "low")
    urgent = _RecordingSink("discord_urgent", "high")
    sinks = [news, urgent]

    high = NewsItem(id="h", source="s", headline="h", url="u", keep=True, urgency="high")
    low = NewsItem(id="l", source="s", headline="l", url="u", keep=True, urgency="low")
    medium = NewsItem(id="m", source="s", headline="m", url="u", keep=True, urgency="medium")

    for item in (high, low, medium):
        _route(item, sinks)

    assert news.delivered == ["h", "l", "m"]   # news gets everything kept
    assert urgent.delivered == ["h"]           # urgent gets only high
