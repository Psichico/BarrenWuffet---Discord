"""End-to-end: real Orchestrator + real stores, fake external APIs.

Proves the two generic hooks wire cadbury_bot in: a poll records kept items,
and a later tick emails exactly one recap referencing them.
"""

import datetime

import scripts.cadbury_bot.bot as bot_mod
import scripts.cadbury_bot.recorder as recorder_mod
from scripts.cadbury_bot.bot import CadburyBot
from scripts.cadbury_bot.composer import compose
from scripts.cadbury_bot.config import CadburyConfig
from scripts.cadbury_bot.recorder import KeptItemRecorder
from scripts.cadbury_bot.store import CadburyStore
from src.dedupe import SeenStore
from src.models import NewsItem
from src.orchestrator import Orchestrator
from src.sinks.base import Sink
from src.sources.base import Source

FIXED_DAY = "2026-08-26"                                  # a Wednesday
FIXED_NOW = datetime.datetime(2026, 8, 26, 16, 35)       # Wed 16:35, naive


class _FakeSource(Source):
    name = "fake"

    def __init__(self, items):
        self._items = items

    def fetch(self):
        return list(self._items)


class _KeepAllFilter:
    def process(self, items):
        for it in items:
            it.keep = True
            it.urgency = "high"
            it.rewritten = f"${it.ticker} — {it.headline}"
        return items


class _RecordingSink(Sink):
    name = "rec"

    def __init__(self):
        self.delivered = []

    def deliver(self, item):
        self.delivered.append(item.id)
        return True


def _oai_resp(text):
    """OpenAI-compatible response: .choices[0].message.content"""
    msg = type("_Msg", (), {"content": text})()
    return type("_Resp", (), {"choices": [type("_Choice", (), {"message": msg})()]})()


class _FakeClient:
    class chat:
        class completions:
            calls = []

            @classmethod
            def create(cls, **kwargs):
                cls.calls.append(kwargs)
                digest = kwargs["messages"][-1]["content"]
                return _oai_resp("BIG DAY.\n\nHere's the recap, in the order it "
                                 "crossed today:\n" + digest + "\n\nSEE YOU TOMORROW.")


class _FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))
        return True


def _cfg(**kw):
    base = dict(enabled=True, timezone="America/New_York", send_hour=16,
                send_minute=30, recap_intro="INTRO",
                recap_style="STYLE {max_items}")
    base.update(kw)
    return CadburyConfig(**base)


def _news():
    return [
        NewsItem(id="u1", source="fake", headline="NVDA beats Q2", url="u1", ticker="NVDA"),
        NewsItem(id="u2", source="fake", headline="AMAT export review", url="u2", ticker="AMAT"),
    ]


def test_poll_records_then_tick_emails_once(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder_mod, "et_today", lambda tz: FIXED_DAY)
    monkeypatch.setattr(bot_mod, "et_now", lambda tz: FIXED_NOW)

    db = str(tmp_path / "seen.db")
    seen = SeenStore(db)
    cad_store = CadburyStore(db)
    recorder = KeptItemRecorder(cad_store, timezone="America/New_York")
    mailer = _FakeMailer()
    _FakeClient.chat.completions.calls = []
    bot = CadburyBot(cfg=_cfg(), store=cad_store, recorder=recorder,
                     composer=compose, mailer=mailer, client=_FakeClient())

    orch = Orchestrator(
        sources=[_FakeSource(_news())],
        filter_=_KeepAllFilter(),
        sinks=[_RecordingSink()],
        store=seen,
        poll_interval_seconds=300,
        batch_size=20,
        on_item_kept=recorder.record,
        post_iteration_hook=bot.tick,
    )

    orch._run_once()

    rows = cad_store.kept_items_for_day(FIXED_DAY)
    assert [r["id"] for r in rows] == ["u1", "u2"]

    # the post-iteration hook drives this in production; call it directly here
    bot.tick()
    bot.tick()  # idempotent same day

    assert len(mailer.sent) == 1
    subject, body = mailer.sent[0]
    assert subject == "Market Recap — Wed, Aug 26 2026"
    assert "$NVDA" in body and "$AMAT" in body


def test_raising_tick_does_not_break_run_forever(tmp_path):
    seen = SeenStore(str(tmp_path / "seen.db"))

    class _BoomOnce:
        def __init__(self):
            self.calls = 0

        def __call__(self):
            self.calls += 1
            raise RuntimeError("kaboom")

    boom = _BoomOnce()
    orch = Orchestrator(
        sources=[_FakeSource([])],
        filter_=_KeepAllFilter(),
        sinks=[],
        store=seen,
        poll_interval_seconds=300,
        batch_size=20,
        post_iteration_hook=boom,
    )

    def _stop_after(*_a, **_k):
        orch._stop = True

    orch._sleep = _stop_after
    orch.run_forever()

    assert boom.calls == 1  # hook ran, exception swallowed, loop exited cleanly
