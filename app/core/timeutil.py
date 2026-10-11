"""Reading and shifting times in the forms a situation uses.

A situation uses one form throughout: "HH:MM", "YYYY-MM-DD", or ISO datetimes
(with an offset when the situation has a time zone). Shifted times keep the
form they came in, and offset-aware times are re-expressed in the situation's
zone, so crossing a daylight-saving change gives the right offset.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


def parse_moment(value: Optional[str]) -> Optional[datetime]:
    """Parse HH:MM, YYYY-MM-DD, or ISO datetime (optionally with offset). None if absent or unparseable."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%H:%M"):
        try:
            dt = datetime.strptime(value, fmt)
            if fmt == "%H:%M":
                return dt.replace(year=2000, month=1, day=1)
            return dt
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _detect_format(value: str) -> str:
    value = value.strip()
    if len(value) == 5 and value[2] == ":":
        return "%H:%M"
    if "T" in value:
        return "%Y-%m-%dT%H:%M"
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return "%Y-%m-%d"
    return "%Y-%m-%dT%H:%M"


def shift_moment(value: Optional[str], minutes: int, zone: Optional[ZoneInfo] = None) -> Optional[str]:
    """`value` moved by `minutes`, in the same form. Unparseable values come back unchanged."""
    if not value:
        return value
    dt = parse_moment(value)
    if dt is None:
        return value
    shifted = dt + timedelta(minutes=minutes)
    if shifted.tzinfo is not None:
        if zone is not None:
            shifted = shifted.astimezone(zone)
        return shifted.isoformat()
    return shifted.strftime(_detect_format(value))


def minutes_between(start: Optional[str], end: Optional[str]) -> Optional[int]:
    """Whole minutes from start to end, or None if either cannot be read."""
    a, b = parse_moment(start), parse_moment(end)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() // 60)


def zone_of(timezone: Optional[str]) -> Optional[ZoneInfo]:
    return ZoneInfo(timezone) if timezone else None
