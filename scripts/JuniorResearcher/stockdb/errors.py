"""
Typed exceptions for the whole package.

WHY TYPED ERRORS (guardrail): callers (and other AI agents) can distinguish an
expected/handleable condition (e.g. a rate-limit note from the API, a bad
symbol) from an unexpected bug. Ingestion catches provider errors PER data type
so one failure doesn't abort the others (graceful degradation, PLAN.md §9).
"""

from __future__ import annotations


class StockDBError(Exception):
    """Base class for every error this package raises on purpose."""


# --- Configuration ---------------------------------------------------------
class ConfigError(StockDBError):
    """A required setting is missing or invalid at startup."""


# --- Symbols ---------------------------------------------------------------
class InvalidSymbolError(StockDBError):
    """A symbol could not be normalized to a valid canonical US ticker."""


# --- Provider / network ----------------------------------------------------
class ProviderError(StockDBError):
    """Base for anything that goes wrong talking to an external data provider."""


class RateLimitError(ProviderError):
    """
    The provider signalled we are over our call budget.

    Alpha Vantage returns HTTP 200 with a JSON "Note"/"Information" field in this
    case (NOT an HTTP 429), so we detect it by inspecting the body and raise this.
    Retries/backoff are handled by the caller.
    """


class ProviderResponseError(ProviderError):
    """
    The provider returned an error payload or an unusable/malformed response
    (e.g. AV "Error Message" for an unknown symbol, or unparseable JSON).
    """


class DataValidationError(StockDBError):
    """A fetched record failed validation and must not be written to the DB."""
