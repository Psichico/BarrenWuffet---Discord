from src.config import LLMConfig
from src.filter.claude_filter import ClaudeFilter
from src.models import NewsItem


class _Block:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self._text)


class _FakeClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def _items():
    return [
        NewsItem(id="1", source="finviz_news", headline="NVDA beats earnings", url="u1"),
        NewsItem(id="2", source="finviz_news", headline="Minor insider sale", url="u2"),
    ]


def _cfg(rewrite=True):
    return LLMConfig(model="claude-haiku-4-5", batch_size=20, rewrite=rewrite,
                     interest_profile="p", urgency_guidance="g", rewrite_style="s")


def test_parses_keep_urgency_and_rewrite():
    resp = (
        '[{"index": 0, "keep": true, "urgency": "high", "reason": "earnings", '
        '"rewrite": "NVDA tops Q2 estimates."},'
        '{"index": 1, "keep": false, "urgency": "low", "reason": "routine", '
        '"rewrite": ""}]'
    )
    f = ClaudeFilter(_cfg(), client=_FakeClient(resp))
    items = f.process(_items())

    assert items[0].keep is True
    assert items[0].urgency == "high"
    assert items[0].rewritten == "NVDA tops Q2 estimates."
    assert items[1].keep is False
    assert items[1].urgency == "low"
    assert items[1].rewritten is None


def test_strips_markdown_fences():
    resp = '```json\n[{"index": 0, "keep": true, "urgency": "medium", "reason": "x", "rewrite": "y"}, {"index": 1, "keep": false, "urgency": "low", "reason": "z", "rewrite": ""}]\n```'
    f = ClaudeFilter(_cfg(), client=_FakeClient(resp))
    items = f.process(_items())
    assert items[0].urgency == "medium"


def test_invalid_urgency_coerced_to_low():
    resp = ('[{"index": 0, "keep": true, "urgency": "URGENT!!!", "reason": "x", "rewrite": "y"},'
            '{"index": 1, "keep": false, "urgency": "low", "reason": "z", "rewrite": ""}]')
    f = ClaudeFilter(_cfg(), client=_FakeClient(resp))
    items = f.process(_items())
    assert items[0].keep is True
    assert items[0].urgency == "low"  # coerced


def test_malformed_response_defaults_to_drop():
    f = ClaudeFilter(_cfg(), client=_FakeClient("not json at all"))
    items = f.process(_items())
    for item in items:
        assert item.keep is False
        assert item.reason == "filter_parse_error"
        assert item.urgency == "low"


def test_missing_index_defaults_that_item_to_drop():
    resp = '[{"index": 0, "keep": true, "urgency": "high", "reason": "x", "rewrite": "y"}]'
    f = ClaudeFilter(_cfg(), client=_FakeClient(resp))
    items = f.process(_items())
    assert items[0].keep is True
    assert items[1].keep is False
    assert items[1].reason == "filter_parse_error"


def test_rewrite_disabled_leaves_rewritten_none():
    resp = '[{"index": 0, "keep": true, "urgency": "high", "reason": "x", "rewrite": "should be ignored"}, {"index": 1, "keep": false, "urgency": "low", "reason": "z", "rewrite": ""}]'
    f = ClaudeFilter(_cfg(rewrite=False), client=_FakeClient(resp))
    items = f.process(_items())
    assert items[0].rewritten is None


def test_api_exception_never_crashes():
    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("api down")

    f = ClaudeFilter(_cfg(), client=Boom())
    items = f.process(_items())
    for item in items:
        assert item.keep is False
        assert item.reason == "filter_parse_error"


def test_disable_filtering_forces_keep_true(monkeypatch):
    monkeypatch.setenv("DISABLE_FILTERING", "true")
    resp = (
        '[{"index": 0, "keep": false, "urgency": "high", "reason": "earnings", '
        '"rewrite": "NVDA tops Q2 estimates."},'
        '{"index": 1, "keep": false, "urgency": "low", "reason": "routine", '
        '"rewrite": ""}]'
    )
    f = ClaudeFilter(_cfg(), client=_FakeClient(resp))
    items = f.process(_items())

    assert items[0].keep is True
    assert items[1].keep is True
