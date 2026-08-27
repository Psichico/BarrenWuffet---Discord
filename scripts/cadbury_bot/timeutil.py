"""DST-correct US-Eastern time helpers.

The core's ``Orchestrator._get_sleep_duration`` uses a fixed UTC-4 offset, which
is fine for its coarse weekday/weekend decision. The recap fires at a specific
wall-clock minute (16:30 ET), so it needs real DST handling — hence ``zoneinfo``
(``tzdata`` is in requirements.txt for slim/Windows environments).
"""

from __future__ import annotations

import datetime as _dt

try:  # stdlib since 3.9; tzdata package supplies the database where the OS lacks it
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 only
    ZoneInfo = None  # type: ignore


def _tz(timezone: str):
    if ZoneInfo is None:  # pragma: no cover
        # Last-resort fallback: fixed UTC-4. Better than crashing.
        return _dt.timezone(_dt.timedelta(hours=-4))
    try:
        return ZoneInfo(timezone)
    except Exception:  # pragma: no cover - bad tz name / missing tzdata
        return _dt.timezone(_dt.timedelta(hours=-4))


def et_now(timezone: str = "America/New_York") -> _dt.datetime:
    """Timezone-aware 'now' in the given zone."""
    return _dt.datetime.now(_tz(timezone))


def et_today(timezone: str = "America/New_York") -> str:
    """Today's calendar date in the given zone, as 'YYYY-MM-DD'."""
    return et_now(timezone).date().isoformat()
