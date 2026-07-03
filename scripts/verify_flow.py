"""End-to-end verification of the whole pipeline using FAKES for the external
APIs (Finviz, Claude, Discord) — no secrets required. Exercises the REAL
orchestrator, dedupe, filter, and sink classes.

Run from the project root:

    python scripts/verify_flow.py

Exits 0 if all checks pass, 1 otherwise. This is a fast confidence check that the
wiring is intact after any change — complements the pytest suite.
"""

import os
import sys
import tempfile

# Make `src` importable when run from the project root.
sys.path.insert(0, os.path.abspath(os.getcwd()))

from src.config import LLMConfig
from src.dedupe import SeenStore
from src.filter.claude_filter import ClaudeFilter
from src.models import NewsItem
from src.orchestrator import Orchestrator
from src.sinks.discord import DiscordSink
from src.sources.base import Source
import src.sinks.discord as discord_mod

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(name, cond):
    results.append(cond)
    print(f"  [{PASS if cond else FAIL}] {name}")


class FakeFinvizSource(Source):
    """Emits a fixed batch, like FinvizNewsSource would after parsing CSV."""
    name = "finviz_news"

    def fetch(self):
        return [
            NewsItem(id="url://nvda-earn", source="finviz_news",
                     headline="NVDA reports record Q2 earnings, beats estimates",
                     url="url://nvda-earn", ticker="NVDA", publisher="Bloomberg"),
            NewsItem(id="url://amd-launch", source="finviz_news",
                     headline="AMD launches new MI400 datacenter GPU",
                     url="url://amd-launch", ticker="AMD", publisher="Reuters"),
            NewsItem(id="url://intc-insider", source="finviz_news",
                     headline="Intel VP sells 200 shares in routine transaction",
                     url="url://intc-insider", ticker="INTC", publisher="SEC"),
        ]


class FakeClaudeClient:
    """Mimics anthropic client: .messages.create() -> resp.content[].text"""
    class messages:
        calls = 0

        @classmethod
        def create(cls, **kwargs):
            cls.calls += 1
            text = (
                '[{"index": 0, "keep": true, "urgency": "high", '
                '"reason": "earnings beat", "rewrite": "NVDA tops Q2 estimates with record earnings."},'
                '{"index": 1, "keep": true, "urgency": "medium", '
                '"reason": "product launch", "rewrite": "AMD debuts MI400 datacenter GPU."},'
                '{"index": 2, "keep": false, "urgency": "low", '
                '"reason": "routine insider sale", "rewrite": ""}]'
            )

            class Block:
                def __init__(self, t): self.text = t

            class Resp:
                def __init__(self, t): self.content = [Block(t)]

            return Resp(text)


posted = []  # (webhook_url, payload)


def fake_post(url, json=None, timeout=None):
    posted.append((url, json))

    class Resp:
        status_code = 204
        text = ""
        def json(self): return {}
    return Resp()


def _llm():
    return LLMConfig(model="claude-haiku-4-5", batch_size=20, rewrite=True,
                     interest_profile="p", urgency_guidance="g", rewrite_style="s")


def _sinks():
    news = DiscordSink("discord_news", "DISCORD_NEWS_WEBHOOK_URL",
                       min_urgency="low", rate_limit_sleep=0)
    urgent = DiscordSink("discord_urgent", "DISCORD_URGENT_WEBHOOK_URL",
                         min_urgency="high", rate_limit_sleep=0)
    return news, urgent


def _tmp_store():
    return SeenStore(os.path.join(tempfile.mkdtemp(), "seen.db"))


def main():
    discord_mod.requests.post = fake_post
    os.environ["DISCORD_NEWS_WEBHOOK_URL"] = "http://hook/news"
    os.environ["DISCORD_URGENT_WEBHOOK_URL"] = "http://hook/urgent"

    print("\n=== 1. Single poll: fetch -> dedupe -> filter -> route -> deliver ===")
    fake_client = FakeClaudeClient()
    fake_client.messages.calls = 0
    news_sink, urgent_sink = _sinks()
    db_path = os.path.join(tempfile.mkdtemp(), "seen.db")
    store = SeenStore(db_path)
    orch = Orchestrator([FakeFinvizSource()], ClaudeFilter(_llm(), client=fake_client),
                        [news_sink, urgent_sink], store, poll_interval_seconds=1,
                        batch_size=20, warm_start=False)
    posted.clear()
    orch._run_once()

    news_posts = [p for p in posted if p[0] == "http://hook/news"]
    urgent_posts = [p for p in posted if p[0] == "http://hook/urgent"]
    news_titles = [p[1]["embeds"][0]["title"] for p in news_posts]
    urgent_titles = [p[1]["embeds"][0]["title"] for p in urgent_posts]

    check("Claude filter called once (batched)", fake_client.messages.calls == 1)
    check("news channel got 2 kept items (high+medium)", len(news_posts) == 2)
    check("urgent channel got 1 item (high only)", len(urgent_posts) == 1)
    check("high-urgency NVDA in BOTH channels",
          any("NVDA" in t for t in news_titles) and any("NVDA" in t for t in urgent_titles))
    check("medium AMD in news only",
          any("AMD" in t for t in news_titles) and not any("AMD" in t for t in urgent_titles))
    check("dropped INTC insider delivered NOWHERE",
          not any("INTC" in t for t in news_titles + urgent_titles))
    nvda_post = next(p for p in news_posts if "NVDA" in p[1]["embeds"][0]["title"])
    check("rewritten text used as description",
          nvda_post[1]["embeds"][0]["description"] == "NVDA tops Q2 estimates with record earnings.")

    print("\n=== 2. Dedupe: a second identical poll delivers NOTHING ===")
    posted.clear()
    fake_client.messages.calls = 0
    orch._run_once()
    check("no new items -> filter NOT called", fake_client.messages.calls == 0)
    check("no duplicate deliveries", len(posted) == 0)

    print("\n=== 3. Restart safety: reopen store, same source, no re-alert ===")
    store.close()
    store2 = SeenStore(db_path)  # simulate restart with persistent disk
    orch2 = Orchestrator([FakeFinvizSource()], ClaudeFilter(_llm(), client=fake_client),
                         [news_sink, urgent_sink], store2, poll_interval_seconds=1,
                         batch_size=20, warm_start=False)
    posted.clear()
    orch2._run_once()
    check("persistent store -> no re-alert after restart", len(posted) == 0)

    print("\n=== 4. Warm start: empty store marks seen WITHOUT delivering ===")
    store3 = _tmp_store()
    orch3 = Orchestrator([FakeFinvizSource()], ClaudeFilter(_llm(), client=fake_client),
                         [news_sink, urgent_sink], store3, poll_interval_seconds=1,
                         batch_size=20, warm_start=True)
    posted.clear()
    orch3._warm_start()
    check("warm start delivered nothing", len(posted) == 0)
    check("warm start marked all 3 seen",
          store3.has_seen("url://nvda-earn") and store3.has_seen("url://amd-launch")
          and store3.has_seen("url://intc-insider"))
    posted.clear()
    orch3._run_once()
    check("poll after warm start delivers nothing (backlog suppressed)", len(posted) == 0)

    print("\n=== 5. Malformed Claude response never floods / never crashes ===")
    class BadClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                class Block: text = "this is not json"
                class Resp: content = [Block()]
                return Resp()
    store4 = _tmp_store()
    orch4 = Orchestrator([FakeFinvizSource()], ClaudeFilter(_llm(), client=BadClient()),
                         list(_sinks()), store4, poll_interval_seconds=1,
                         batch_size=20, warm_start=False)
    posted.clear()
    try:
        orch4._run_once()
        crashed = False
    except Exception:
        crashed = True
    check("malformed response did not crash the loop", not crashed)
    check("malformed response delivered NOTHING (no flood)", len(posted) == 0)

    print("\n=== 6. Source that raises does not kill the poll ===")
    class BoomSource(Source):
        name = "boom"
        def fetch(self): raise RuntimeError("network exploded")
    store5 = _tmp_store()
    orch5 = Orchestrator([BoomSource(), FakeFinvizSource()],
                         ClaudeFilter(_llm(), client=fake_client),
                         [news_sink], store5, poll_interval_seconds=1,
                         batch_size=20, warm_start=False)
    posted.clear()
    try:
        orch5._run_once()
        crashed = False
    except Exception:
        crashed = True
    check("crashing source did not kill poll", not crashed)
    check("good source still delivered despite bad source", len(posted) == 2)

    print("\n" + "=" * 60)
    total, passed = len(results), sum(results)
    print(f"RESULT: {passed}/{total} checks passed")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
