"""cadbury_bot — end-of-day Market Recap email.

Self-contained, optional bolt-on. The core (`src/`) only calls two generic,
feature-agnostic hooks (`on_item_kept`, `post_iteration_hook`); everything
specific to the recap lives in this package. See ``README.md`` for the removal
checklist.

Public entrypoint: ``build_cadbury(config_path, db_path) -> CadburyBot | None``.
Returns ``None`` when the ``cadbury_bot:`` config block is absent or disabled, so
wiring it into ``src/main.py`` is a no-op unless it is turned on.
"""

from __future__ import annotations

import logging

from scripts.cadbury_bot.bot import CadburyBot
from scripts.cadbury_bot.composer import compose
from scripts.cadbury_bot.config import CadburyConfig, load_cadbury_config
from scripts.cadbury_bot.mailer import EmailMailer
from scripts.cadbury_bot.recorder import KeptItemRecorder
from scripts.cadbury_bot.store import CadburyStore

logger = logging.getLogger(__name__)

__all__ = [
    "CadburyBot",
    "CadburyConfig",
    "CadburyStore",
    "EmailMailer",
    "KeptItemRecorder",
    "build_cadbury",
    "load_cadbury_config",
]


def build_cadbury(config_path: str = "config.yaml",
                  db_path: str = "./seen.db") -> "CadburyBot | None":
    """Assemble the feature from config, or return ``None`` if it is off.

    ``db_path`` is the SAME SQLite file the core uses (``settings.dedupe.path``);
    cadbury_bot opens its own connection to it and owns only the ``cadbury_*``
    tables.
    """
    cfg = load_cadbury_config(config_path)
    if cfg is None or not cfg.enabled:
        logger.info("cadbury_bot: disabled (no config block or enabled: false).")
        return None

    store = CadburyStore(db_path)
    recorder = KeptItemRecorder(store, cfg.timezone)
    mailer = EmailMailer(cfg)
    bot = CadburyBot(cfg=cfg, store=store, recorder=recorder,
                     composer=compose, mailer=mailer)
    logger.info("cadbury_bot: enabled — recap at %02d:%02d %s to %s",
                cfg.send_hour, cfg.send_minute, cfg.timezone,
                ", ".join(cfg.recipients) or "(no recipients!)")
    return bot
