"""Outside-world tools: weather, public holidays, and calendar import.

Each one fetches outside data through its provider, turns it into the engine's
own terms (times a place is unavailable, or commitments), and lets the engine
judge the plan. None of them decides whether anything works.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.core.candidate import AddCommitment, AddLocation, Edit, SetUnavailability
from app.core.models import Commitment, Location, Situation
from app.core.timeutil import parse_moment
from app.events.holidays import calendar as holidays
from app.events.holidays.calendar import HolidayError
from app.events.weather import forecast as weather
from app.events.weather.forecast import WeatherError
from app.mcp.context import ToolContext, apply_and_save, evaluate, slug, unique
from app.mcp.views import OutsideCheckView
from app.situations.importers.calendar_ics import CalendarError, events_on, load_calendar
from app.situations.places import place_named

DEFAULT_TIMEZONE = "Australia/Sydney"


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
    added_ids: Optional[List[str]] = None, attribution: Optional[str] = None,
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
        attribution=attribution,
    )


def register(server: MCPServer, ctx: ToolContext) -> None:
    @server.tool()
    def check_weather(situation_id: str, rain_threshold: int = weather.DEFAULT_RAIN_THRESHOLD) -> OutsideCheckView:
        """
        Check the forecast for every outdoor place in the plan (places added with outdoor=true and map
        coordinates). Hours with a rain chance at or above `rain_threshold`% make that place unavailable,
        which breaks anything planned there; find_options can then move it. Refreshing replaces the
        previous weather windows.
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
        for place in (loc for loc in situation.locations if loc.outdoor):
            if place.latitude is None or place.longitude is None:
                notes.append(f"'{place.name}' is outdoor but has no map coordinates, so its weather cannot be checked")
                continue
            days = _days_used(situation, place)
            if not days:
                notes.append(f"Nothing with a date is planned at '{place.name}'")
                continue
            try:
                forecast = ctx.weather.forecast(place.latitude, place.longitude, zone_name)
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

        if not edits:
            raise ToolError("Nothing to check: " + ("; ".join(notes) or "no outdoor places in this plan"))
        after = apply_and_save(ctx, state, edits, "weather_checked", "; ".join(changes))
        return _view(situation_id, ctx.weather.name, ctx.weather.live, before, after, changes, notes, attribution=attribution)

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
        Add the timed events of one day (YYYY-MM-DD) from a calendar file as commitments owned by `owner`,
        with their places. Events already imported are skipped; repeating events count once.
        """
        state = ctx.load(situation_id)
        situation = state.situation
        before = evaluate(situation)
        try:
            wanted = date.fromisoformat(day)
            events = events_on(load_calendar(calendar), wanted, _zone(situation))
        except ValueError as exc:
            raise ToolError(f"day must be YYYY-MM-DD: {exc}") from exc
        except CalendarError as exc:
            raise ToolError(str(exc)) from exc

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
                location_id=location_id, meta={"calendar": calendar, "calendar_uid": event.uid, "title": event.title},
            )))
            added.append(commitment_id)
            changes.append(f"{event.title} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}")
            if event.repeats:
                notes.append(f"'{event.title}' repeats; only this occurrence was added")

        if not edits:
            notes.append(f"No new timed events on {day} in '{calendar}'")
            return _view(situation_id, f"calendar:{calendar}", False, before, before, changes, notes)
        after = apply_and_save(ctx, state, edits, "calendar_imported", "; ".join(changes), {"calendar": calendar})
        return _view(situation_id, f"calendar:{calendar}", False, before, after, changes, notes, added_ids=added)
