"""Calendar import from iCalendar (.ics) files — the format Google Calendar and Outlook export.

A small reader for what plans need: each event's title, start, end, place and id.
Handles times with a TZID, UTC times (...Z) and all-day dates. A repeating event
(RRULE) is read as its first occurrence only, and reported as such.

Calendars live in data/calendars/<name>.ics (or SITUATION_GUARD_CALENDAR_DIR).
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.paths import DATA_DIR

CALENDAR_DIR_ENV = "SITUATION_GUARD_CALENDAR_DIR"


class CalendarError(Exception):
    """The calendar could not be read."""


class CalendarEvent(BaseModel):
    uid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: Optional[str] = None
    repeats: bool = Field(False, description="Has a repeat rule; only the first occurrence is read")


def calendar_dir() -> Path:
    return Path(os.environ.get(CALENDAR_DIR_ENV) or DATA_DIR / "calendars")


def list_calendars() -> List[str]:
    folder = calendar_dir()
    return sorted(p.stem for p in folder.glob("*.ics")) if folder.exists() else []


def load_calendar(name: str) -> List[CalendarEvent]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        raise CalendarError(f"Invalid calendar name {name!r}")
    path = calendar_dir() / f"{name}.ics"
    if not path.exists():
        raise CalendarError(f"No calendar '{name}'. Available: {', '.join(list_calendars()) or 'none'}")
    return parse_ics(path.read_text(encoding="utf-8"))


# --- the reader -------------------------------------------------------------------


def _unfold(text: str) -> List[str]:
    """Join continuation lines (RFC 5545: a line starting with a space or tab continues the previous one)."""
    lines: List[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split(line: str) -> Tuple[str, Dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    return name.upper(), dict(p.split("=", 1) for p in params if "=" in p), value


def _unescape(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _moment(value: str, params: Dict[str, str]) -> Tuple[datetime, bool]:
    """(moment, is_all_day). Floating times without TZID or Z are refused: their zone is unknown."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value, "%Y%m%d").date()
        return datetime.combine(d, datetime.min.time()), True
    if value.endswith("Z"):
        return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc), False
    if "TZID" in params:
        try:
            zone = ZoneInfo(params["TZID"].strip('"'))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CalendarError(f"Unknown time zone {params['TZID']!r}") from exc
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=zone), False
    raise CalendarError(f"Time {value!r} has no time zone (TZID or Z)")


def parse_ics(text: str) -> List[CalendarEvent]:
    events: List[CalendarEvent] = []
    current: Optional[Dict[str, Tuple[Dict[str, str], str]]] = None
    for line in _unfold(text):
        name, params, value = _split(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            current = {}
        elif name == "END" and value.upper() == "VEVENT" and current is not None:
            events.append(_event(current))
            current = None
        elif current is not None:
            current.setdefault(name, (params, value))
    return events


def _event(props: Dict[str, Tuple[Dict[str, str], str]]) -> CalendarEvent:
    if "DTSTART" not in props:
        raise CalendarError("An event has no DTSTART")
    start, all_day = _moment(props["DTSTART"][1], props["DTSTART"][0])
    if "DTEND" in props:
        end, _ = _moment(props["DTEND"][1], props["DTEND"][0])
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(0))
    title = _unescape(props.get("SUMMARY", ({}, "(no title)"))[1])
    return CalendarEvent(
        uid=props.get("UID", ({}, f"{title}-{start.isoformat()}"))[1],
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        location=_unescape(props["LOCATION"][1]) if "LOCATION" in props else None,
        repeats="RRULE" in props,
    )


def events_on(events: List[CalendarEvent], day: date, zone: Optional[ZoneInfo]) -> List[CalendarEvent]:
    """Timed events starting that local day (all-day events are left out: they block nobody's time)."""
    chosen = []
    for event in events:
        if event.all_day:
            continue
        local = event.start.astimezone(zone) if zone else event.start
        if local.date() == day:
            chosen.append(event)
    return sorted(chosen, key=lambda e: e.start)
