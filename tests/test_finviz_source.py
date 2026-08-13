import src.sources.finviz as finviz_mod
from src.sources.finviz import FinvizNewsSource

CSV_FIXTURE = (
    "Title,Source,Date,Url,Ticker\n"
    "NVDA beats on earnings,Bloomberg,2026-07-01 16:05:00,"
    "https://finviz.com/news/1,NVDA\n"
    "AMD unveils new GPU,Reuters,2026-07-01 09:00:00,"
    "https://finviz.com/news/2,AMD\n"
)


class _FakeResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def test_parses_csv_into_news_items(monkeypatch):
    monkeypatch.setenv("FINVIZ_AUTH_TOKEN", "tok")
    monkeypatch.setattr(finviz_mod.requests, "get",
                        lambda *a, **k: _FakeResp(CSV_FIXTURE))
    # No sleeping between requests in tests.
    monkeypatch.setattr(finviz_mod.time, "sleep", lambda *_: None)

    # Single-ticker watchlist so we hit the export path once.
    src = FinvizNewsSource(watchlist=["NVDA"], max_items_per_fetch=50)
    items = src.fetch()

    assert len(items) == 2
    first = items[0]
    assert first.headline == "NVDA beats on earnings"
    assert first.url == "https://finviz.com/news/1"
    assert first.id == first.url          # id is the stable URL
    assert first.source == "finviz_news"
    assert first.ticker == "NVDA"
    assert first.publisher == "Bloomberg"
    assert first.published_at is not None


def test_missing_token_returns_empty(monkeypatch):
    monkeypatch.delenv("FINVIZ_AUTH_TOKEN", raising=False)
    src = FinvizNewsSource(watchlist=["NVDA"])
    assert src.fetch() == []


def test_auth_failure_returns_empty(monkeypatch):
    monkeypatch.setenv("FINVIZ_AUTH_TOKEN", "tok")
    monkeypatch.setattr(finviz_mod.requests, "get",
                        lambda *a, **k: _FakeResp("nope", status_code=403))
    monkeypatch.setattr(finviz_mod.time, "sleep", lambda *_: None)
    src = FinvizNewsSource(watchlist=["NVDA"])
    assert src.fetch() == []


def test_html_login_page_treated_as_auth_failure(monkeypatch):
    monkeypatch.setenv("FINVIZ_AUTH_TOKEN", "tok")
    monkeypatch.setattr(finviz_mod.requests, "get",
                        lambda *a, **k: _FakeResp("<!DOCTYPE html><html>login</html>"))
    monkeypatch.setattr(finviz_mod.time, "sleep", lambda *_: None)
    src = FinvizNewsSource(watchlist=["NVDA"])
    assert src.fetch() == []


def test_pid_omitted_when_watchlist_only_unset(monkeypatch):
    monkeypatch.setenv("FINVIZ_AUTH_TOKEN", "tok")
    monkeypatch.delenv("WATCHLIST_ONLY", raising=False)
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResp(CSV_FIXTURE)

    monkeypatch.setattr(finviz_mod.requests, "get", fake_get)
    monkeypatch.setattr(finviz_mod.time, "sleep", lambda *_: None)

    src = FinvizNewsSource(pid="12345", watchlist=[], max_items_per_fetch=50)
    src.fetch()

    assert "pid" not in captured["params"]


def test_pid_included_when_watchlist_only_enabled(monkeypatch):
    monkeypatch.setenv("FINVIZ_AUTH_TOKEN", "tok")
    monkeypatch.setenv("WATCHLIST_ONLY", "1")
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeResp(CSV_FIXTURE)

    monkeypatch.setattr(finviz_mod.requests, "get", fake_get)
    monkeypatch.setattr(finviz_mod.time, "sleep", lambda *_: None)

    src = FinvizNewsSource(pid="12345", watchlist=[], max_items_per_fetch=50)
    src.fetch()

    assert captured["params"]["pid"] == "12345"


def test_network_error_returns_empty(monkeypatch):
    monkeypatch.setenv("FINVIZ_AUTH_TOKEN", "tok")

    def boom(*a, **k):
        raise finviz_mod.requests.RequestException("down")

    monkeypatch.setattr(finviz_mod.requests, "get", boom)
    monkeypatch.setattr(finviz_mod.time, "sleep", lambda *_: None)
    src = FinvizNewsSource(watchlist=["NVDA"])
    assert src.fetch() == []
