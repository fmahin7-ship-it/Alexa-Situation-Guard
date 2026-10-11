"""Real-life features: Google Calendar, today's weather, umbrella advice, the live clock, home.

Google, Open-Meteo and Amazon Location are fakes here: no network, no cost, no real calendar.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest
from mcp import Client

from app.calendars import google
from app.calendars.google import GoogleCalendar, GoogleCalendarError, parse_google_events
from app.paths import STATE_DIR
from app.core.models import Location
from app.events.holidays.nager import RecordedHolidayProvider
from app.events.weather.forecast import Conditions, DayForecast, Forecast, GeoPoint, HourlyForecast, WeatherError
from app.events.weather.open_meteo import parse_conditions, parse_geocoding
from app.mcp.server import build_server
from app.options.reschedule import RescheduleSource
from app.situations.importers.calendar_ics import CalendarEvent
from app.situations.places import HOME_ENV, place_named
from app.situations.store import SituationStore

SYDNEY = ZoneInfo("Australia/Sydney")
DAY = "2026-10-12"


class FakeCalendar:
    """Stands in for Google Calendar: remembers what was created and moved."""

    name, live = "google-calendar", True

    def __init__(self, events: Optional[List[CalendarEvent]] = None):
        self.events = events or []
        self.created: List[Dict[str, Any]] = []
        self.moved: List[Dict[str, Any]] = []

    def events_on(self, day: date, zone: ZoneInfo) -> List[CalendarEvent]:
        return [e for e in self.events if e.start.astimezone(zone).date() == day]

    def create(self, title: str, start: str, end: str, timezone: str, location: Optional[str] = None) -> str:
        self.created.append({"title": title, "start": start, "end": end, "location": location})
        return f"evt{len(self.created)}"

    def move(self, event_id: str, start: str, end: str, timezone: str) -> None:
        self.moved.append({"id": event_id, "start": start, "end": end})


class FakeWeather:
    """Open-Meteo stand-in: rain from 17:00 on the day, 21°C now."""

    name, live = "open-meteo", True

    def forecast(self, latitude: float, longitude: float, timezone: str) -> Forecast:
        start = datetime(2026, 10, 12, 0, 0, tzinfo=SYDNEY)
        hours = [
            HourlyForecast(time=(start + timedelta(hours=h)).isoformat(), rain_probability=80 if h >= 17 else 10)
            for h in range(48)
        ]
        return Forecast(provider=self.name, live=True, latitude=latitude, longitude=longitude, hours=hours, attribution="Open-Meteo")

    def conditions(self, latitude: float, longitude: float, timezone: str) -> Conditions:
        return Conditions(
            provider=self.name, live=True, time="2026-10-12T09:00:00+11:00", temperature_c=21.4,
            feels_like_c=20.1, conditions="partly cloudy", attribution="Open-Meteo",
            days=[DayForecast(date=DAY, conditions="showers", min_c=15, max_c=24, rain_chance=80)],
        )

    def find(self, name: str) -> GeoPoint:
        if name.lower().startswith("melbourne"):
            return GeoPoint(name="Melbourne, Victoria, Australia", latitude=-37.81, longitude=144.96, timezone="Australia/Melbourne")
        raise WeatherError(f"No town or city called '{name}' was found")


class FakeLookup:
    name, live = "fake-places", True

    def look_up(self, name: str, near: Optional[List[float]] = None) -> Location:
        return Location(id="loc_found", name=name, latitude=-33.89, longitude=151.18)


class ToolFailed(Exception):
    pass


class Tools:
    def __init__(self, tmp_path, calendar=None, lookup=None):
        self.store = SituationStore(tmp_path)
        self.calendar = calendar
        self.server = build_server(
            self.store, [RescheduleSource()], weather=FakeWeather(), holidays=RecordedHolidayProvider(),
            place_lookup=lookup, calendar=calendar,
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


def _day_plan(tools: Tools) -> str:
    return tools("create_situation", name="My day Mon 12 Oct", now=f"{DAY}T09:00:00+11:00")["situation_id"]


# --- Google Calendar -------------------------------------------------------------------------


def test_google_events_are_parsed_and_cancelled_ones_dropped():
    events = parse_google_events([
        {"id": "a", "summary": "Stand-up", "start": {"dateTime": "2026-10-12T09:30:00+11:00"},
         "end": {"dateTime": "2026-10-12T09:45:00+11:00"}, "recurringEventId": "r1", "location": "Office"},
        {"id": "b", "status": "cancelled", "start": {"dateTime": "2026-10-12T10:00:00+11:00"},
         "end": {"dateTime": "2026-10-12T11:00:00+11:00"}},
        {"id": "c", "summary": "Holiday", "start": {"date": "2026-10-12"}, "end": {"date": "2026-10-13"}},
    ])
    assert [e.uid for e in events] == ["a", "c"]
    assert events[0].repeats and events[0].location == "Office" and not events[0].all_day
    assert events[1].all_day


class FakeResponse:
    def __init__(self, status: int, body: Dict[str, Any]):
        self.status_code, self._body, self.content, self.text = status, body, b"x", str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response, self.calls = response, []

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def test_google_calendar_asks_for_one_local_day_and_writes_events():
    session = FakeSession(FakeResponse(200, {"items": [
        {"id": "a", "summary": "Team meeting", "start": {"dateTime": "2026-10-12T14:00:00+11:00"},
         "end": {"dateTime": "2026-10-12T15:00:00+11:00"}},
    ]}))
    calendar = GoogleCalendar(session=session)
    events = calendar.events_on(date(2026, 10, 12), SYDNEY)
    method, _, kwargs = session.calls[0]
    assert method == "GET" and kwargs["params"]["timeMin"] == "2026-10-12T00:00:00+11:00"
    assert kwargs["params"]["singleEvents"] == "true"
    assert [e.title for e in events] == ["Team meeting"]

    session.response = FakeResponse(200, {"id": "new1"})
    assert calendar.create("Boss", "2026-10-12T14:00:00+11:00", "2026-10-12T15:00:00+11:00", "Australia/Sydney") == "new1"
    assert session.calls[-1][2]["json"]["summary"] == "Boss"


def test_google_refusal_is_a_clear_error():
    calendar = GoogleCalendar(session=FakeSession(FakeResponse(403, {"error": {"message": "Insufficient Permission"}})))
    with pytest.raises(GoogleCalendarError, match="403.*Insufficient Permission"):
        calendar.create("x", "2026-10-12T14:00:00+11:00", "2026-10-12T15:00:00+11:00", "Australia/Sydney")


def test_not_signed_in_says_how(tmp_path):
    with pytest.raises(GoogleCalendarError, match="app.calendars.google login"):
        GoogleCalendar(token_path=tmp_path / "none.json").events_on(date(2026, 10, 12), SYDNEY)


def test_google_token_is_kept_out_of_the_situation_folder(tmp_path, monkeypatch):
    assert STATE_DIR not in google.DEFAULT_TOKEN.parents
    old, new = tmp_path / "state" / "google_token.json", tmp_path / "secrets" / "google_token.json"
    old.parent.mkdir()
    old.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(google, "LEGACY_TOKEN", old)
    monkeypatch.setattr(google, "DEFAULT_TOKEN", new)
    monkeypatch.delenv(google.TOKEN_ENV, raising=False)

    assert google.token_location() == new  # an old token is moved, not lost: no new sign-in needed
    assert new.read_text(encoding="utf-8") == "{}" and not old.exists()
    assert GoogleCalendar().signed_in

    custom = tmp_path / "elsewhere" / "token.json"
    monkeypatch.setenv(google.TOKEN_ENV, str(custom))
    assert google.token_location() == custom


def test_schedule_then_clash_then_move_the_meeting_in_google(tmp_path):
    tools = Tools(tmp_path, calendar=FakeCalendar())
    plan = _day_plan(tools)

    boss = tools("add_commitment", situation_id=plan, owner="me", action="meeting with boss",
                 start=f"{DAY}T14:00:00+11:00", duration_minutes=60)
    assert boss["feasible"] is True
    added = tools("add_to_calendar", situation_id=plan, commitment_id=boss["added_id"])
    assert added["title"] == "Meeting with boss" and tools.calendar.created[0]["start"] == f"{DAY}T14:00:00+11:00"

    with pytest.raises(ToolFailed, match="already in Google Calendar"):
        tools("add_to_calendar", situation_id=plan, commitment_id=boss["added_id"])

    teammate = tools("add_commitment", situation_id=plan, owner="me", action="call with teammate",
                     start=f"{DAY}T14:00:00+11:00", duration_minutes=30)
    assert teammate["feasible"] is False  # one person, two things at 2pm
    with pytest.raises(ToolFailed, match="engine found a problem"):
        tools("add_to_calendar", situation_id=plan, commitment_id=teammate["added_id"])
    assert len(tools.calendar.created) == 1  # nothing clashing reached the real calendar

    options = tools("find_options", situation_id=plan, commitment_id=boss["added_id"])["options"]
    label = next(o["label"] for o in options if tools("try_option", situation_id=plan, label=o["label"])["feasible"])
    confirmed = tools("confirm_option", situation_id=plan, label=label)
    assert confirmed["verified"] is True
    assert tools.calendar.moved and tools.calendar.moved[0]["id"] == "evt1"
    assert confirmed["calendar_updates"][0].startswith("Moved 'Meeting with boss' in Google Calendar")


def test_google_meetings_come_in_and_are_not_doubled(tmp_path):
    meeting = CalendarEvent(uid="g1", title="Team meeting", start=datetime(2026, 10, 12, 14, tzinfo=SYDNEY),
                            end=datetime(2026, 10, 12, 15, tzinfo=SYDNEY))
    tools = Tools(tmp_path, calendar=FakeCalendar([meeting]))
    plan = _day_plan(tools)
    first = tools("import_calendar", situation_id=plan, day=DAY, calendar="google")
    assert first["live"] is True and first["changes"] == ["Team meeting 14:00-15:00"]
    again = tools("import_calendar", situation_id=plan, day=DAY, calendar="google")
    assert again["changes"] == [] and "already imported" in again["notes"][0]

    clash = tools("add_commitment", situation_id=plan, owner="me", action="talk to teammate",
                  start=f"{DAY}T14:30:00+11:00", duration_minutes=30)
    assert clash["feasible"] is False


def test_google_tools_explain_when_not_connected(tmp_path):
    tools = Tools(tmp_path)
    plan = _day_plan(tools)
    with pytest.raises(ToolFailed, match="not connected"):
        tools("import_calendar", situation_id=plan, day=DAY, calendar="google")


# --- weather -----------------------------------------------------------------------------------


def test_weather_now_for_a_city():
    data = {
        "timezone": "Australia/Sydney",
        "current": {"time": "2026-10-11T19:30", "temperature_2m": 18.2, "apparent_temperature": 17.0, "weather_code": 61},
        "daily": {"time": ["2026-10-11"], "weather_code": [63], "temperature_2m_min": [14.0],
                  "temperature_2m_max": [22.5], "precipitation_probability_max": [70]},
    }
    conditions = parse_conditions(data, "open-meteo", True)
    assert conditions.time == "2026-10-11T19:30:00+11:00" and conditions.conditions == "light rain"
    assert conditions.days[0].max_c == 22.5 and conditions.days[0].conditions == "rain"
    point = parse_geocoding({"results": [{"name": "Dhaka", "country": "Bangladesh", "latitude": 23.7, "longitude": 90.4,
                                          "timezone": "Asia/Dhaka"}]}, "Dhaka")
    assert point.name == "Dhaka, Bangladesh" and point.timezone == "Asia/Dhaka"


def test_get_weather_tool(tmp_path, monkeypatch):
    tools = Tools(tmp_path, lookup=FakeLookup())
    melbourne = tools("get_weather", place="Melbourne")
    assert melbourne["temperature_c"] == 21.4 and melbourne["place"].startswith("Melbourne")
    assert melbourne["day"]["date"] == DAY  # today, from the provider's own clock
    with pytest.raises(ToolFailed, match="No forecast for 2026-11-01"):
        tools("get_weather", place="Melbourne", day="2026-11-01")

    monkeypatch.setenv(HOME_ENV, "1 Example St, Newtown NSW")
    home = tools("get_weather")
    assert home["place"] == "Home"


def test_umbrella_advice_when_leaving_in_the_rain(tmp_path, monkeypatch):
    monkeypatch.setenv(HOME_ENV, "1 Example St, Newtown NSW")
    tools = Tools(tmp_path, lookup=FakeLookup())
    plan = _day_plan(tools)
    home = tools("add_place", situation_id=plan, name="home", look_up=True)["added_id"]
    airport = tools("add_place", situation_id=plan, name="Sydney Airport T1")["added_id"]
    tools("add_commitment", situation_id=plan, owner="me", action="go to airport", start=f"{DAY}T18:00:00+11:00",
          duration_minutes=40, from_place=home, to_place=airport)
    tools("add_commitment", situation_id=plan, owner="me", action="go back", start=f"{DAY}T11:00:00+11:00",
          duration_minutes=40, from_place=home, to_place=airport)
    checked = tools("check_weather", situation_id=plan)
    assert any("take an umbrella" in a and "18:00" in a for a in checked["advice"])
    assert any(a.startswith("Dry when you leave Home at 11:00") for a in checked["advice"])
    assert checked["feasible_after"] is True  # advice only: nothing breaks


# --- live clock and home -------------------------------------------------------------------------


def test_live_plan_follows_the_real_clock(tmp_path):
    tools = Tools(tmp_path)
    plan = tools("create_situation", name="My day", now="live")["situation_id"]
    now = datetime.fromisoformat(tools("get_situation", situation_id=plan)["now"])
    assert abs((now - datetime.now(SYDNEY)).total_seconds()) < 60
    assert tools.store.load(plan).live_clock is True


def test_home_uses_the_address_from_settings(monkeypatch):
    monkeypatch.delenv(HOME_ENV, raising=False)
    plain, notes = place_named("home", look_up=True, lookup=FakeLookup())
    assert plain.id == "loc_home" and plain.latitude is None and HOME_ENV in notes[0]

    monkeypatch.setenv(HOME_ENV, "1 Example St, Newtown NSW")
    found, _ = place_named("My home", look_up=True, lookup=FakeLookup())
    assert (found.id, found.name, found.latitude) == ("loc_home", "Home", -33.89)


# --- the user's current location ----------------------------------------------------------------


def test_current_location_is_a_place_with_exact_coordinates_that_can_move(tmp_path):
    tools = Tools(tmp_path)
    plan = _day_plan(tools)
    here = tools("add_place", situation_id=plan, name="Current location", latitude=-33.87, longitude=151.21)
    assert here["added_id"] == "loc_current_location"
    tools("add_place", situation_id=plan, name="Current location", latitude=-33.90, longitude=151.18)
    place = next(p for p in tools.store.load(plan).situation.locations if p.id == "loc_current_location")
    assert (place.latitude, place.longitude) == (-33.90, 151.18)
    with pytest.raises(ToolFailed, match="both latitude and longitude"):
        tools("add_place", situation_id=plan, name="x", latitude=-33.9)
    with pytest.raises(ToolFailed, match="not a place on Earth"):
        tools("add_place", situation_id=plan, name="x", latitude=-133.9, longitude=10)


def test_weather_at_exact_coordinates(tmp_path):
    tools = Tools(tmp_path)
    here = tools("get_weather", place="your location", latitude=-33.87, longitude=151.21)
    assert here["place"] == "your location" and here["temperature_c"] == 21.4


def test_prompt_tells_alexa_where_the_user_is():
    from app.agent.prompt import system_prompt

    text = system_prompt("Australia/Sydney", location=(-33.87, 151.21))
    assert "latitude -33.87000, longitude 151.21000" in text and "Current location" in text
    assert "latitude" not in system_prompt("Australia/Sydney").split("The current time")[1]
