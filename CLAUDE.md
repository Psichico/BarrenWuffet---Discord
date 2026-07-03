# CLAUDE.md — Agent guide to the News Alert Bot

> This file is written for an AI coding agent (and any human) who lands in this
> repo fresh. It explains what the system does, how every piece fits together,
> how data flows, what's verified vs. what still needs live testing, and exactly
> what to do next. Read this top-to-bottom before changing anything.

---

## 1. What this is (one paragraph)

A long-running Python worker that polls **Finviz Elite** for stock-market news on
a watchlist, sends each new headline to **Claude** (`claude-haiku-4-5`) which
decides *keep or drop*, rewrites kept headlines into trader-friendly one-liners,
and tags each with an **urgency** (`high`/`medium`/`low`). Kept items are
delivered to **Discord** via webhooks. A `#news` channel gets everything kept; a
`#urgent` channel gets only high-urgency items. State (which items were already
seen) lives in a **SQLite** file so restarts don't re-alert old news. It runs
24/7 as a **Render Background Worker**.

The whole thing is built to be **pluggable**: sources, the filter, and sinks are
independent components behind small interfaces, wired together by config. Adding
a new data source or output channel is "new file + one registry line + one config
block" — nothing in the core changes.

---

## 2. The pipeline (data flow)

```
Orchestrator.run_forever()  (src/orchestrator.py) — the loop, every poll_interval_seconds:

  SOURCES.fetch()        DEDUPE                FILTER.process()        SINKS
  ─────────────          ──────                ────────────────        ─────
  finviz_news    ──▶  drop already-seen  ──▶   Claude: keep/drop  ──▶  for each kept item:
  [screener v2]       (SQLite seen ids)        + urgency               for each sink:
  [x future]                                   + rewrite                 if sink.accepts(item):
                                                                            sink.deliver(item)

  Routing rule: sink.accepts(item) decides membership.
    discord_news  (min_urgency=low)  -> every kept item
    discord_urgent(min_urgency=high) -> only urgency == "high"
```

The **single data type** flowing through every stage is `NewsItem`
(`src/models.py`). Sources create it; the filter mutates `keep`/`reason`/
`urgency`/`rewritten`; sinks read it. This shared type is what keeps the stages
decoupled.

**Critical ordering rule (see `orchestrator._run_once`):** an item is only
`mark_seen()` *after* it has been through the filter. So a crash mid-filter
causes at most one duplicate later — never a silently-swallowed unprocessed item.
Losing news is worse than a rare duplicate.

---

## 3. File-by-file map

```
src/
  main.py              Entrypoint (`python -m src.main`). Builds every component
                       from config via the registries, then runs the loop. The
                       real Anthropic client is created here.
  orchestrator.py      The poll loop. Knows ONLY the Source/Filter/Sink
                       interfaces — never a concrete class. Handles warm-start,
                       SIGTERM, per-iteration try/except, dedupe ordering.
  models.py            NewsItem dataclass — the normalized unit. See the `id`
                       note: it MUST be stable across polls (dedupe depends on it).
  config.py            Loads .env (secrets) + config.yaml (tunables) into a typed
                       Settings object. DEDUPE_PATH env overrides config path.
  dedupe.py            SeenStore: SQLite table `seen(id PK, first_seen)`.
                       has_seen / mark_seen (INSERT OR IGNORE) / prune / is_empty.

  sources/
    base.py            Source ABC: `fetch() -> list[NewsItem]`. Must NOT raise on
                       transient errors — log and return [].
    finviz.py          FinvizNewsSource (Strategy A: authenticated CSV export).
                       *** The main integration point — see §6. ***
    finviz_screener.py FinvizScreenerSource — v2 STUB, raises NotImplementedError.
                       The worked example proving a new source is a drop-in.
    registry.py        SOURCE_REGISTRY: config name -> class. build_source().

  filter/
    base.py            Filter ABC: `process(items) -> items` (mutates in place).
    claude_filter.py   ClaudeFilter. Builds the system prompt from config, batches
                       items, calls Claude, parses strict JSON, coerces bad urgency
                       to "low", and on ANY failure defaults items to keep=False so
                       a broken response can never flood Discord or crash the loop.

  sinks/
    base.py            Sink ABC: `accepts(item)` routing predicate (default True)
                       + `deliver(item) -> bool`. Must NOT raise — log, return False.
    discord.py         DiscordSink: webhook POST, embed builder, urgency->color,
                       429 retry (reads retry_after), min_urgency routing.
    registry.py        SINK_REGISTRY. Note: discord_news AND discord_urgent map to
                       the SAME DiscordSink class — differ only by config.

tests/                 pytest suite, fully mocked (no network, no API key).
  test_dedupe.py           store correctness + persistence + prune
  test_finviz_source.py    CSV parsing + auth/network/HTML-login error handling
  test_claude_filter.py    JSON parse, fence-strip, urgency coerce, no-crash
  test_discord_sink.py     accepts() routing, embed contents, 429 retry
  test_routing.py          high->both sinks, low/medium->news only

scripts/
  verify_flow.py       End-to-end DRY-RUN harness (fakes all 3 external APIs,
                       exercises the REAL orchestrator/filter/sink/store). No
                       secrets needed. `python scripts/verify_flow.py`.

config.yaml            Non-secret tunables + the interest profile (the "brain" of
                       the filter, in plain English). Edit freely — no code change.
.env.example           Template for secrets. Copy to .env and fill in.
render.yaml            Render Background Worker + persistent disk definition.
Dockerfile             Optional reproducible build (python:3.11-slim).
requirements.txt       anthropic, requests, PyYAML, python-dotenv, pytest.
README.md              Human-facing quickstart + deploy notes.
```

---

## 4. Configuration model (secrets vs. tunables)

Two layers, deliberately separated:

| Layer          | File           | Committed? | Holds |
|----------------|----------------|-----------|-------|
| Secrets        | `.env`         | **NO**    | API keys, Finviz token, webhook URLs, DB path |
| Tunables       | `config.yaml`  | yes       | poll interval, model, interest profile, watchlist, which sources/sinks are enabled |

Sinks never store the webhook URL in `config.yaml`. Instead config names the
*env var* (`webhook_env_var: DISCORD_NEWS_WEBHOOK_URL`) and the sink reads the
actual URL from the environment. This keeps secrets out of git entirely.

Required env vars (see `.env.example`):
- `ANTHROPIC_API_KEY` — read automatically by the Anthropic SDK.
- `FINVIZ_AUTH_TOKEN` — read by FinvizNewsSource.
- `DISCORD_NEWS_WEBHOOK_URL` — the #news channel.
- `DISCORD_URGENT_WEBHOOK_URL` — the #urgent channel (optional in v1).
- `DEDUPE_PATH` — where the SQLite file lives (`./seen.db` local; `/var/data/seen.db` on Render).
- Optional: `FINVIZ_NEWS_EXPORT_URL` (override endpoint), `LOG_LEVEL`.

---

## 5. Verification status — what's PROVEN vs. what's NOT

### PROVEN (no secrets required)

- **Unit tests:** `pytest -q` → 23 passing. Covers dedupe, Finviz CSV parsing +
  every error path, Claude JSON parsing + coercion + no-crash, Discord routing +
  embed + 429 retry.
- **End-to-end dry run:** `python scripts/verify_flow.py` → 17/17 checks. This
  drives the REAL orchestrator, filter, sinks, and store with FAKE external APIs
  and asserts the full behavior:
  1. one poll: high item → both channels, medium → news only, dropped →
     nowhere; rewrite used as the embed description; filter called once (batched).
  2. dedupe: a second identical poll delivers nothing and doesn't call Claude.
  3. restart: reopening the SQLite store → no re-alert.
  4. warm-start: empty store marks everything seen WITHOUT delivering.
  5. malformed Claude response → no delivery, no crash.
  6. a source that raises → poll survives, other sources still deliver.

**This means the wiring, routing, dedupe, and failure-handling are all confirmed
correct.** What the dry run cannot confirm is the shape of the real external
APIs.

### NOT yet proven (needs your live credentials)

1. **The exact Finviz Elite news export contract** — the endpoint path, the query
   params, and the real CSV column headers. The code makes reasonable assumptions
   (below) and isolates them so fixing them touches ONE file.
2. That the Claude prompt produces good keep/drop/urgency judgments on *real*
   headlines (the JSON parsing is proven; the quality of decisions is a tuning
   task once real data flows).
3. That the Discord webhook accepts our exact embed payload (format is per
   Discord's documented schema, but only a live POST confirms it end to end).

---

## 6. The Finviz integration — the one real unknown

`src/sources/finviz.py` implements **Strategy A**: an authenticated CSV export.
Everything the real API might differ on is isolated so you change only this file:

- **Endpoint:** `NEWS_EXPORT_URL` (default `https://elite.finviz.com/news_export.ashx`),
  overridable via the `FINVIZ_NEWS_EXPORT_URL` env var.
- **Auth:** the token is sent as the `auth=<TOKEN>` query param (from
  `FINVIZ_AUTH_TOKEN`). Cookie auth is the documented alternative if needed.
- **Per-ticker filter:** currently sent as `t=<ticker>`. Adjust if the real API
  differs.
- **CSV columns:** mapped by `_COLUMN_ALIASES` (accepts common header variants
  like Title/Headline, Url/Link, Source/Publisher, Date, Ticker/Symbol). If the
  real headers differ, edit that dict + `_row_to_item`. Nothing else in the
  system needs to change.

**Error handling is already robust and distinct:**
- Missing token → clear error, returns `[]`.
- HTTP 401/403 → "auth failure" error, returns `[]`.
- 200 but HTML body (Finviz returns a login page when the token is bad) →
  detected and logged as an auth failure, returns `[]`.
- Network exception → "network error", returns `[]`.
- The loop never crashes on any of these.

**To finalize this, run one raw request and share the output:**
```bash
curl -s "https://elite.finviz.com/news_export.ashx?v=3&auth=YOUR_TOKEN" | head -5
```
The first lines reveal the real column headers and confirm the endpoint. Then
adjust `_COLUMN_ALIASES` / params to match.

---

## 7. How to test locally (step by step)

```bash
# 0. Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Prove the wiring with ZERO secrets
pytest -q                      # unit tests -> 23 passed
python scripts/verify_flow.py  # end-to-end dry run -> 17/17

# 2. Add secrets
cp .env.example .env
#   edit .env: ANTHROPIC_API_KEY, FINVIZ_AUTH_TOKEN, DISCORD_NEWS_WEBHOOK_URL
#   For a first live test, keep DEDUPE_PATH=./seen.db

# 3. Test Finviz alone (cheap, no Claude/Discord spend). In a python shell:
python -c "from dotenv import load_dotenv; load_dotenv(); \
from src.sources.finviz import FinvizNewsSource; \
items = FinvizNewsSource(watchlist=['NVDA']).fetch(); \
print(len(items), 'items'); [print(i.ticker, i.headline, i.url) for i in items[:5]]"
#   If this prints real headlines, parsing works. If [], read the logged error.

# 4. Full run. NOTE: warm_start=true means the FIRST boot marks everything seen
#    WITHOUT posting (by design, so you don't dump the backlog). To actually SEE
#    a delivery on the first run, temporarily set `warm_start: false` in
#    config.yaml OR delete ./seen.db between runs after the warm boot.
python -m src.main
#   Watch stdout: "Fetched N items, M new", then "KEPT [urgency] ... -> [sinks]".
```

### Gotcha checklist for first live run
- [ ] `warm_start: true` suppresses the first batch — expected. Flip to `false`
      once, or clear `seen.db`, to observe a real delivery.
- [ ] If Finviz returns `[]`, the log says whether it was auth vs. network vs.
      HTML-login. Fix accordingly (§6).
- [ ] If nothing is "kept", the interest profile in `config.yaml` may be too
      strict for the day's headlines — that's a tuning question, not a bug.
- [ ] `discord_urgent` only fires on `urgency == high`. A quiet news day may
      produce none — that's correct.

---

## 8. Deployment (Render Background Worker)

Deploy as a **Background Worker**, NOT a Web Service (there's no HTTP port; a Web
Service would be marked unhealthy). `render.yaml` is committed and IaC-ready:

1. Push to GitHub.
2. Render → New → Blueprint → point at the repo (reads `render.yaml`).
3. Set the secret env vars in the dashboard (they're `sync: false` in yaml).
4. Deploy; watch logs for the first successful poll.

**Persistence:** `render.yaml` mounts a 1 GB disk at `/var/data` and sets
`DEDUPE_PATH=/var/data/seen.db`, so the dedupe DB survives deploys/restarts. The
`warm_start: true` flag is the secondary safety net if the disk is ever empty.

**Cost:** free tier does NOT keep workers alive 24/7 → use Starter (~$7/mo).
Claude (Haiku) ≈ $2–4/mo at 5-min polling. Total ≈ $10/mo.

---

## 9. How to extend (the whole point of the architecture)

### Add a second Discord channel — CONFIG ONLY (already stubbed)
`discord_urgent` is already in `config.yaml`. Just create the webhook and set
`DISCORD_URGENT_WEBHOOK_URL`. No code change — same `DiscordSink` class, different
config. The filter already emits `urgency`, so the data is ready.

### Add a new SOURCE (e.g. X/Twitter, Finviz screener)
1. Write `src/sources/<name>.py` subclassing `Source`, implementing `fetch()`.
   - Emit `NewsItem`s with a **stable `id`** (URL, or a hash — never a timestamp).
   - Never raise on transient errors: log and return `[]`.
2. Add one line to `SOURCE_REGISTRY` in `src/sources/registry.py`.
3. Add a config block under `sources:` in `config.yaml`.
   The same filter and sinks handle it unchanged. See `finviz_screener.py` +
   plan §6.1 for the worked example.

### Add a new SINK (e.g. Telegram)
1. Write `src/sinks/<name>.py` subclassing `Sink` (`accepts()` + `deliver()`).
2. Add one line to `SINK_REGISTRY`.
3. Add a config block under `sinks:`.

### Change filter behavior
Edit the `interest_profile`, `urgency_guidance`, or `rewrite_style` strings in
`config.yaml`. These are plain English and drive the whole classification. Swap
the `model` there too — no code change.

---

## 10. Invariants an agent must NOT break

- **`NewsItem.id` must be stable across polls.** Dedupe correctness depends on it.
  Never derive it from fetch time or randomness.
- **Sources and sinks must never raise on transient errors.** Log and return
  `[]` / `False`. The orchestrator also guards defensively, but the contract is
  on the component.
- **`mark_seen` happens AFTER the filter**, per batch. Don't move it earlier.
- **A malformed filter response must default items to `keep=False`.** Never let a
  parse failure result in delivery ("fail closed" — silence beats flooding).
- **Secrets stay in env / `.env`.** Never write a key or webhook into
  `config.yaml` or any committed file. `.env` and `*.db` are gitignored.
- **The orchestrator must not import concrete source/sink classes.** It depends
  only on the ABCs; wiring goes through the registries.
- After any change, re-run both `pytest -q` and `python scripts/verify_flow.py`.

---

## 11. Quick reference — commands

```bash
pytest -q                       # unit tests (23)
python scripts/verify_flow.py   # end-to-end dry run (17 checks), no secrets
python -m src.main              # run the real bot (needs .env)
```

---

## 12. Current state summary (for the next agent)

- All modules from the implementation plan are built and wired.
- 23/23 unit tests pass; 17/17 end-to-end dry-run checks pass.
- The ONLY thing blocking a real run is confirming the Finviz Elite export
  endpoint + CSV columns against a live token (§6), plus supplying the three
  secrets. Everything downstream of a correctly-parsed `NewsItem` is verified.
- Screener source (`finviz_screener.py`) and X source are intentionally NOT built
  (v2) — the stub + registry pattern proves they're drop-ins.
```
