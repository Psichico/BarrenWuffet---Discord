"""ClaudeFilter: keep/drop + urgency + optional rewrite. See §11.

Sends items in batches to Claude, requires strict JSON out, and mutates each
NewsItem in place. Robust to malformed responses: on any parse failure the
affected items default to keep=False so a broken response can never flood the
sinks or crash the loop.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from src.config import LLMConfig
from src.filter.base import Filter
from src.models import NewsItem

logger = logging.getLogger(__name__)

_VALID_URGENCY = {"high", "medium", "low"}
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

_SYSTEM_TEMPLATE = """\
You are a news filter and rewriter for a trader. Apply these rules exactly.

{interest_profile}

Urgency rules (tag every KEPT item):
{urgency_guidance}

For rewriting kept items:
{rewrite_style}

You will receive a numbered list of news items. Respond with ONLY a JSON array,
no prose, no markdown fences. One object per input item:
{{"index": <int>, "keep": <bool>, "urgency": "high|medium|low", "reason": "<short>", "rewrite": "<string or empty>"}}
- If keep is false, set urgency to "low" and rewrite to "".
- If rewrite is disabled, set rewrite to "".
"""


class ClaudeFilter(Filter):
    def __init__(self, llm: LLMConfig, client=None):
        """``client`` is an Anthropic client instance. It is injected so tests
        can pass a mock; in production main.py constructs the real client. Kept
        optional/lazy so the rest of the system imports cleanly before the API
        key/SDK is wired in.
        """
        self.llm = llm
        self._client = client

    def _get_client(self):
        if self._client is None:
            # Lazy import so the package imports without the SDK installed until
            # the API is wired in.
            from anthropic import Anthropic

            self._client = Anthropic()
        return self._client

    # --- public API -------------------------------------------------------

    def process(self, items: list[NewsItem]) -> list[NewsItem]:
        if not items:
            return items

        system_prompt = _SYSTEM_TEMPLATE.format(
            interest_profile=self.llm.interest_profile,
            urgency_guidance=self.llm.urgency_guidance,
            rewrite_style=self.llm.rewrite_style if self.llm.rewrite
            else "(rewriting disabled — always set rewrite to \"\")",
        )

        for start in range(0, len(items), self.llm.batch_size):
            batch = items[start:start + self.llm.batch_size]
            self._process_batch(batch, system_prompt)
        return items

    # --- internals --------------------------------------------------------

    def _process_batch(self, batch: list[NewsItem], system_prompt: str) -> None:
        user_message = self._build_user_message(batch)

        try:
            response = self._get_client().messages.create(
                model=self.llm.model,
                max_tokens=self.llm.max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            text = self._extract_text(response)
            parsed = self._parse_json(text)
        except Exception as e:  # never crash the loop on API/parse failure
            logger.error("Claude filter call failed: %s — defaulting batch to "
                         "keep=False", e)
            self._fail_batch(batch, "filter_parse_error")
            return

        if parsed is None:
            self._fail_batch(batch, "filter_parse_error")
            return

        by_index = {}
        for obj in parsed:
            if isinstance(obj, dict) and "index" in obj:
                by_index[obj["index"]] = obj

        for i, item in enumerate(batch):
            obj = by_index.get(i)
            if obj is None:
                logger.warning("No filter result for index %d (%s) — dropping",
                               i, item.headline)
                item.keep = False
                item.reason = "filter_parse_error"
                item.urgency = "low"
                continue
            self._apply(item, obj)

    def _apply(self, item: NewsItem, obj: dict) -> None:
        item.keep = bool(obj.get("keep", False))
        item.reason = str(obj.get("reason", ""))[:500]

        urgency = str(obj.get("urgency", "low")).lower().strip()
        if urgency not in _VALID_URGENCY:
            logger.warning("Invalid urgency %r for %r — coercing to 'low'",
                           urgency, item.headline)
            urgency = "low"
        # Enforce the contract: dropped items are always low urgency.
        item.urgency = urgency if item.keep else "low"

        rewrite = obj.get("rewrite", "") or ""
        item.rewritten = rewrite if (self.llm.rewrite and item.keep and rewrite) else None

    @staticmethod
    def _build_user_message(batch: list[NewsItem]) -> str:
        lines = []
        for i, item in enumerate(batch):
            ticker = item.ticker or "-"
            publisher = f"  ({item.publisher})" if item.publisher else ""
            lines.append(f"{i}. [{ticker}] {item.headline}{publisher}")
        return "\n".join(lines)

    @staticmethod
    def _extract_text(response) -> str:
        # Anthropic SDK: response.content is a list of blocks with .text
        parts = []
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def _parse_json(text: str) -> Optional[list]:
        if not text:
            return None
        cleaned = _FENCE_RE.sub("", text).strip()
        # In case fences are embedded rather than wrapping.
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error("Claude filter returned non-JSON: %s | raw=%.200r",
                         e, text)
            return None
        if not isinstance(data, list):
            logger.error("Claude filter JSON was not a list: %.200r", text)
            return None
        return data

    @staticmethod
    def _fail_batch(batch: list[NewsItem], reason: str) -> None:
        for item in batch:
            item.keep = False
            item.reason = reason
            item.urgency = "low"
            item.rewritten = None
