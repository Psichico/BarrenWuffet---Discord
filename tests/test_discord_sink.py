import src.sinks.discord as discord_mod
from src.models import NewsItem
from src.sinks.discord import DiscordSink


class _FakeResp:
    def __init__(self, status_code=204, json_body=None):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = ""

    def json(self):
        return self._json


def _item(urgency="high"):
    return NewsItem(id="1", source="finviz_news", headline="NVDA beats",
                    url="http://x", ticker="NVDA", publisher="Bloomberg",
                    keep=True, urgency=urgency, rewritten="NVDA tops estimates.")


def test_accepts_routes_by_min_urgency(monkeypatch):
    monkeypatch.setenv("W", "http://hook")
    news = DiscordSink("discord_news", "W", min_urgency="low")
    urgent = DiscordSink("discord_urgent", "W", min_urgency="high")

    assert news.accepts(_item("low")) is True
    assert news.accepts(_item("high")) is True
    assert urgent.accepts(_item("low")) is False
    assert urgent.accepts(_item("medium")) is False
    assert urgent.accepts(_item("high")) is True


def test_accepts_filters_by_allowed_sources(monkeypatch):
    monkeypatch.setenv("W", "http://hook")
    sink = DiscordSink(
        "discord_market",
        "W",
        allowed_sources=["finviz_market"]
    )
    item_market = NewsItem(id="1", source="finviz_market", headline="A", url="h", keep=True)
    item_stocks = NewsItem(id="2", source="finviz_stocks", headline="B", url="h", keep=True)

    assert sink.accepts(item_market) is True
    assert sink.accepts(item_stocks) is False


def test_deliver_posts_and_returns_true(monkeypatch):
    monkeypatch.setenv("W", "http://hook")
    posted = {}

    def fake_post(url, json=None, timeout=None):
        posted["url"] = url
        posted["json"] = json
        return _FakeResp(204)

    monkeypatch.setattr(discord_mod.requests, "post", fake_post)
    sink = DiscordSink("discord_news", "W", rate_limit_sleep=0)
    assert sink.deliver(_item()) is True

    embed = posted["json"]["embeds"][0]
    assert "NVDA" in embed["title"]
    assert embed["description"] == "NVDA tops estimates."   # uses rewritten
    assert embed["url"] == "http://x"
    assert "Bloomberg" in embed["footer"]["text"]


def test_deliver_handles_429_then_retries(monkeypatch):
    monkeypatch.setenv("W", "http://hook")
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(429, {"retry_after": 0.01})
        return _FakeResp(204)

    monkeypatch.setattr(discord_mod.requests, "post", fake_post)
    monkeypatch.setattr(discord_mod.time, "sleep", lambda *_: None)
    sink = DiscordSink("discord_news", "W", rate_limit_sleep=0)
    assert sink.deliver(_item()) is True
    assert calls["n"] == 2


def test_deliver_returns_false_on_error(monkeypatch):
    monkeypatch.setenv("W", "http://hook")
    monkeypatch.setattr(discord_mod.requests, "post",
                        lambda *a, **k: _FakeResp(500))
    sink = DiscordSink("discord_news", "W", rate_limit_sleep=0)
    assert sink.deliver(_item()) is False


def test_missing_webhook_returns_false(monkeypatch):
    monkeypatch.delenv("MISSING", raising=False)
    sink = DiscordSink("discord_news", "MISSING", rate_limit_sleep=0)
    assert sink.deliver(_item()) is False
