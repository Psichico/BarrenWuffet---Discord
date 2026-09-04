"""Config for cadbury_bot.

Reads the ``cadbury_bot:`` key out of the SAME ``config.yaml`` the core uses,
without importing or touching ``src/config.py``. Secrets (the Gmail app
password) stay in the environment — only the env var *names* live here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_DEFAULT_INTRO = (
    "You are writing an end-of-day market recap email for one trader. The input "
    "is the list of headlines this bot flagged as important today, already "
    "filtered to the trader's interests and given to you IN THE ORDER THEY "
    "CROSSED during the day. Write only from the one-liners provided — never add "
    "figures, quotes, or facts that aren't in the input."
)

_DEFAULT_STYLE = (
    "Plain text only. No markdown headers, no bullets, no JSON. Structure "
    "exactly:\n"
    "  - Line 1: ONE punchy ALL-CAPS sentence you write fresh for today, tone "
    "matching how eventful the day was.\n"
    "  - Line 2: \"Here's the recap, in the order it crossed today:\"\n"
    "  - Then a numbered list, AT MOST {max_items} points, roughly in the order "
    "the news crossed. Each point is 2-4 sentences in YOUR words summarising a "
    "story or a cluster of related stories — do NOT just echo a headline. "
    "Fold same-topic headlines (same company/event) into one point. Lead with "
    "the company name then $TICKER (or the theme). State the fact, no hype, no "
    "emoji.\n"
    "  - If far more than {max_items} headlines were flagged, pick the "
    "{max_items} most market-moving threads and, in the last point, note how "
    "many others there were.\n"
    "  - If no items were given, replace the list with exactly: \"Nothing "
    "crossed the bot's filter today — markets were quiet on the names you "
    "follow.\"\n"
    "  - Last line: ONE fresh ALL-CAPS sign-off sentence."
)


@dataclass
class CadburyConfig:
    enabled: bool = False
    timezone: str = "America/New_York"
    send_hour: int = 16
    send_minute: int = 30
    model: str = ""               # blank ⇒ "openrouter/auto" (see composer.py)
    max_tokens: int = 16000       # reasoning + visible tokens; give reasoning models headroom
    reasoning_effort: str = "low" # low|medium|high|minimal; "" ⇒ don't send it to OpenRouter
    max_items: int = 12           # numbered points the model should output
    digest_max_items: int = 40    # day's items actually fed to the model / fallback
    recipients: list[str] = field(default_factory=list)
    subject_prefix: str = "Market Recap"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender_env_var: str = "GMAIL_ADDRESS"
    password_env_var: str = "GMAIL_APP_PASSWORD"
    recap_intro: str = _DEFAULT_INTRO
    recap_style: str = _DEFAULT_STYLE
    # Kept dedupe/prune windows (days).
    prune_kept_days: int = 14
    prune_runs_days: int = 30


def load_cadbury_config(config_path: str = "config.yaml") -> Optional[CadburyConfig]:
    """Return a CadburyConfig, or ``None`` if there is no ``cadbury_bot:`` block."""
    load_dotenv()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("cadbury_bot: config file %s not found", config_path)
        return None

    block = raw.get("cadbury_bot")
    if not isinstance(block, dict):
        return None

    recipients = block.get("recipients") or []
    if isinstance(recipients, str):
        recipients = [recipients]

    return CadburyConfig(
        enabled=bool(block.get("enabled", False)),
        timezone=str(block.get("timezone", "America/New_York")),
        send_hour=int(block.get("send_hour", 16)),
        send_minute=int(block.get("send_minute", 30)),
        model=str(block.get("model", "")),
        max_tokens=int(block.get("max_tokens", 16000)),
        reasoning_effort=str(block.get("reasoning_effort", "low") or ""),
        max_items=int(block.get("max_items", 12)),
        digest_max_items=int(block.get("digest_max_items", 40)),
        recipients=[str(r) for r in recipients],
        subject_prefix=str(block.get("subject_prefix", "Market Recap")),
        smtp_host=str(block.get("smtp_host", "smtp.gmail.com")),
        smtp_port=int(block.get("smtp_port", 587)),
        sender_env_var=str(block.get("sender_env_var", "GMAIL_ADDRESS")),
        password_env_var=str(block.get("password_env_var", "GMAIL_APP_PASSWORD")),
        recap_intro=str(block.get("recap_intro") or _DEFAULT_INTRO),
        recap_style=str(block.get("recap_style") or _DEFAULT_STYLE),
        prune_kept_days=int(block.get("prune_kept_days", 14)),
        prune_runs_days=int(block.get("prune_runs_days", 30)),
    )
