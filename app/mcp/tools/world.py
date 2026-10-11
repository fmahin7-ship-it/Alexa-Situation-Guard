"""Outside-world tools: weather, public holidays, and the user's calendars.

Each one fetches outside data through its provider, turns it into the engine's
own terms (times a place is unavailable, or commitments), and lets the engine
judge the plan. None of them decides whether anything works. add_to_calendar
writes to the user's real calendar, and only for a commitment the engine says fits.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.calendars.google import GoogleCalendarError
from app.calendars.sync import EVENT_ID
from app.core.candidate import AddCommitment, AddLocation, Edit, SetUnavailability
from app.core.models import Commitment, Location, Situation
from app.core.timeutil import parse_moment
from app.events.holidays import calendar as holidays
from app.events.holidays.calendar import HolidayError
from app.events.weather import forecast as weather
from app.events.weather.forecast import GeoPoint, WeatherError
from app.mcp.context import ToolContext, apply_and_save, evaluate, slug, unique
from app.mcp.views import CalendarAddView, OutsideCheckView, WeatherNowView
from app.situations.importers.calendar_ics import CalendarError, events_on, load_calendar
from app.situations.places import place_named
from app.situations.store import LogEntry

DEFAULT_TIMEZONE = "Australia/Sydney"
NOT_CONNECTED = "Google Calendar is not connected (SITUATION_GUARD_CALENDAR=google, then sign in)"
UMBRELLA_CHANCE = 40  # percent chance of rain when leaving at which to suggest an umbrella


def _zone(situation: Situation) -> Optional[ZoneInfo]:
    return ZoneInfo(situation.timezone) if situation.timezone else None


def _days_used(situation: Situation, place: Location) -> List[date]:
    """Local dates on which something is planned at this place."""
    zone = _zone(situation)
    days = set()
    for c in situation.commitments:
        if c.location_id != place.id or c.status == "cancelled":
            continue
        start = parse_moment(c.start)
        if start is None or start.year == 2000:  # bare "HH:MM" times carry no date
            continue
        days.add((start.astimezone(zone) if zone and start.tzinfo else start).date())
    return sorted(days)


def _view(
    situation_id: str, source: str, live: bool, before, after, changes: List[str], notes: List[str],
    added_ids: Optional[List[str]] = None, attribution: Optional[str] = None, advice: Optional[List[str]] = None,
) -> OutsideCheckView:
    return OutsideCheckView(
        situation_id=situation_id,
        source=source,
        live=live,
        changes=changes,
        added_ids=added_ids or [],
        feasible_before=before.feasible,
        feasible_after=after.feasible,
        goals_at_risk=after.affected_goals,
        reasons=after.reasons,
        notes=notes,
        advice=advice or [],
        attribution=attribution,
    )


def _umbrella_advice(situation: Situation, forecast_for, zone: ZoneInfo, notes: List[str]) -> List[str]:
    """For each journey with a start time: will it rain where and when they leave?"""
    places = {loc.id: loc for loc in situation.locations}
    advice = []
    for c in situation.commitments:
        origin = places.get(c.origin_id or "")
        start = parse_moment(c.start)
        if c.status == "cancelled" or origin is None or start is None or start.tzinfo is None:
            continue
        if origin.latitude is None or origin.longitude is None:
            continue
        try:
            hour = weather.rain_at(forecast_for(origin), start)
        except WeatherError as exc:
            notes.append(str(exc))
            continue
        if hour is None or hour.rain_probability is None:
            notes.append(f"No forecast yet for {start.astimezone(zone):%a %H:%M} at {origin.name}")
            continue
        when = f"{start.astimezone(zone):%H:%M}"
        who = "you leave" if c.owner_id == "me" else f"{c.owner_id} leaves"
        if hour.rain_probability >= UMBRELLA_CHANCE:
            advice.append(f"{hour.rain_probability}% chance of rain at {origin.name} around {when} when {who}: take an umbrella")
        else:
            advice.append(f"Dry when {who} {origin.name} at {when} ({hour.rain_probability}% chance of rain)")
    return advice


def register(server: MCPServer, ctx: ToolContext) -> None:
    @server.tool()
    def check_weather(situation_id: str, rain_threshold: int = weather.DEFAULT_RAIN_THRESHOLD) -> OutsideCheckView:
        """
        Check the forecast for the plan: every outdoor place (added with outdoor=true and map coordinates)
        and every journey's starting point. Hours with a rain chance at or above `rain_threshold`% make an
        outdoor place unavailable, which breaks anything planned there; find_options can then move it.
        For journeys it gives advice (take an umbrella). Refreshing replaces the previous weather windows.
        """
        if ctx.weather is None:
            raise ToolError("No weather provider is configured")
        state = ctx.load(situation_id)
        situation = state.situation
        before = evaluate(situation)
        zone_name = situation.timezone or DEFAULT_TIMEZONE
        zone = ZoneInfo(zone_name)

        edits: List[Edit] = []
        changes: List[str] = []
        notes: List[str] = []
        attribution = None
        forecasts: Dict[str, weather.Forecast] = {}

        def forecast_for(place: Location) -> weather.Forecast:
            if place.id not in forecasts:
                forecasts[place.id] = ctx.weather.forecast(place.latitude, place.longitude, zone_name)
            return forecasts[place.id]

        for place in (loc for loc in situation.locations if loc.outdoor):
            if place.latitude is None or place.longitude is None:
                notes.append(f"'{place.name}' is outdoor but has no map coordinates, so its weather cannot be checked")
                continue
            days = _days_used(situation, place)
            if not days:
                notes.append(f"Nothing with a date is planned at '{place.name}'")
                continue
            try:
                forecast = forecast_for(place)
            except WeatherError as exc:
                notes.append(str(exc))
                continue
            attribution = forecast.attribution
            windows = []
            for day in days:
                start = datetime.combine(day, time(0), tzinfo=zone)
                windows += weather.wet_windows(forecast, start, start + timedelta(days=1), rain_threshold)
            edits.append(SetUnavailability(location_id=place.id, source=weather.SOURCE, windows=windows))
            changes += [f"{place.name}: {w.reason}, {w.start} to {w.end}" for w in windows] or [f"{place.name}: dry"]

        advice = _umbrella_advice(situation, forecast_for, zone, notes)
        if not edits and not advice:
            raise ToolError("Nothing to check: " + ("; ".join(notes) or "no outdoor places or timed journeys in this plan"))
        after = apply_and_save(ctx, state, edits, "weather_checked", "; ".join(changes + advice)) if edits else before
        attribution = attribution or next((f.attribution for f in forecasts.values()), None)
        return _view(
            situation_id, ctx.weather.name, ctx.weather.live, before, after, changes, notes,
            attribution=attribution, advice=advice,
        )

    @server.tool()
    def check_public_holidays(situation_id: str, country: str = "AU", region: str = "AU-NSW") -> OutsideCheckView:
        """
        Check public holidays for places that close on them (added with closes_on_public_holidays=true).
        On a holiday such a place is unavailable all day, which breaks anything planned there.
        `country` is an ISO code (AU); `region` a subdivision code (AU-NSW, AU-VIC, ...).
        """
        if ctx.holidays is None:
            raise ToolError("No holiday provider is configured")
        state = ctx.load(situation_id)
        situation = state.situation
        before = evaluate(situation)

        edits: List[Edit] = []
        changes: List[str] = []
        notes: List[str] = []
        by_year: Dict[int, list] = {}
        for place in (loc for loc in situation.locations if loc.closes_on_public_holidays):
            windows = []
            for day in _days_used(situation, place):
                if day.year not in by_year:
                    try:
                        by_year[day.year] = ctx.holidays.holidays(day.year, country)
                    except HolidayError as exc:
                        raise ToolError(str(exc)) from exc
                holiday = holidays.holiday_on(by_year[day.year], day, region)
                if holiday is not None:
                    windows.append(holidays.closed_all_day(holiday, situation.timezone))
                    changes.append(f"{place.name} closed on {day.isoformat()}: {holiday.name}")
            edits.append(SetUnavailability(location_id=place.id, source=holidays.SOURCE, windows=windows))

        if not edits:
            raise ToolError("No places in this plan close on public holidays (add_place closes_on_public_holidays=true)")
        if not changes:
            notes.append(f"No public holidays in {region} on the days planned")
        after = apply_and_save(ctx, state, edits, "holidays_checked", "; ".join(changes) or "no holidays")
        return _view(situation_id, ctx.holidays.name, ctx.holidays.live, before, after, changes, notes)

    @server.tool()
    def import_calendar(situation_id: str, day: str, calendar: str = "work", owner: str = "me") -> OutsideCheckView:
        """
        Add the timed events of one day (YYYY-MM-DD) as commitments owned by `owner`, with their places.
        `calendar` is "google" for the user's real Google Calendar (when connected) or the name of a
        calendar file ("work"). Events already in the plan are skipped; repeating events count once.
        """
        state = ctx.load(situation_id)
        situation = state.situation
        before = evaluate(situation)
        google = calendar.strip().lower() == "google"
        if google and ctx.calendar is None:
            raise ToolError(NOT_CONNECTED)
        try:
            wanted = date.fromisoformat(day)
            if google:
                events = ctx.calendar.events_on(wanted, _zone(situation) or ZoneInfo(DEFAULT_TIMEZONE))
            else:
                events = events_on(load_calendar(calendar), wanted, _zone(situation))
        except ValueError as exc:
            raise ToolError(f"day must be YYYY-MM-DD: {exc}") from exc
        except (CalendarError, GoogleCalendarError) as exc:
            raise ToolError(str(exc)) from exc
        source, live = (ctx.calendar.name, True) if google else (f"calendar:{calendar}", False)

        imported = {c.meta.get("calendar_uid") for c in situation.commitments}
        taken_ids = {c.id for c in situation.commitments}
        known_places = {loc.id for loc in situation.locations}
        zone = _zone(situation)
        edits: List[Edit] = []
        changes: List[str] = []
        notes: List[str] = []
        added: List[str] = []
        for event in events:
            if event.uid in imported:
                notes.append(f"'{event.title}' was already imported")
                continue
            location_id = None
            if event.location:
                place, _ = place_named(event.location)
                if place.id not in known_places:
                    edits.append(AddLocation(location=place))
                    known_places.add(place.id)
                location_id = place.id
            start, end = (event.start.astimezone(zone), event.end.astimezone(zone)) if zone else (event.start, event.end)
            as_text = (lambda dt: dt.isoformat()) if zone else (lambda dt: dt.strftime("%Y-%m-%dT%H:%M"))
            commitment_id = unique(f"c_{slug(event.title)}", taken_ids)
            taken_ids.add(commitment_id)
            edits.append(AddCommitment(commitment=Commitment(
                id=commitment_id, owner_id=owner, action=slug(event.title), start=as_text(start), end=as_text(end),
                location_id=location_id,
                meta={"calendar": calendar, "calendar_uid": event.uid, "title": event.title, **({EVENT_ID: event.uid} if google else {})},
            )))
            added.append(commitment_id)
            changes.append(f"{event.title} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}")
            if event.repeats:
                notes.append(f"'{event.title}' repeats; only this occurrence was added")

        if not edits:
            notes.append(f"No new timed events on {day} in '{calendar}'")
            return _view(situation_id, source, live, before, before, changes, notes)
        after = apply_and_save(ctx, state, edits, "calendar_imported", "; ".join(changes), {"calendar": calendar})
        return _view(situation_id, source, live, before, after, changes, notes, added_ids=added)

    @server.tool()
    def add_to_calendar(situation_id: str, commitment_id: str) -> CalendarAddView:
        """
        Put a planned commitment (a meeting, a call, a trip) into the user's real Google Calendar.
        Use it when the user asked to schedule or book something. Refused when the engine finds a
        problem with that commitment (a clash, a closed place, ...): sort the plan out first.
        """
        if ctx.calendar is None:
            raise ToolError(NOT_CONNECTED)
        state = ctx.load(situation_id)
        situation = state.situation
        commitment = next((c for c in situation.commitments if c.id == commitment_id), None)
        if commitment is None:
            raise ToolError(f"No commitment '{commitment_id}' in this plan")
        if commitment.meta.get(EVENT_ID):
            raise ToolError(f"'{commitment_id}' is already in Google Calendar")
        start, end = parse_moment(commitment.start), parse_moment(commitment.end)
        if start is None or end is None or start.tzinfo is None or end <= start:
            raise ToolError("Only a commitment with a dated start and end (with time zone) can go in the calendar")
        verdict = evaluate(situation)
        if commitment_id in verdict.broken_commitment_ids:
            raise ToolError("Not added, the engine found a problem: " + "; ".join(verdict.reasons))

        title = commitment.meta.get("title") or commitment.action.replace("_", " ").capitalize()
        place = next((loc.name for loc in situation.locations if loc.id == commitment.location_id), None)
        try:
            event_id = ctx.calendar.create(
                title, commitment.start, commitment.end, situation.timezone or DEFAULT_TIMEZONE, place
            )
        except GoogleCalendarError as exc:
            raise ToolError(str(exc)) from exc
        commitment.meta.update({EVENT_ID: event_id, "calendar_uid": event_id, "calendar": "google", "title": title})
        state.log.append(LogEntry(
            kind="calendar_added", detail=f"{title} {start:%H:%M}-{end:%H:%M}", data={"event_id": event_id}
        ))
        ctx.store.save(state)
        return CalendarAddView(
            situation_id=situation_id, commitment_id=commitment_id, event_id=event_id, title=title,
            start=commitment.start, end=commitment.end, calendar=ctx.calendar.name,
        )

    @server.tool()
    def get_weather(
        place: str = "home",
        day: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> WeatherNowView:
        """
        The real weather: the temperature and conditions now, and the forecast for `day` (YYYY-MM-DD,
        up to 6 days ahead; today if not given). Give `latitude`/`longitude` for an exact spot such as
        the user's current location (then `place` is just its label), or a town, city, address or "home".
        For "what's the weather / temperature". It does not change any plan.
        """
        if ctx.weather is None:
            raise ToolError("No weather provider is configured")
        if (latitude is None) != (longitude is None):
            raise ToolError("Give both latitude and longitude, or neither")
        try:
            if latitude is not None:
                point = GeoPoint(name=place, latitude=latitude, longitude=longitude)
            else:
                point = _weather_point(place)
            now = ctx.weather.conditions(point.latitude, point.longitude, point.timezone or DEFAULT_TIMEZONE)
        except WeatherError as exc:
            raise ToolError(str(exc)) from exc
        wanted = day or now.time[:10]
        chosen = next((d for d in now.days if d.date == wanted), None)
        if day and chosen is None:
            span = f"{now.days[0].date} to {now.days[-1].date}" if now.days else "no days"
            raise ToolError(f"No forecast for {day}; the forecast covers {span}")
        return WeatherNowView(
            place=point.name, time=now.time, temperature_c=now.temperature_c, feels_like_c=now.feels_like_c,
            conditions=now.conditions, day=chosen, live=now.live, source=now.provider, attribution=now.attribution,
        )

    def _weather_point(place: str) -> GeoPoint:
        """Known places and home first, then the weather service's town search, then a full place lookup."""
        found, notes = place_named(place)
        if found.latitude is None and found.id == "loc_home":
            found, notes = place_named(place, look_up=True, lookup=ctx.place_lookup)
            if found.latitude is None:
                raise WeatherError("; ".join(notes) or "Home has no map coordinates")
        if found.latitude is not None and found.longitude is not None:
            return GeoPoint(name=found.name, latitude=found.latitude, longitude=found.longitude)
        try:
            return ctx.weather.find(place)
        except WeatherError:
            if ctx.place_lookup is None:
                raise
        found, notes = place_named(place, look_up=True, lookup=ctx.place_lookup)
        if found.latitude is None or found.longitude is None:
            raise WeatherError("; ".join(notes) or f"Could not find '{place}'")
        return GeoPoint(name=found.name, latitude=found.latitude, longitude=found.longitude)
