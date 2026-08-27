import datetime

from scripts.cadbury_bot.bot import CadburyBot
from scripts.cadbury_bot.config import CadburyConfig


class _FakeStore:
    def __init__(self, items=None):
        self._items = items or []
        self.runs = set()
        self.pruned = 0

    def kept_items_for_day(self, day):
        return list(self._items)

    def has_run(self, day):
        return day in self.runs

    def mark_run(self, day):
        self.runs.add(day)

    def prune(self, *a, **k):
        self.pruned += 1


class _FakeMailer:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))
        return self.ok


def _compose_stub(cfg, items, when, client=None):
    return f"BODY for {when.date().isoformat()} with {len(items)} items"


def _cfg(**kw):
    base = dict(enabled=True, timezone="America/New_York", send_hour=16,
                send_minute=30, subject_prefix="Market Recap")
    base.update(kw)
    return CadburyConfig(**base)


def _bot(cfg=None, store=None, mailer=None):
    cfg = cfg or _cfg()
    store = store or _FakeStore(items=[{"ticker": "NVDA"}])
    mailer = mailer or _FakeMailer()
    bot = CadburyBot(cfg=cfg, store=store, recorder=None,
                     composer=_compose_stub, mailer=mailer)
    return bot, store, mailer


WEEKDAY_LATE = datetime.datetime(2026, 8, 26, 16, 35)   # Wed 16:35
WEEKDAY_EARLY = datetime.datetime(2026, 8, 26, 16, 15)  # Wed 16:15
SATURDAY = datetime.datetime(2026, 8, 29, 17, 0)        # Sat
SUNDAY = datetime.datetime(2026, 8, 30, 17, 0)          # Sun


def test_fires_weekday_after_target():
    bot, store, mailer = _bot()
    bot.tick(now=WEEKDAY_LATE)
    assert len(mailer.sent) == 1
    assert mailer.sent[0][0] == "Market Recap — Wed, Aug 26 2026"
    assert "2026-08-26" in store.runs
    assert store.pruned == 1


def test_not_before_target():
    bot, store, mailer = _bot()
    bot.tick(now=WEEKDAY_EARLY)
    assert mailer.sent == []
    assert store.runs == set()


def test_not_on_weekend():
    for when in (SATURDAY, SUNDAY):
        bot, store, mailer = _bot()
        bot.tick(now=when)
        assert mailer.sent == []


def test_only_once_per_day():
    bot, store, mailer = _bot()
    bot.tick(now=WEEKDAY_LATE)
    bot.tick(now=datetime.datetime(2026, 8, 26, 18, 0))
    bot.tick(now=datetime.datetime(2026, 8, 26, 23, 59))
    assert len(mailer.sent) == 1


def test_send_failure_does_not_mark_run():
    bot, store, mailer = _bot(mailer=_FakeMailer(ok=False))
    bot.tick(now=WEEKDAY_LATE)
    assert mailer.sent  # attempted
    assert store.runs == set()  # not marked -> retried next loop


def test_disabled_does_nothing():
    bot, store, mailer = _bot(cfg=_cfg(enabled=False))
    bot.tick(now=WEEKDAY_LATE)
    assert mailer.sent == []


def test_run_now_ignores_time_and_weekend():
    bot, store, mailer = _bot()
    assert bot.run_now(day="2026-08-29") is True   # a Saturday, forced by CLI
    assert len(mailer.sent) == 1


def test_run_now_respects_marker_without_force():
    store = _FakeStore(items=[{"ticker": "NVDA"}])
    store.runs.add("2026-08-26")
    bot, _, mailer = _bot(store=store)
    assert bot.run_now(day="2026-08-26") is False
    assert mailer.sent == []
    assert bot.run_now(day="2026-08-26", force=True) is True
