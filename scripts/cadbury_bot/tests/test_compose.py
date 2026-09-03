import datetime

from scripts.cadbury_bot.composer import build_digest, compose
from scripts.cadbury_bot.config import CadburyConfig


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, text):
        self.choices = [_Choice(text)]


class _FakeCompletions:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self._text)


class _FakeClient:
    def __init__(self, text):
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(text)})()


class _BoomCompletions:
    @staticmethod
    def create(**kwargs):
        raise RuntimeError("model down")


class _BoomClient:
    chat = type("_Chat", (), {"completions": _BoomCompletions()})()


WHEN = datetime.datetime(2026, 8, 26, 16, 35)


def _cfg(**kw):
    base = dict(model="anthropic/claude-sonnet-4.5", max_tokens=2000, max_items=12,
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
    call = client.chat.completions.calls[0]
    assert call["model"] == "anthropic/claude-sonnet-4.5"
    system, user = call["messages"][0]["content"], call["messages"][1]["content"]
    assert "1. [$TSM]" in user
    assert "12 items" in system                  # {max_items} substituted
    assert "August 26 2026" in system            # date rendered


def test_compose_blank_model_uses_openrouter_auto():
    client = _FakeClient("OK")
    compose(_cfg(model=""), _items(), WHEN, client=client)
    assert client.chat.completions.calls[0]["model"] == "openrouter/auto"


def test_compose_empty_day_still_calls_model():
    client = _FakeClient("QUIET ONE.\n\nNothing crossed the bot's filter today")
    out = compose(_cfg(), [], WHEN, client=client)
    assert "Nothing crossed the bot's filter today" in out
    assert client.chat.completions.calls  # model was still consulted for opener/sign-off


def test_compose_falls_open_on_model_error():
    out = compose(_cfg(), _items(), WHEN, client=_BoomClient())
    assert "auto-summary unavailable" in out
    assert "$TSM" in out and "$AMAT" in out       # raw list preserved
    assert out.splitlines()[0].isupper()          # still has an ALL-CAPS header


def test_compose_tells_model_the_true_volume_when_capped():
    client = _FakeClient("OK")
    compose(_cfg(), _items(), WHEN, client=client, total=470)
    system = client.chat.completions.calls[0]["messages"][0]["content"]
    assert "470" in system and "2" in system      # 470 flagged, 2 shown


def test_fallback_caps_rows_and_notes_the_overflow():
    many = [dict(ticker=f"T{i}", urgency="low", headline=f"h{i}",
                 rewritten=f"$T{i} did a thing {i}") for i in range(40)]
    out = compose(_cfg(), many, WHEN, client=_BoomClient(), total=612)
    numbered = [ln for ln in out.splitlines() if ln[:2].strip().rstrip(".").isdigit()]
    assert len(numbered) <= 20                     # hard fallback ceiling
    assert "more headline" in out and "612" not in out  # notes hidden count, not raw total
    assert "and 592 more" in out                   # 612 - 20 shown


def test_compose_fallback_quiet_day():
    out = compose(_cfg(), [], WHEN, client=_BoomClient())
    assert "Nothing crossed the bot's filter today" in out


def test_compose_many_items_no_crash():
    many = [dict(ticker=f"T{i}", urgency="low", headline=f"h{i}", rewritten=f"$T{i} did a thing")
            for i in range(30)]
    out = compose(_cfg(), many, WHEN, client=_FakeClient("OK"))
    assert out == "OK"
