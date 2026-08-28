"""Standalone end-to-end dry run for cadbury_bot — no secrets, no network.

    python scripts/cadbury_bot/verify_cadbury.py

Drives the REAL store / recorder / composer / scheduler with FAKE external APIs
(Anthropic + SMTP) and asserts the full behaviour. Mirrors
``scripts/verify_flow.py``. Exits 0 on success, 1 on any failed check.
"""

from __future__ import annotations

import datetime
import os
import sys
import tempfile
from pathlib import Path

# Make `scripts.cadbury_bot` / `src` importable when run as a script from the repo root.
sys.path.insert(0, os.path.abspath(os.getcwd()))

from scripts.cadbury_bot.bot import CadburyBot
from scripts.cadbury_bot.composer import compose
from scripts.cadbury_bot.config import CadburyConfig
from scripts.cadbury_bot.recorder import KeptItemRecorder
from scripts.cadbury_bot.store import CadburyStore
from src.models import NewsItem

_checks = 0
_fails = 0


def check(label, cond):
    global _checks, _fails
    _checks += 1
    status = "ok  " if cond else "FAIL"
    if not cond:
        _fails += 1
    print(f"  [{status}] {label}")


class _Block:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeClient:
    """Echoes the digest back inside a recap-shaped envelope."""

    def __init__(self):
        self.calls = []

    class _M:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.calls.append(kwargs)
            digest = kwargs["messages"][0]["content"]
            return _Resp("A LOT HAPPENED TODAY.\n\nHere's the recap, in the "
                         "order it crossed today:\n" + digest +
                         "\n\nWALL STREET NEVER SLEEPS.")

    @property
    def messages(self):
        return _FakeClient._M(self)


class _BoomClient:
    class messages:
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("model unavailable")


class _FakeMailer:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))
        return self.ok


def _cfg(**kw):
    base = dict(enabled=True, timezone="America/New_York", send_hour=16,
                send_minute=30, subject_prefix="Market Recap",
                recap_intro="INTRO", recap_style="STYLE up to {max_items}")
    base.update(kw)
    return CadburyConfig(**base)


DAY = "2026-08-26"
WED_1635 = datetime.datetime(2026, 8, 26, 16, 35)
WED_1615 = datetime.datetime(2026, 8, 26, 16, 15)
SAT_1700 = datetime.datetime(2026, 8, 29, 17, 0)


def _seed(store, day=DAY):
    for i, (tk, hl) in enumerate([
        ("NVDA", "Nvidia guides Q3 above consensus"),
        ("MU", "Micron raises HBM guidance"),
        ("AMAT", "Applied Materials falls on export review"),
    ], 1):
        store.record(item_id=f"{day}-{i}", day=day, ticker=tk, headline=hl,
                     rewritten=f"${tk} — {hl}.", urgency="high", url=f"http://x/{i}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    db = str(tmp / "seen.db")

    print("1. recorder writes kept items in order")
    store = CadburyStore(db)
    rec = KeptItemRecorder(store, "America/New_York")
    # feed through the real recorder using a NewsItem, day pinned via monkey-free path
    for i, (tk, hl) in enumerate([("TSM", "TSMC Aug sales +32%"),
                                  ("ADI", "ADI to buy analog-power startup")], 1):
        it = NewsItem(id=f"rec-{i}", source="fake", headline=hl, url=f"u{i}",
                      ticker=tk, keep=True, urgency="medium",
                      rewritten=f"${tk} — {hl}.")
        rec.record(it)
    from scripts.cadbury_bot.timeutil import et_today
    rows = store.kept_items_for_day(et_today("America/New_York"))
    check("two rows recorded", len(rows) == 2)
    check("insertion order preserved", [r["id"] for r in rows] == ["rec-1", "rec-2"])
    check("idempotent re-record", (rec.record(NewsItem(id="rec-1", source="f",
          headline="dup", url="u", ticker="TSM", keep=True)),
          len(store.kept_items_for_day(et_today("America/New_York"))) == 2)[1])
    store.close()

    print("2. schedule gate")
    store = CadburyStore(db)
    _seed(store)
    client = _FakeClient()
    mailer = _FakeMailer()
    bot = CadburyBot(cfg=_cfg(), store=store, recorder=None, composer=compose,
                     mailer=mailer, client=client)
    bot.tick(now=WED_1615)
    check("before 16:30 -> no send", mailer.sent == [])
    bot.tick(now=SAT_1700)
    check("weekend -> no send", mailer.sent == [])
    bot.tick(now=WED_1635)
    check("weekday 16:35 -> one send", len(mailer.sent) == 1)
    check("subject formatted", mailer.sent[0][0] == "Market Recap — Wed, Aug 26 2026")
    body = mailer.sent[0][1]
    check("recap references seeded tickers in order",
          body.find("$NVDA") < body.find("$MU") < body.find("$AMAT"))
    check("model prompt had {max_items} substituted", "up to 12" in client.calls[0]["system"])
    bot.tick(now=datetime.datetime(2026, 8, 26, 18, 0))
    check("second tick same day -> still one send", len(mailer.sent) == 1)
    store.close()

    print("3. send failure is retried (run marker not set)")
    store = CadburyStore(db)
    _seed(store, day="2026-08-27")
    failing = _FakeMailer(ok=False)
    bot = CadburyBot(cfg=_cfg(), store=store, recorder=None, composer=compose,
                     mailer=failing, client=_FakeClient())
    bot.tick(now=datetime.datetime(2026, 8, 27, 16, 40))
    check("send attempted", len(failing.sent) == 1)
    check("run NOT marked after failed send", not store.has_run("2026-08-27"))
    ok_mailer = _FakeMailer(ok=True)
    bot.mailer = ok_mailer
    bot.tick(now=datetime.datetime(2026, 8, 27, 16, 45))
    check("retried on next tick", len(ok_mailer.sent) == 1)
    store.close()

    print("4. empty day still emails a 'quiet day' recap")
    store = CadburyStore(db)
    quiet_client = _FakeClient()
    mailer = _FakeMailer()
    bot = CadburyBot(cfg=_cfg(), store=store, recorder=None, composer=compose,
                     mailer=mailer, client=quiet_client)
    bot.tick(now=datetime.datetime(2026, 8, 28, 16, 35))
    check("email sent on empty day", len(mailer.sent) == 1)
    check("model was still consulted", len(quiet_client.calls) == 1)
    store.close()

    print("5. model failure -> fail-open raw list")
    store = CadburyStore(db)
    _seed(store, day="2026-08-31")
    mailer = _FakeMailer()
    bot = CadburyBot(cfg=_cfg(), store=store, recorder=None, composer=compose,
                     mailer=mailer, client=_BoomClient())
    bot.tick(now=datetime.datetime(2026, 8, 31, 16, 35))
    check("email still sent when model fails", len(mailer.sent) == 1)
    check("body is the raw-list fallback",
          "auto-summary unavailable" in mailer.sent[0][1])
    check("fallback still lists the tickers", "$NVDA" in mailer.sent[0][1])
    store.close()

    print("6. a 400-headline day is capped, not dumped")
    store = CadburyStore(db)
    for i in range(1, 420):
        urg = "high" if i % 50 == 0 else "low"
        store.record(item_id=f"flood-{i}", day="2026-09-01", ticker=f"T{i}",
                     headline=f"headline number {i}", rewritten=f"$T{i} did thing {i}.",
                     urgency=urg, url=f"http://x/{i}")
    client = _FakeClient()
    mailer = _FakeMailer()
    bot = CadburyBot(cfg=_cfg(digest_max_items=40), store=store, recorder=None,
                     composer=compose, mailer=mailer, client=client)
    bot.tick(now=datetime.datetime(2026, 9, 1, 16, 35))
    check("email sent", len(mailer.sent) == 1)
    digest_sent = client.calls[0]["messages"][0]["content"]
    check("at most 40 items reach the model", digest_sent.count("\n") <= 39)
    check("model told the true volume (419)", "419" in client.calls[0]["system"])
    check("all 8 high-urgency items kept in the cap",
          all(f"$T{n}" in digest_sent for n in (50, 100, 150, 200, 250, 300, 350, 400)))
    store.close()

    print(f"\n{_checks - _fails}/{_checks} checks passed.")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
