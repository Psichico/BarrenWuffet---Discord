"""Turn the day's flagged one-liners into the recap email body (via Claude).

Reuses the lazy-client + text-extraction pattern from
``src/filter/claude_filter.py`` (kept as a small copy rather than a shared
import, so this package stays self-contained).

Unlike ``ClaudeFilter`` — which fails *closed* (a broken response drops items) —
the recap fails *open*: on any error it emails a plain templated list, because a
recap is informational and the raw list beats sending nothing.
"""

from __future__ import annotations

import logging

from scripts.cadbury_bot.config import CadburyConfig

logger = logging.getLogger(__name__)

_SYSTEM_TEMPLATE = """\
{intro}

Today is {date}.

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


def _fallback_body(items: list[dict]) -> str:
    header = "MARKETS — END-OF-DAY RECAP\n\n" \
             "Here's the recap, in the order it crossed today:\n"
    if not items:
        body = _QUIET_LINE
    else:
        rows = []
        for i, it in enumerate(items, 1):
            tag = _cashtag(it.get("ticker"))
            lead = f"{tag} " if tag else ""
            text = it.get("rewritten") or it.get("headline") or ""
            rows.append(f"{i}. {lead}{text}")
        body = "\n".join(rows)
    return (f"{header}\n{body}\n\n(auto-summary unavailable — raw list)\n"
            "THE TAPE STOPS FOR NO ONE. SEE YOU TOMORROW.")


def _extract_text(response) -> str:
    parts = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _get_client(client):
    if client is not None:
        return client
    from anthropic import Anthropic  # lazy: SDK/key not needed until first send

    return Anthropic()


def compose(cfg: CadburyConfig, items: list[dict], when, client=None) -> str:
    """Return the plain-text email body. Never raises."""
    digest = build_digest(items)
    try:
        style = cfg.recap_style.replace("{max_items}", str(cfg.max_items))
        system = _SYSTEM_TEMPLATE.format(
            intro=cfg.recap_intro,
            date=when.strftime("%A, %B %d %Y"),
            style=style,
        )
        resp = _get_client(client).messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=system,
            messages=[{"role": "user", "content": digest}],
        )
        text = _extract_text(resp)
        if not text:
            raise ValueError("empty response from model")
        return text
    except Exception as e:
        logger.error("cadbury_bot: recap generation failed (%s) - sending raw "
                     "list", e)
        return _fallback_body(items)
