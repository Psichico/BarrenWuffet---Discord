"""Entrypoint: builds components from config, runs the orchestrator.

    python -m src.main

Everything is assembled from config.yaml + env via the registries — no concrete
source/sink is referenced here directly.
"""

from __future__ import annotations

import logging
import os
import sys

from scripts.cadbury_bot import build_cadbury  # optional bolt-on; None when off
from src.config import load_settings
from src.dedupe import SeenStore
from src.filter.claude_filter import ClaudeFilter
from src.orchestrator import Orchestrator
from src.sinks.registry import build_sink
from src.sources.registry import build_source


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,  # Render captures stdout
    )


def build_orchestrator(config_path: str = "config.yaml") -> Orchestrator:
    settings = load_settings(config_path)

    sources = [
        build_source(name, cfg or {})
        for name, cfg in settings.sources.items()
    ]
    sinks = [
        build_sink(name, cfg or {})
        for name, cfg in settings.sinks.items()
    ]

    # ClaudeFilter builds its OpenRouter (OpenAI-compatible) client lazily on
    # first use from the OR_NewsFilterAPI env var — imports don't need the SDK/key.
    claude_filter = ClaudeFilter(settings.llm)

    store = SeenStore(settings.dedupe.path)

    # Optional end-of-day recap email. `None` unless config.yaml has an enabled
    # `cadbury_bot:` block — see scripts/cadbury_bot/README.md.
    cadbury = build_cadbury(config_path, settings.dedupe.path)

    return Orchestrator(
        sources=sources,
        filter_=claude_filter,
        sinks=sinks,
        store=store,
        poll_interval_seconds=settings.poll_interval_seconds,
        poll_interval_weekend_seconds=settings.poll_interval_weekend_seconds,
        batch_size=settings.llm.batch_size,
        prune_older_than_days=settings.dedupe.prune_older_than_days,
        warm_start=settings.warm_start,
        on_item_kept=(cadbury.recorder.record if cadbury else None),
        post_iteration_hook=(cadbury.tick if cadbury else None),
    )


def main() -> None:
    _configure_logging()
    log = logging.getLogger("main")
    log.info("Starting news-bot...")
    orchestrator = build_orchestrator()
    log.info("Built %d source(s), %d sink(s). Poll interval %ds.",
             len(orchestrator.sources), len(orchestrator.sinks),
             orchestrator.poll_interval_seconds)
    orchestrator.run_forever()


if __name__ == "__main__":
    main()
