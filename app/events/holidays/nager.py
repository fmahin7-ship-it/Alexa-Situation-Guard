"""Nager.Date public holidays: live (free, keyless, private/non-profit use) or replayed from a recording.

    SITUATION_GUARD_HOLIDAYS=recorded   (default) replay data/recorded/holidays/, free and offline
    SITUATION_GUARD_HOLIDAYS=live       ask date.nager.at
"""

from __future__ import annotations

import json
import os
from typing import Any, List

from app.events.holidays.calendar import Holiday, HolidayError, HolidayProvider
from app.http_json import FetchError, get_json
from app.paths import RECORDED_DIR

HOLIDAYS_ENV = "SITUATION_GUARD_HOLIDAYS"
URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
RECORDINGS = RECORDED_DIR / "holidays"


def parse_nager(data: Any) -> List[Holiday]:
    if not isinstance(data, list):
        raise HolidayError("Unexpected holiday list shape")
    try:
        return [Holiday(date=h["date"], name=h["name"], regions=None if h.get("global") else h.get("counties")) for h in data]
    except (KeyError, TypeError) as exc:
        raise HolidayError(f"Unexpected holiday entry: missing {exc}") from exc


class NagerProvider:
    name = "nager.date"
    live = True

    def holidays(self, year: int, country: str) -> List[Holiday]:
        try:
            return parse_nager(get_json(URL.format(year=year, country=country.upper())))
        except FetchError as exc:
            raise HolidayError(f"Nager.Date could not answer: {exc}") from exc


class RecordedHolidayProvider:
    name = "nager.date (recorded)"
    live = False

    def holidays(self, year: int, country: str) -> List[Holiday]:
        path = RECORDINGS / f"{country.upper()}_{year}.json"
        if not path.exists():
            raise HolidayError(f"No recorded holidays for {country.upper()} {year}; use {HOLIDAYS_ENV}=live")
        return parse_nager(json.loads(path.read_text(encoding="utf-8")))


def make_holiday_provider() -> HolidayProvider:
    choice = os.environ.get(HOLIDAYS_ENV, "recorded").strip().lower()
    if choice == "recorded":
        return RecordedHolidayProvider()
    if choice == "live":
        return NagerProvider()
    raise HolidayError(f"{HOLIDAYS_ENV} must be 'recorded' or 'live', not '{choice}'")
