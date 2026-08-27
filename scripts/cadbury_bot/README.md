# cadbury_bot — end-of-day Market Recap email

A **self-contained, optional bolt-on** for the news-alert bot. At **16:30 ET on
weekdays** it emails a plain-text recap of every headline the bot flagged that
day, in the order they crossed. Nothing flagged → a short "quiet day" note still
goes out.

It plugs into the core through two **generic, feature-agnostic** hooks on
`Orchestrator` (`on_item_kept`, `post_iteration_hook`). Nothing in `src/` knows
this package exists beyond `src/main.py` wiring it up (and it wires to `None`
unless the `cadbury_bot:` config block is present and `enabled: true`).

## How it works

```
poll loop (src/orchestrator.py)
  │
  ├─ for each kept, non-duplicate item ──▶ on_item_kept(item)
  │                                          └▶ KeptItemRecorder.record()
  │                                               └▶ cadbury_kept_items  (in seen.db)
  │
  └─ after each iteration ──────────────▶ post_iteration_hook()
                                             └▶ CadburyBot.tick()
                                                  ├─ gate: weekday? ≥16:30 ET? not sent today?
                                                  ├─ compose(): Claude writes the recap
                                                  │             (fail-open: raw list on error)
                                                  └─ EmailMailer.send(): Gmail SMTP
                                                       └▶ cadbury_runs  (marks the day done)
```

Storage: cadbury_bot opens its **own** SQLite connection to the same file the
core uses (`DEDUPE_PATH` / `dedupe.path`, default `./seen.db`) and owns only the
`cadbury_kept_items` and `cadbury_runs` tables. `src/dedupe.py` is untouched.

Timezone: uses `zoneinfo("America/New_York")` (real DST), not the core's fixed
UTC-4 offset — hence `tzdata` in `requirements.txt`.

## Config (`config.yaml` → `cadbury_bot:` block)

| key | default | meaning |
|---|---|---|
| `enabled` | `false` | master switch; `false`/absent ⇒ feature is inert |
| `timezone` | `America/New_York` | zone for the 16:30 trigger + the "day" bucket |
| `send_hour` / `send_minute` | `16` / `30` | ET wall-clock trigger |
| `model` | `claude-sonnet-5` | model for the recap write-up |
| `max_tokens` | `2000` | response cap |
| `max_items` | `12` | merge related headlines beyond this |
| `recipients` | – | list of To: addresses (not a secret) |
| `subject_prefix` | `Market Recap` | ⇒ `Market Recap — Wed, Aug 26 2026` |
| `smtp_host` / `smtp_port` | `smtp.gmail.com` / `587` | SMTP endpoint |
| `sender_env_var` | `GMAIL_ADDRESS` | env var holding the sender address |
| `password_env_var` | `GMAIL_APP_PASSWORD` | env var holding the app password |
| `recap_intro` / `recap_style` | built-in | plain-English prompt pieces |
| `prune_kept_days` / `prune_runs_days` | `14` / `30` | table retention |

## Secrets (env only)

- `GMAIL_ADDRESS` — the sending Gmail address.
- `GMAIL_APP_PASSWORD` — a Google **App Password** (the account needs 2-Step
  Verification): <https://myaccount.google.com/apppasswords>. Not your normal
  password.

Add both to `.env` locally and to the `news-bot` service in `render.yaml`
(already listed there, `sync: false`).

## Manual trigger / smoke test

```bash
python -m scripts.cadbury_bot --force            # build + send today's recap right now
python -m scripts.cadbury_bot --date 2026-08-26  # re-run a specific day's items
```

Without `--force` it still respects the once-per-day marker.

## Tests

```bash
pytest -q scripts/cadbury_bot/tests          # unit + integration (mocked, no secrets)
python scripts/cadbury_bot/verify_cadbury.py # standalone end-to-end dry run
```

The core suite (`pytest -q`, `python scripts/verify_flow.py`) is unaffected.

## Catch-up / edge behaviour

- Worker down at 16:30, back by 23:59 ET → recap still goes out that day.
- Worker down the whole evening → that day is skipped (the gate keys on *today*).
  Use `python -m scripts.cadbury_bot --date <missed-day> --force` to send it late.
- SMTP failure → the day is **not** marked done; the next poll iteration retries.

## Removal checklist

1. Delete the `scripts/cadbury_bot/` directory. (`scripts/__init__.py` is
   harmless to leave; delete it too if nothing else imports `scripts.*`.)
2. `src/main.py` — remove: the `from scripts.cadbury_bot import build_cadbury` line, the
   `cadbury = build_cadbury(...)` line, and the two `on_item_kept=` /
   `post_iteration_hook=` kwargs on the `Orchestrator(...)` call.
3. `src/orchestrator.py` — optional: drop the two ctor params
   (`on_item_kept`, `post_iteration_hook`), the two `self.…` assignments, and the
   two guarded call sites. They are generic no-ops, so leaving them is harmless.
4. `config.yaml` — delete the `cadbury_bot:` block.
5. `render.yaml` / `.env.example` — remove `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD`
   if nothing else uses them.
6. `requirements.txt` — remove `tzdata` if nothing else uses it.
7. Optional: `sqlite3 seen.db 'DROP TABLE cadbury_kept_items; DROP TABLE cadbury_runs;'`
