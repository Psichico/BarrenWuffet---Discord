"""
Symbol normalization -> a single canonical, US-only ticker form.

WHY THIS MATTERS: the same company must never land in the DB under two keys
(e.g. "aapl" and "AAPL", or "BRK.B" and "BRK-B"). `tickers.symbol` is UNIQUE, so
we normalize BEFORE the symbol ever reaches the database.

THE TRICKY CASE (resolved deliberately):
    "BRK.B"    -> a SHARE CLASS. Alpha Vantage expects "BRK-B" (dash).
    "0700.HK"  -> an EXCHANGE SUFFIX (Hong Kong). Non-US -> rejected.
Both use a dot, so order matters: we detect the share-class pattern FIRST and
convert its dot to a dash; only then do we treat any remaining dot as an
exchange suffix to strip. See PLAN.md §5.
"""

from __future__ import annotations

import re

from .errors import InvalidSymbolError

# A share class looks like 1-4 letters, a dot, then exactly ONE letter: BRK.B, BF.B.
_CLASS_SHARE_RE = re.compile(r"^[A-Z]{1,4}\.[A-Z]$")

# The canonical, accepted US form: 1-5 letters, optionally a "-X" share class.
# Examples that pass: AAPL, MSFT, BRK-B, BF-B.  Digits or extra suffixes fail.
_CANONICAL_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")


def normalize_symbol(raw: str) -> str:
    """
    Normalize a raw user/provider symbol to the canonical US form.

    Returns the canonical symbol (uppercase). Raises InvalidSymbolError for
    anything that isn't a valid US ticker (including non-US exchange-suffixed
    symbols like '0700.HK'), so a bad symbol can never silently propagate.
    """
    if raw is None:
        raise InvalidSymbolError("Symbol is None")

    symbol = raw.strip().upper()
    if not symbol:
        raise InvalidSymbolError("Symbol is empty")

    # 1) Share class first: BRK.B -> BRK-B (dot becomes dash).
    if _CLASS_SHARE_RE.match(symbol):
        symbol = symbol.replace(".", "-")
    # 2) Any OTHER dot is treated as an exchange suffix; drop it and everything
    #    after. The stripped base is then validated below (non-US bases like
    #    "0700" contain digits and will be rejected).
    elif "." in symbol:
        symbol = symbol.split(".", 1)[0]

    # 3) Final validation against the canonical US shape.
    if not _CANONICAL_RE.match(symbol):
        raise InvalidSymbolError(
            f"{raw!r} does not normalize to a valid US ticker (got {symbol!r}). "
            f"Only US symbols are supported (no exchange suffixes)."
        )

    return symbol
