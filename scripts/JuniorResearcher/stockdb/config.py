"""
Configuration loading + validation.

All runtime configuration comes from environment variables (optionally loaded
from a local .env file via python-dotenv). Nothing is hard-coded and NO secret
is ever logged.

Usage:
    from stockdb.config import get_settings
    settings = get_settings()          # cached; validated once
    settings.database_url              # -> str
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

from .errors import ConfigError

# Load .env into os.environ if present. Real environment variables always win
# over .env (load_dotenv does not override existing ones by default).
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Immutable, validated view of all configuration."""

    database_url: str
    alphavantage_key: str
    av_rate_limit_per_min: int
    ttl_days: int
    news_limit: int
    log_level: str


def _require(name: str) -> str:
    """Return a required env var or raise a clear ConfigError."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required environment variable {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    """Parse an integer env var with a default and a sanity floor."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Build and cache the Settings object.

    Cached with lru_cache so repeated calls are cheap and configuration is read
    exactly once per process. Raises ConfigError early (fail fast) if anything
    required is missing — better to crash at startup than mid-ingest.
    """
    return Settings(
        database_url=_require("DATABASE_URL"),
        alphavantage_key=_require("ALPHAVANTAGE_KEY"),
        av_rate_limit_per_min=_int_env("AV_RATE_LIMIT_PER_MIN", default=75),
        ttl_days=_int_env("TTL_DAYS", default=2),
        news_limit=_int_env("NEWS_LIMIT", default=1000),
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
