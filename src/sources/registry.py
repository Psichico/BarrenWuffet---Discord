"""Maps config source name -> Source class (plug-and-play wiring).

To add a new source: write the module, add one line here, add a config block.
Nothing else changes.
"""

from __future__ import annotations

from src.sources.base import Source
from src.sources.finviz import FinvizNewsSource

# from src.sources.finviz_screener import FinvizScreenerSource  # v2: uncomment
# from src.sources.x import XSource                             # future

SOURCE_REGISTRY = {
    "finviz_news": FinvizNewsSource,
    # "finviz_screener": FinvizScreenerSource,
    # "x": XSource,
}


def build_source(name: str, cfg: dict) -> Source:
    if name not in SOURCE_REGISTRY:
        raise ValueError(f"Unknown source: {name}")
    return SOURCE_REGISTRY[name](**cfg)
