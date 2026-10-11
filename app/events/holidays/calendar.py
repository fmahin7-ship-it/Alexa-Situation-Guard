"""Provider-neutral public holidays, and turning one into a day a place is closed."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.core.models import Unavailability

SOURCE = "holidays"


class HolidayError(Exception):
    """No holiday list could be had: no network, no recording for that year/country, ..."""


class Holiday(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    name: str
    regions: Optional[List[str]] = Field(None, description="Where it applies (e.g. AU-NSW); None = nationwide")


class HolidayProvider(Protocol):
    name: str
    live: bool

    def holidays(self, year: int, country: str) -> List[Holiday]:
        """Public holidays for a year and country (ISO code, e.g. AU), or raise HolidayError."""
        ...


def holiday_on(holidays: List[Holiday], day: date, region: Optional[str]) -> Optional[Holiday]:
    """The holiday on `day` that applies in `region` (nationwide ones apply everywhere)."""
    for holiday in holidays:
        if holiday.date == day.isoformat() and (holiday.regions is None or region in holiday.regions):
            return holiday
    return None


def closed_all_day(holiday: Holiday, timezone: Optional[str]) -> Unavailability:
    """The whole local day, in the situation's form (offset-aware when it has a time zone)."""
    day = date.fromisoformat(holiday.date)
    zone = ZoneInfo(timezone) if timezone else None
    start = datetime.combine(day, time(0), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone)
    as_text = (lambda dt: dt.isoformat()) if zone else (lambda dt: dt.strftime("%Y-%m-%dT%H:%M"))
    return Unavailability(start=as_text(start), end=as_text(end), reason=f"{holiday.name} (public holiday)", source=SOURCE)
