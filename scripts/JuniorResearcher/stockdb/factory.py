"""
Provider factory.

Kept in its own tiny module (rather than inside cli.py) so both the CLI and the
agent-facing API can build a provider without importing each other. This is the
one place to change the default provider wiring later.
"""

from __future__ import annotations

from .config import Settings
from .providers.alphavantage import AlphaVantageProvider
from .providers.base import Provider
from .ratelimit import RateLimiter


def build_provider(settings: Settings) -> Provider:
    """Construct the default provider (Alpha Vantage) with a shared rate limiter."""
    limiter = RateLimiter(per_minute=settings.av_rate_limit_per_min)
    return AlphaVantageProvider(
        api_key=settings.alphavantage_key,
        rate_limiter=limiter,
        news_limit=settings.news_limit,
    )
