"""The normalized unit passed between all pipeline stages.

Sources produce ``NewsItem``s, the filter mutates them (keep/urgency/reason/
rewritten), and sinks consume them. Keeping this the single shared data type is
what lets sources and sinks stay decoupled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class NewsItem:
    # --- required, set by the Source ---
    id: str                    # STABLE unique id for dedupe (see note below)
    source: str                # source name, e.g. "finviz_news", "x"
    headline: str
    url: str

    # --- optional, set by the Source when available ---
    ticker: Optional[str] = None
    publisher: Optional[str] = None      # e.g. "Bloomberg", or X handle
    published_at: Optional[datetime] = None
    raw_text: Optional[str] = None       # full body if the source has it

    # --- set by the Filter stage ---
    keep: bool = False                   # Claude's decision
    reason: str = ""                     # why kept/dropped (for logs)
    rewritten: Optional[str] = None      # Claude's rewritten version, if enabled
    urgency: str = "low"                 # "high" | "medium" | "low" — drives sink routing

    # --- freeform extra fields per source ---
    meta: dict = field(default_factory=dict)


# NOTE — the ``id`` field:
# Dedupe correctness depends entirely on this being STABLE across polls. Prefer
# the item's canonical URL. If a source lacks stable URLs, hash
# (source + headline + published_date). Never use a fetch timestamp or a random
# value.
