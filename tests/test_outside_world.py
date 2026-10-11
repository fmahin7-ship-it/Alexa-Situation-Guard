"""Outside-world sources: weather, public holidays, calendar import, place lookup.

Recorded data only (a real Open-Meteo forecast and the real 2026 AU holiday list);
Amazon Location is a botocore Stubber. No network, no cost.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
import pytest
from botocore.stub import Stubber
from mcp import Client

from app.core.models import Location
from app.events.holidays.calendar import Holiday, closed_all_day, holiday_on
from app.events.holidays.nager import RecordedHolidayProvider, make_holiday_provider, parse_nager
from app.events.weather.forecast import Forecast, HourlyForecast, wet_windows
from app.events.weather.open_meteo import OpenMeteoProvider, RecordedWeatherProvider, make_weather_provider
from app.mcp.server import build_server
from app.options.reschedule import RescheduleSource
from app.situations.importers.calendar_ics import CalendarError, events_on, parse_ics
from app.situations.places import make_place_lookup, place_named
from app.situations.places.amazon_location import AmazonPlaceLookup, PlaceLookupError, search_text_params
from app.situations.store import SituationStore

SYDNEY = ZoneInfo("Australia/Sydney")


class FakeLookup:
    """Stands in for Amazon Location: knows one real park near the recorded forecast."""

    name, live = "fake-places", True

    def look_up(self, name: str, near: Optional[List[float]] = None) -> Location:
        return Location(id="loc_barangaroo_reserve", name="Barangaroo Reserve", latitude=-33.8574, longitude=151.2023)


class ToolFailed(Exception):
    pass


class Tools:
    def __init__(self, tmp_path, lookup=None):
        self.store = SituationStore(tmp_path)
        self.server = build_server(
            self.store,
            [RescheduleSource()],
            weather=RecordedWeatherProvider(),
            holidays=RecordedHolidayProvider(),
            place_lookup=lookup,
        )

    def __call__(self, tool: str, **arguments: Any) -> Dict[str, Any]:
        async def go():
            async with Client(self.server) as client:
                return await client.call_tool(tool, arguments)

        result = asyncio.run(go())
        if result.is_error:
            raise ToolFailed(result.content[0].text)
        content = result.structured_content
        return content["result"] if set(content) == {"result"} else content


@pytest.fixture
def tools(tmp_path):
    return Tools(tmp_path, lookup=FakeLookup())


# --- weather ----------------------------------------------------------------------------


def _outdoor_dinner(
    tools: Tools, start: str = "2026-10-12T21:00:00+11:00", now: str = "2026-10-12T12:00:00+11:00"
) -> Dict[str, str]:
    sid = tools("create_situation", name="Dinner outside", now=now)["situation_id"]
    park = tools("add_place", situation_id=sid, name="Barangaroo Reserve", outdoor=True, look_up=True)
    dinner = tools("add_commitment", situation_id=sid, owner="me", action="dinner outside", start=start,
                   duration_minutes=90, at_place=park["added_id"])
    return {"sid": sid, "park": park["added_id"], "dinner": dinner["added_id"], "notes": park["notes"]}


def test_rain_at_an_outdoor_place_breaks_dinner_and_reschedule_moves_it(tools):
    ids = _outdoor_dinner(tools)
    assert ids["notes"] == ["Found with fake-places"]
    checked = tools("check_weather", situation_id=ids["sid"])
    assert checked["live"] is False and checked["source"] == "open-meteo (recorded)"
    assert checked["attribution"] == "Weather data by Open-Meteo.com (CC BY 4.0)"
    assert checked["changes"] == [
        "Barangaroo Reserve: rain likely (up to 82%), 2026-10-12T21:00:00+11:00 to 2026-10-13T00:00:00+11:00"
    ]
    assert checked["feasible_before"] is True and checked["feasible_after"] is False
    assert "while it is unavailable" in checked["reasons"][0]

    found = tools("find_options", situation_id=ids["sid"], commitment_id=ids["dinner"])
    summaries = [o["summary"] for o in found["options"]]
    assert summaries[1] == (
        "B | Move me's dinner outside to 19:30-21:00, before Barangaroo Reserve unavailable (rain likely (up to 82%))"
    )
    assert tools("try_option", situation_id=ids["sid"], label="B")["feasible"] is True


def test_weather_refresh_replaces_its_own_windows(tools):
    ids = _outdoor_dinner(tools)
    tools("check_weather", situation_id=ids["sid"])
    tools("check_weather", situation_id=ids["sid"])
    park = next(l for l in tools.store.load(ids["sid"]).situation.locations if l.id == ids["park"])
    assert len(park.unavailable) == 1


def test_a_dry_evening_stays_fine(tools):
    ids = _outdoor_dinner(tools, start="2026-10-11T18:00:00+11:00", now="2026-10-11T09:00:00+11:00")
    checked = tools("check_weather", situation_id=ids["sid"])  # Sunday evening: 0% rain in the recording
    assert checked["changes"] == ["Barangaroo Reserve: dry"]
    assert checked["feasible_after"] is True


def test_weather_needs_an_outdoor_place_with_coordinates(tmp_path):
    tools = Tools(tmp_path, lookup=None)  # lookups off
    sid = tools("create_situation", name="BBQ", now="2026-10-12T12:00:00+11:00")["situation_id"]
    with pytest.raises(ToolFailed, match="no outdoor places"):
        tools("check_weather", situation_id=sid)
    garden = tools("add_place", situation_id=sid, name="back garden", outdoor=True, look_up=True)
    assert "Looking up 'back garden' is off" in garden["notes"][0]
    tools("add_commitment", situation_id=sid, owner="me", action="bbq", start="2026-10-12T21:00:00+11:00",
          duration_minutes=60, at_place=garden["added_id"])
    with pytest.raises(ToolFailed, match="no map coordinates"):
        tools("check_weather", situation_id=sid)


def test_wet_hours_are_merged_and_the_threshold_is_respected():
    hours = [HourlyForecast(time=f"2026-10-12T{h:02d}:00:00+11:00", rain_probability=p)
             for h, p in [(18, 20), (19, 70), (20, 65), (21, 40), (22, 90)]]
    forecast = Forecast(provider="t", live=False, latitude=0, longitude=0, hours=hours)
    day = datetime(2026, 10, 12, tzinfo=SYDNEY), datetime(2026, 10, 13, tzinfo=SYDNEY)
    assert [(w.start[11:16], w.end[11:16]) for w in wet_windows(forecast, *day, threshold=60)] == [("19:00", "21:00"), ("22:00", "23:00")]
    assert [(w.start[11:16], w.end[11:16]) for w in wet_windows(forecast, *day, threshold=80)] == [("22:00", "23:00")]


def test_recorded_weather_refuses_far_away_places_and_modes_are_settings(monkeypatch):
    with pytest.raises(Exception, match="No recorded forecast near"):
        RecordedWeatherProvider().forecast(-37.81, 144.96, "Australia/Melbourne")
    monkeypatch.delenv("SITUATION_GUARD_WEATHER", raising=False)
    assert make_weather_provider().live is False
    monkeypatch.setenv("SITUATION_GUARD_WEATHER", "live")
    assert isinstance(make_weather_provider(), OpenMeteoProvider)  # created, not called


# --- public holidays ----------------------------------------------------------------------


def _review_on_labour_day(tools: Tools) -> Dict[str, str]:
    sid = tools("create_situation", name="Labour Day week", now="2026-10-01T09:00:00+10:00")["situation_id"]
    office = tools("add_place", situation_id=sid, name="office", closes_on_public_holidays=True)["added_id"]
    review = tools("add_commitment", situation_id=sid, owner="me", action="quarterly review",
                   start="2026-10-05T10:00:00+11:00", duration_minutes=60, at_place=office)["added_id"]
    return {"sid": sid, "office": office, "review": review}


def test_office_closed_on_nsw_labour_day(tools):
    ids = _review_on_labour_day(tools)
    checked = tools("check_public_holidays", situation_id=ids["sid"])
    assert checked["changes"] == ["office closed on 2026-10-05: Labour Day"]
    assert checked["feasible_before"] is True and checked["feasible_after"] is False


def test_labour_day_is_not_a_holiday_in_victoria(tools):
    ids = _review_on_labour_day(tools)
    checked = tools("check_public_holidays", situation_id=ids["sid"], region="AU-VIC")
    assert checked["changes"] == [] and checked["feasible_after"] is True
    assert checked["notes"] == ["No public holidays in AU-VIC on the days planned"]


def test_marking_an_existing_place_updates_it(tools):
    sid = tools("create_situation", name="Office", now="2026-10-01T09:00:00+10:00")["situation_id"]
    tools("add_place", situation_id=sid, name="office")
    with pytest.raises(ToolFailed, match="No places in this plan close"):
        tools("check_public_holidays", situation_id=sid)
    tools("add_place", situation_id=sid, name="office", closes_on_public_holidays=True)
    assert tools.store.load(sid).situation.locations[0].closes_on_public_holidays is True


def test_holiday_regions_and_recordings():
    hs = RecordedHolidayProvider().holidays(2026, "au")
    assert holiday_on(hs, date(2026, 10, 5), "AU-NSW").name == "Labour Day"
    assert holiday_on(hs, date(2026, 12, 25), "AU-WA").name == "Christmas Day"  # nationwide
    assert closed_all_day(Holiday(date="2026-10-05", name="X"), "Australia/Sydney").end == "2026-10-06T00:00:00+11:00"
    assert parse_nager([{"date": "2026-01-01", "name": "New Year", "global": True, "counties": None}])[0].regions is None
    with pytest.raises(Exception, match="No recorded holidays for AU 2027"):
        RecordedHolidayProvider().holidays(2027, "AU")


def test_holiday_mode_is_a_setting(monkeypatch):
    monkeypatch.delenv("SITUATION_GUARD_HOLIDAYS", raising=False)
    assert make_holiday_provider().live is False


# --- calendar import ----------------------------------------------------------------------


def test_import_a_work_day_then_the_boss_adds_a_meeting(tools):
    sid = tools("create_situation", name="Monday at work", now="2026-10-12T08:00:00+11:00")["situation_id"]
    imported = tools("import_calendar", situation_id=sid, day="2026-10-12")
    assert imported["changes"] == ["Team stand-up 09:30-09:45", "Team meeting 14:00-15:00", "Design review 15:00-16:00"]
    assert imported["notes"] == ["'Team stand-up' repeats; only this occurrence was added"]
    assert imported["feasible_after"] is True

    again = tools("import_calendar", situation_id=sid, day="2026-10-12")
    assert again["added_ids"] == [] and len(again["notes"]) == 4  # 3 already imported + nothing new

    boss = tools("add_commitment", situation_id=sid, owner="me", action="meeting with boss",
                 start="2026-10-12T13:00:00+11:00", duration_minutes=120, at_place="loc_office")
    assert boss["feasible"] is False


def test_calendar_errors_are_clear(tools):
    sid = tools("create_situation", name="x", now="2026-10-12T08:00:00+11:00")["situation_id"]
    with pytest.raises(ToolFailed, match="No calendar 'home'. Available: work"):
        tools("import_calendar", situation_id=sid, day="2026-10-12", calendar="home")
    with pytest.raises(ToolFailed, match="YYYY-MM-DD"):
        tools("import_calendar", situation_id=sid, day="12/10/2026")


def test_ics_reader_handles_utc_folding_all_day_and_refuses_floating_times():
    ics = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:1\nDTSTART:20261012T040000Z\nDTEND:20261012T050000Z\n"
        "SUMMARY:Long title that is\n  folded onto two lines\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:2\nDTSTART;VALUE=DATE:20261012\nSUMMARY:All day\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    events = parse_ics(ics)
    assert events[0].title == "Long title that is folded onto two lines"
    assert [e.title for e in events_on(events, date(2026, 10, 12), SYDNEY)] == ["Long title that is folded onto two lines"]
    assert events_on(events, date(2026, 10, 12), SYDNEY)[0].start.astimezone(SYDNEY).hour == 15
    with pytest.raises(CalendarError, match="no time zone"):
        parse_ics("BEGIN:VEVENT\nDTSTART:20261012T100000\nEND:VEVENT\n")


# --- place lookup (Amazon Location Places, stubbed) -----------------------------------------


def _stubbed_lookup():
    client = boto3.client("geo-places", region_name="ap-southeast-2", aws_access_key_id="t", aws_secret_access_key="t")
    return AmazonPlaceLookup(client=client), Stubber(client)


def test_amazon_lookup_sends_a_storage_search_and_reads_the_first_result():
    lookup, stub = _stubbed_lookup()
    reply = {
        "ResultItems": [{
            "PlaceId": "p1", "PlaceType": "PointOfInterest", "Title": "Sydney Opera House",
            "Position": [151.2153, -33.8568], "Address": {"Label": "Bennelong Point, Sydney NSW 2000"},
        }],
        "PricingBucket": "Core",
    }
    stub.add_response("search_text", reply, expected_params=search_text_params("Sydney Opera House"))
    with stub:
        place = lookup.look_up("Sydney Opera House")
    assert (place.id, place.latitude, place.address) == ("loc_sydney_opera_house", -33.8568, "Bennelong Point, Sydney NSW 2000")
    assert search_text_params("x")["IntendedUse"] == "Storage"


def test_amazon_lookup_failures_leave_a_plain_place():
    lookup, stub = _stubbed_lookup()
    stub.add_response("search_text", {"ResultItems": [], "PricingBucket": "Core"})
    stub.add_client_error("search_text", "AccessDeniedException", "not allowed")
    with stub:
        with pytest.raises(PlaceLookupError, match="No place found"):
            lookup.look_up("Nowhere at all")
        place, notes = place_named("Opera House cafe", look_up=True, lookup=lookup)
    assert place.latitude is None and "AccessDeniedException" in notes[0]


def test_place_lookup_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SITUATION_GUARD_PLACES", raising=False)
    assert make_place_lookup() is None
    monkeypatch.setenv("SITUATION_GUARD_PLACES", "live")
    assert isinstance(make_place_lookup(), AmazonPlaceLookup)  # created, not called
