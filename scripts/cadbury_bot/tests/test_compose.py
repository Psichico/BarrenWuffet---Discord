import datetime

from scripts.cadbury_bot.composer import build_digest, compose
from scripts.cadbury_bot.config import CadburyConfig


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


class _BoomClient:
    class messages:
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("model down")


WHEN = datetime.datetime(2026, 8, 26, 16, 35)


def _cfg(**kw):
    base = dict(model="claude-sonnet-5", max_tokens=2000, max_items=12,
                recap_intro="INTRO", recap_style="STYLE up to {max_items} items")
    base.update(kw)
    return CadburyConfig(**base)


def _items():
    return [
        {"ticker": "TSM", "urgency": "high", "headline": "TSMC Aug sales +32%",
         "rewritten": "$TSM August sales up 32% YoY."},
        {"ticker": "AMAT", "urgency": "medium", "headline": "export review",
         "rewritten": "$AMAT falls on export-rule review."},
    ]


def test_build_digest_keeps_order_and_numbers():
    d = build_digest(_items())
    assert d.splitlines()[0].startswith("1. [$TSM] high — $TSM August sales")
    assert d.splitlines()[1].startswith("2. [$AMAT] medium —")


def test_build_digest_empty():
    assert "no items" in build_digest([]).lower()


def test_compose_passes_digest_and_returns_model_text():
    client = _FakeClient("RECAP BODY GOES HERE")
    out = compose(_cfg(), _items(), WHEN, client=client)
    assert out == "RECAP BODY GOES HERE"
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert "1. [$TSM]" in call["messages"][0]["content"]
    assert "12 items" in call["system"]          # {max_items} substituted
    assert "August 26 2026" in call["system"]    # date rendered


def test_compose_empty_day_still_calls_model():
    client = _FakeClient("QUIET ONE.\n\nNothing crossed the bot's filter today")
    out = compose(_cfg(), [], WHEN, client=client)
    assert "Nothing crossed the bot's filter today" in out
    assert client.messages.calls  # model was still consulted for opener/sign-off


def test_compose_falls_open_on_model_error():
    out = compose(_cfg(), _items(), WHEN, client=_BoomClient())
    assert "auto-summary unavailable" in out
    assert "$TSM" in out and "$AMAT" in out       # raw list preserved
    assert out.splitlines()[0].isupper()          # still has an ALL-CAPS header


def test_compose_fallback_quiet_day():
    out = compose(_cfg(), [], WHEN, client=_BoomClient())
    assert "Nothing crossed the bot's filter today" in out


def test_compose_many_items_no_crash():
    many = [dict(ticker=f"T{i}", urgency="low", headline=f"h{i}", rewritten=f"$T{i} did a thing")
            for i in range(30)]
    out = compose(_cfg(), many, WHEN, client=_FakeClient("OK"))
    assert out == "OK"
