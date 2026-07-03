"""Maps config sink name -> Sink class.

Note both Discord channels are the SAME DiscordSink class, instantiated twice
with different config (webhook + min_urgency). Adding the urgent channel is
config-only — no new code.
"""

from __future__ import annotations

from src.sinks.base import Sink
from src.sinks.discord import DiscordSink

SINK_REGISTRY = {
    "discord_news": DiscordSink,     # both keys map to the SAME class;
    "discord_urgent": DiscordSink,   # config gives each a different min_urgency + webhook
}


def build_sink(name: str, cfg: dict) -> Sink:
    if name not in SINK_REGISTRY:
        raise ValueError(f"Unknown sink: {name}")
    # The sink's ``name`` is its config key; pass it through explicitly.
    return SINK_REGISTRY[name](name=name, **cfg)
