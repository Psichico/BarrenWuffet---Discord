# News Alert Bot

A pluggable news-alert pipeline: **sources → dedupe → Claude filter → sinks**.
It polls Finviz Elite for watchlist news, uses Claude (`claude-haiku-4-5`) to
keep/drop, rewrite, and tag urgency, then routes items to Discord channels.

The architecture is deliberately modular. Adding a new **source** (X/Twitter, a
Finviz screener) or a new **sink** (Telegram, a second Discord channel) never
requires touching the filter or orchestrator — you drop in a module and add a
line of config.

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ SOURCES  │──▶│ DEDUPE   │──▶│ FILTER   │──▶│ SINKS    │
│finviz_news│  │ SQLite   │   │ Claude   │   │#news     │
│[screener]│   │ seen ids │   │keep/drop │   │#urgent   │
│[x]       │   │          │   │+urgency  │   │[telegram]│
└──────────┘   └──────────┘   └──────────┘   └──────────┘
```

Routing: each sink's `accepts(item)` decides if it takes the item.
`#news` accepts every kept item; `#urgent` accepts only `urgency == "high"`.

## Layout

```
src/
  main.py            entrypoint — builds everything from config, runs the loop
  orchestrator.py    poll loop; wires sources → dedupe → filter → sinks
  models.py          NewsItem — the normalized unit passed between stages
  config.py          loads .env + config.yaml into a typed Settings
  dedupe.py          SeenStore (SQLite)
  sources/           Source ABC, FinvizNewsSource, screener stub, registry
  filter/            Filter ABC, ClaudeFilter
  sinks/             Sink ABC, DiscordSink, registry
tests/               pytest suite (no live network / API calls)
```

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then fill in your secrets
python -m src.main
```

Run the tests (all mocked — no network, no API key needed):

```bash
pytest -q
```

## Configuration

- **Secrets** live in `.env` (never committed): `ANTHROPIC_API_KEY`,
  `FINVIZ_AUTH_TOKEN`, `DISCORD_NEWS_WEBHOOK_URL`,
  `DISCORD_URGENT_WEBHOOK_URL` (v1.1), `DEDUPE_PATH`.
- **Tunables + interest profile** live in `config.yaml`: poll interval, the LLM
  model/prompt, watchlist, sources, sinks, dedupe pruning.

The interest profile, urgency guidance, and rewrite style in `config.yaml` are
plain English and are the heart of the filter — edit them freely, no code change.

### Adding a second Discord channel (v1.1 — config only)

1. Create a second Discord webhook, set `DISCORD_URGENT_WEBHOOK_URL`.
2. The `discord_urgent` block is already in `config.yaml` (`min_urgency: high`).

No code change: both channels use the same `DiscordSink`, and the filter already
emits the `urgency` field.

### Adding a new source later (e.g. X, Finviz screener)

1. Write `src/sources/<name>.py` implementing the `Source` interface.
2. Add one line to `src/sources/registry.py`.
3. Add a config block under `sources:` in `config.yaml`.

Nothing else changes. `FinvizScreenerSource` is stubbed in
`src/sources/finviz_screener.py` as the worked example (see plan §6.1).

## Deployment (Render Background Worker)

Deploy as a **Background Worker**, not a Web Service — there is no HTTP port.

1. Push the repo to GitHub.
2. Render Dashboard → New → Blueprint → point at the repo (reads `render.yaml`).
3. Set the secret env vars in the dashboard (Anthropic key, Finviz token, one or
   both Discord webhooks).
4. Deploy and watch the logs for the first successful poll.

**Persistence:** `render.yaml` attaches a 1 GB persistent disk at `/var/data` and
sets `DEDUPE_PATH=/var/data/seen.db` so the dedupe store survives restarts and
old news is not re-alerted. As a secondary safety net, `warm_start: true` marks
current items as seen on first boot with an empty store — so even without a disk,
a restart won't dump the backlog to Discord.

**Cost:** Render's free tier does not keep workers alive 24/7 — use the **Starter
worker (~$7/mo)**. Claude (Haiku) runs ~$2–4/mo at 5-min polling. Finviz Elite is
already owned. **Total ≈ $10/mo.**

## Notes on the Finviz integration

`FinvizNewsSource` implements Strategy A (authenticated CSV export via
`news_export.ashx`, token from `FINVIZ_AUTH_TOKEN`). The exact endpoint path and
CSV column names are confirmed at integration time — the column mapping is
centralized in `_COLUMN_ALIASES` / `_row_to_item` in `src/sources/finviz.py` and
the endpoint is overridable via `FINVIZ_NEWS_EXPORT_URL`, so adjusting to the
real API touches only that one file. Auth failures (expired token, HTML login
page returned with 200) are logged distinctly from network errors, and the loop
always survives.
```
