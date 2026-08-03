"""
Technical indicators, computed from a chronological list of closing prices.

WHY COMPUTED, NOT STORED (PLAN.md §5): indicators are pure functions of price
history. Storing them would duplicate data and force re-fetching on every tweak.
Computing at read-time keeps the DB lean and the math transparent.

All functions take a list of floats ordered OLDEST -> NEWEST and return the most
recent value(s), or None when there isn't enough history. Indicator math uses
float (standard practice); prices themselves remain Decimal in the DB.
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def sma(closes: List[float], period: int) -> Optional[float]:
    """Simple moving average of the last `period` closes."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema_series(values: List[float], period: int) -> List[float]:
    """Exponential moving average series (same length as `values`)."""
    k = 2.0 / (period + 1)
    ema = values[0]
    out = [ema]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Relative Strength Index using Wilder's smoothing. Returns 0-100, or None if
    there is not enough history (need > `period` closes).
    """
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(d for d in deltas[:period] if d > 0) / period
    avg_loss = sum(-d for d in deltas[:period] if d < 0) / period
    for d in deltas[period:]:
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    MACD. Returns (macd_line, signal_line, histogram) latest values, or
    (None, None, None) if there isn't enough history.
    """
    if len(closes) < slow:
        return (None, None, None)
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = _ema_series(macd_line, signal)
    return (macd_line[-1], signal_line[-1], macd_line[-1] - signal_line[-1])


def compute_indicators(closes: List[float]) -> dict:
    """
    Convenience bundle used by the read layer. Rounds for compact output.
    `closes` must be OLDEST -> NEWEST.
    """
    macd_line, macd_signal, macd_hist = macd(closes)

    def _r(x: Optional[float], nd: int = 4) -> Optional[float]:
        return round(x, nd) if x is not None else None

    return {
        "rsi_14": _r(rsi(closes)),
        "macd": _r(macd_line),
        "macd_signal": _r(macd_signal),
        "macd_hist": _r(macd_hist),
        "sma_50": _r(sma(closes, 50)),
        "sma_200": _r(sma(closes, 200)),
    }
