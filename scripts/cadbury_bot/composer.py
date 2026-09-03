"""Turn the day's flagged one-liners into the recap email body (via an LLM).

LLM calls go through OpenRouter's OpenAI-compatible API; the key comes from the
``OR_CadburySummaryAPI`` env var. Reuses the lazy-client + text-extraction
pattern from ``src/filter/claude_filter.py`` (kept as a small copy rather than a
shared import, so this package stays self-contained).

Unlike ``ClaudeFilter`` — which fails *closed* (a broken response drops items) —
the recap fails *open*: on any error it emails a plain templated list, because a
recap is informational and the raw list beats sending nothing.

The caller passes an ALREADY-CAPPED list (``cfg.digest_max_items``) plus the
``total`` flagged that day, so neither the model prompt nor the fallback can ever
balloon to hundreds of lines.
"""

from __future__ import annotations

import logging
import os

from scripts.cadbury_bot.config import CadburyConfig

logger = logging.getLogger(__name__)

_FALLBACK_MAX_ROWS = 20  # hard ceiling on the raw-list fallback, whatever comes in

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_API_KEY_ENV_VAR = "OR_CadburySummaryAPI"
_DEFAULT_MODEL = "openrouter/auto"  # used when cfg.model is blank

_SYSTEM_TEMPLATE = """\
{intro}

Today is {date}. {volume_note}

{style}
"""

_QUIET_LINE = (
    "Nothing crossed the bot's filter today — markets were quiet on the names "
    "you follow."
)


def _cashtag(ticker) -> str:
    t = (ticker or "").strip().upper()
    return f"${t}" if t else ""


def build_digest(items: list[dict]) -> str:
    """Numbered plaintext, in the given (chronological) order."""
    if not items:
        return "(no items were flagged today)"
    lines = []
    for i, it in enumerate(items, 1):
        tag = _cashtag(it.get("ticker")) or "-"
        urg = (it.get("urgency") or "low").lower()
        text = it.get("rewritten") or it.get("headline") or ""
        lines.append(f"{i}. [{tag}] {urg} — {text}")
    return "\n".join(lines)


def _volume_note(shown: int, total: int) -> str:
    if total <= shown:
        return f"{total} headline(s) were flagged."
    return (f"{total} headlines were flagged today; you are given the {shown} "
            f"highest-priority ones. Summarise the day, don't just relist them.")


def _fallback_body(items: list[dict], total: int) -> str:
    header = ("MARKETS — END-OF-DAY RECAP\n"
              "(auto-summary unavailable — showing the raw flagged list)\n\n"
              "Here's the recap, in the order it crossed today:\n")
    if not items:
        return f"{header}\n{_QUIET_LINE}\n\nSEE YOU TOMORROW."
    rows = []
    for i, it in enumerate(items[:_FALLBACK_MAX_ROWS], 1):
        tag = _cashtag(it.get("ticker"))
        lead = f"{tag} " if tag else ""
        text = it.get("rewritten") or it.get("headline") or ""
        rows.append(f"{i}. {lead}{text}")
    body = "\n".join(rows)
    hidden = total - min(len(items), _FALLBACK_MAX_ROWS)
    if hidden > 0:
        body += f"\n\n…and {hidden} more headline(s) flagged today."
    return f"{header}\n{body}\n\nSEE YOU TOMORROW."


def _extract_text(response) -> str:
    # OpenAI-compatible SDK: response.choices[0].message.content
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message is not None else None
    return (content or "").strip()


def _get_client(client):
    if client is not None:
        return client
    from openai import OpenAI  # lazy: SDK/key not needed until first send

    return OpenAI(
        base_url=_OPENROUTER_BASE_URL,
        api_key=os.environ.get(_API_KEY_ENV_VAR),
    )


def compose(cfg: CadburyConfig, items: list[dict], when, client=None,
            total: "int | None" = None) -> str:
    """Return the plain-text email body. Never raises.

    ``items`` is the capped list to summarise; ``total`` is how many were
    actually flagged that day (defaults to ``len(items)``).
    """
    if total is None:
        total = len(items)
    digest = build_digest(items)
    try:
        style = cfg.recap_style.replace("{max_items}", str(cfg.max_items))
        system = _SYSTEM_TEMPLATE.format(
            intro=cfg.recap_intro,
            date=when.strftime("%A, %B %d %Y"),
            volume_note=_volume_note(len(items), total),
            style=style,
        )
        resp = _get_client(client).chat.completions.create(
            model=cfg.model or _DEFAULT_MODEL,
            max_tokens=cfg.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": digest},
            ],
        )
        text = _extract_text(resp)
        if not text:
            raise ValueError("empty response from model")
        return text
    except Exception as e:
        logger.error("cadbury_bot: recap generation failed (%s) - sending raw "
                     "list", e)
        return _fallback_body(items, total)
