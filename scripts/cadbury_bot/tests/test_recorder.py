from scripts.cadbury_bot.recorder import KeptItemRecorder
from scripts.cadbury_bot.store import CadburyStore
from src.models import NewsItem


def _kept(**kw):
    base = dict(id="1", source="finviz_market", headline="NVDA beats Q2",
                url="http://x", ticker="NVDA", keep=True, urgency="high",
                rewritten="$NVDA tops Q2 estimates, guides Q3 above consensus.")
    base.update(kw)
    return NewsItem(**base)


def test_record_writes_one_row_with_et_day(tmp_path):
    store = CadburyStore(str(tmp_path / "seen.db"))
    rec = KeptItemRecorder(store, timezone="America/New_York")

    rec.record(_kept())

    cur = store._conn.execute(
        "SELECT day, ticker, rewritten, urgency FROM cadbury_kept_items")
    got = cur.fetchall()
    assert len(got) == 1
    day, ticker, rewritten, urgency = got[0]
    assert len(day) == 10 and day[4] == "-"  # YYYY-MM-DD
    assert ticker == "NVDA"
    assert rewritten.startswith("$NVDA tops")
    assert urgency == "high"


def test_record_idempotent_on_id(tmp_path):
    store = CadburyStore(str(tmp_path / "seen.db"))
    rec = KeptItemRecorder(store)
    rec.record(_kept(headline="first"))
    rec.record(_kept(headline="second"))
    cur = store._conn.execute("SELECT COUNT(*) FROM cadbury_kept_items")
    assert cur.fetchone()[0] == 1


def test_record_ignores_non_kept(tmp_path):
    store = CadburyStore(str(tmp_path / "seen.db"))
    rec = KeptItemRecorder(store)
    rec.record(_kept(keep=False))
    cur = store._conn.execute("SELECT COUNT(*) FROM cadbury_kept_items")
    assert cur.fetchone()[0] == 0


def test_record_swallows_store_errors(tmp_path):
    class _BoomStore:
        def record(self, **kw):
            raise RuntimeError("db is on fire")

    rec = KeptItemRecorder(_BoomStore())
    rec.record(_kept())  # must not raise
