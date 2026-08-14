"""DiscordSink — webhook delivery with urgency-based routing. See §7.

One instance per Discord channel, each reading its own webhook URL from env and
carrying its own ``min_urgency`` routing rule. Two instances (news + urgent) is
the whole "urgency bot" — same class, different config.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

from src.models import NewsItem
from src.sinks.base import Sink

logger = logging.getLogger(__name__)

_RANK = {"low": 0, "medium": 1, "high": 2}

# Presentation only — routing is handled by accepts(), not color.
_URGENCY_COLOR = {
    "high": 0xE74C3C,    # red
    "medium": 0xF39C12,  # amber
    "low": 0x3498DB,     # blue
}

# Discord limits
_TITLE_MAX = 256
_DESC_MAX = 4096
_HEADLINE_TRUNC = 200


class DiscordSink(Sink):
    def __init__(
        self,
        name: str,
        webhook_env_var: str,
        min_urgency: str = "low",
        min_urgency_env_var: Optional[str] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        rate_limit_sleep: float = 1.0,
        request_timeout: float = 10.0,
        allowed_sources: Optional[list[str]] = None,
    ):
        self.name = name
        self.webhook_env_var = webhook_env_var
        self.webhook_url = os.environ.get(webhook_env_var, "")
        if not self.webhook_url:
            logger.warning(
                "Webhook env var %s is not set; sink %s will fail delivery until "
                "it is configured.", webhook_env_var, name,
            )
        self.min_urgency = min_urgency if min_urgency in _RANK else "low"
        self.min_urgency_env_var = min_urgency_env_var
        self.username = username
        self.avatar_url = avatar_url
        self.rate_limit_sleep = rate_limit_sleep
        self.request_timeout = request_timeout
        self.allowed_sources = allowed_sources

    # --- routing ----------------------------------------------------------

    def _effective_min_urgency(self) -> str:
        """Read the env var override live (not cached), so a Render dashboard
        change takes effect on the next poll with no restart needed. Falls back
        to the config-file default if unset or holds an unrecognized value."""
        if self.min_urgency_env_var:
            override = os.environ.get(self.min_urgency_env_var, "").strip().lower()
            if override in _RANK:
                return override
        return self.min_urgency

    def accepts(self, item: NewsItem) -> bool:
        if self.allowed_sources is not None and item.source not in self.allowed_sources:
            return False
        return _RANK.get(item.urgency, 0) >= _RANK[self._effective_min_urgency()]

    # --- delivery ---------------------------------------------------------

    def deliver(self, item: NewsItem) -> bool:
        if not self.webhook_url:
            logger.error("Sink %s has no webhook URL (%s unset) — cannot deliver",
                         self.name, self.webhook_env_var)
            return False

        payload = self._build_payload(item)

        ok = self._post(payload)
        if ok and self.rate_limit_sleep:
            time.sleep(self.rate_limit_sleep)
        return ok

    # --- internals --------------------------------------------------------

    def _post(self, payload: dict, allow_retry: bool = True) -> bool:
        try:
            resp = requests.post(
                self.webhook_url, json=payload, timeout=self.request_timeout
            )
        except requests.RequestException as e:
            logger.error("Discord network error on sink %s: %s", self.name, e)
            return False

        if 200 <= resp.status_code < 300:
            return True

        if resp.status_code == 429 and allow_retry:
            retry_after = self._retry_after_seconds(resp)
            logger.warning("Discord 429 on sink %s — sleeping %.2fs then retrying",
                           self.name, retry_after)
            time.sleep(retry_after)
            return self._post(payload, allow_retry=False)

        logger.error("Discord delivery failed on sink %s: HTTP %s | %.200s",
                     self.name, resp.status_code, resp.text)
        return False

    @staticmethod
    def _retry_after_seconds(resp) -> float:
        try:
            body = resp.json()
            return float(body.get("retry_after", 1.0))
        except (ValueError, AttributeError):
            return 1.0

    def _build_payload(self, item: NewsItem) -> dict:
        headline = item.headline[:_HEADLINE_TRUNC]
        title = (f"{item.ticker} — {headline}" if item.ticker else headline)[:_TITLE_MAX]
        description = (item.rewritten or item.headline)[:_DESC_MAX]

        footer_bits = [b for b in (item.publisher, item.source, item.urgency) if b]
        embed = {
            "title": title,
            "description": description,
            "color": _URGENCY_COLOR.get(item.urgency, _URGENCY_COLOR["low"]),
            "footer": {"text": " | ".join(footer_bits)},
        }
        if item.url:
            embed["url"] = item.url
        if item.published_at:
            embed["timestamp"] = item.published_at.isoformat()

        payload = {"embeds": [embed]}
        if self.username:
            payload["username"] = self.username
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url
        return payload
