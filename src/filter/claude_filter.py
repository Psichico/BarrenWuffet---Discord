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

Each input item comes with a ticker tag supplied by the data feed, which is
SOMETIMES WRONG (it can be stale, or lifted from an unrelated story in a broad
market-news feed). For every item:
- Read the headline and decide what company/ticker the story is ACTUALLY
  about. If the supplied ticker matches, echo it back. If it's wrong or the
  story isn't really about a specific ticker, output the correct ticker
  instead, or "" if none applies. Never let a wrong ticker pass through.
- Produce a "story_key": a short lowercase-hyphenated slug identifying the
  underlying real-world EVENT, built only from (a) the true primary ticker/
  company and (b) the core factual event type (e.g. "earnings", "sp500-
  inclusion", "guidance-cut", "product-launch"). Deliberately EXCLUDE framing,
  sentiment, or opinion words ("surge", "concerns", "finally", "after months
  of speculation") — those vary between outlets covering the same event and
  must not change the key. Two headlines about the same event, worded
  differently, MUST produce the identical story_key.
  Example: both of these should yield story_key "rddt-sp500-inclusion":
    "RDDT — Reddit is joining the S&P 500. Analysts flag concerns."
    "Reddit shares surge as S&P 500 inclusion is confirmed."

You will receive a numbered list of news items. Respond with ONLY a JSON array,
no prose, no markdown fences. One object per input item:
{{"index": <int>, "keep": <bool>, "urgency": "high|medium|low", "reason": "<short>", "rewrite": "<string or empty>", "ticker": "<corrected ticker or empty>", "story_key": "<slug or empty>"}}
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

        # Only filter items that aren't already marked keep=True
        items_to_filter = [item for item in items if not item.keep]
        if not items_to_filter:
            return items

        import os
        disable_filtering = os.environ.get("DISABLE_FILTERING", "").lower() in ("true", "1", "yes")

        profile = self.llm.interest_profile
        if disable_filtering:
            profile = "KEEP news about:\n  - ALL topics. Always set 'keep' to true for all items."

        system_prompt = _SYSTEM_TEMPLATE.format(
            interest_profile=profile,
            urgency_guidance=self.llm.urgency_guidance,
            rewrite_style=self.llm.rewrite_style if self.llm.rewrite
            else "(rewriting disabled — always set rewrite to \"\")",
        )

        for start in range(0, len(items_to_filter), self.llm.batch_size):
            batch = items_to_filter[start:start + self.llm.batch_size]
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
                import os
                disable_filtering = os.environ.get("DISABLE_FILTERING", "").lower() in ("true", "1", "yes")
                item.keep = True if disable_filtering else False
                item.reason = "filter_parse_error"
                item.urgency = "low"
                continue
            self._apply(item, obj)

    def _apply(self, item: NewsItem, obj: dict) -> None:
        import os
        disable_filtering = os.environ.get("DISABLE_FILTERING", "").lower() in ("true", "1", "yes")

        keep_val = bool(obj.get("keep", False))
        if disable_filtering:
            keep_val = True

        item.keep = keep_val
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

        # Ticker correction: Claude reads the actual headline content, so trust
        # its judgment over the (sometimes wrong) feed-supplied ticker. Blank
        # means "no specific ticker applies" — leave item.ticker as None rather
        # than showing a bogus one.
        ticker = str(obj.get("ticker", "") or "").strip().upper()[:10]
        item.ticker = ticker or None

        # story_key: used for cross-source/cross-poll duplicate-story detection
        # in the orchestrator. Best-effort — a missing/blank key just means this
        # item won't be matched against other stories, never a failure.
        #
        # KNOWN LIMITATION: the key intentionally omits date/time (see the
        # prompt above) so reworded coverage of the same event still matches.
        # The flip side: two genuinely distinct same-day/same-ticker/
        # same-event-type stories (e.g. two separate TSLA product
        # announcements) could collide and the second gets wrongly suppressed
        # by SeenStore.has_recent_story() (src/dedupe.py) within its 24h
        # window. If that's observed in practice, the fix is to make the key
        # more specific (or add a fuzzy match) rather than reverting to
        # id-only dedupe — see the plan history / CLAUDE.md §12.
        story_key = str(obj.get("story_key", "") or "").strip().lower()
        if story_key:
            item.meta["story_key"] = story_key

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
        import os
        disable_filtering = os.environ.get("DISABLE_FILTERING", "").lower() in ("true", "1", "yes")
        for item in batch:
            item.keep = True if disable_filtering else False
            item.reason = reason
            item.urgency = "low"
            item.rewritten = None
