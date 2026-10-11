"""What every MCP tool shares: the store, the option sources, and turning failures into clear tool errors."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver.exceptions import ToolError

from datetime import datetime
from zoneinfo import ZoneInfo

from app.calendars.google import GoogleCalendar
from app.core.candidate import Edit, apply_edits
from app.core.feasibility import FeasibilityResult, evaluate_situation, situation_errors
from app.core.models import Situation
from app.events.holidays.calendar import HolidayProvider
from app.events.weather.forecast import WeatherProvider
from app.options.base import OptionSource
from app.situations.places import PlaceLookup
from app.situations.store import LogEntry, Offer, SessionState, SituationStore, UnreadableSituation


@dataclass
class ToolContext:
    """Everything the tools need, chosen once when the server is built."""

    store: SituationStore
    sources: List[OptionSource] = field(default_factory=list)
    weather: Optional[WeatherProvider] = None
    holidays: Optional[HolidayProvider] = None
    place_lookup: Optional[PlaceLookup] = None
    calendar: Optional[GoogleCalendar] = None

    def load(self, situation_id: str) -> SessionState:
        try:
            state = self.store.load(situation_id)
        except KeyError as exc:
            known = ", ".join(self.store.list_ids())
            raise ToolError(f"Unknown situation '{situation_id}'. Known: {known}") from exc
        except UnreadableSituation as exc:
            raise ToolError(str(exc)) from exc
        if state.live_clock:
            state.situation.now = real_now(state.situation.timezone)
        return state


def real_now(timezone: Optional[str]) -> str:
    """The real current time, to the second, with the plan's offset."""
    moment = datetime.now(ZoneInfo(timezone)) if timezone else datetime.now().astimezone()
    return moment.replace(microsecond=0).isoformat()


def apply_and_save(
    ctx: ToolContext,
    state: SessionState,
    edits: List[Edit],
    log_kind: str,
    detail: str,
    data: Optional[Dict[str, Any]] = None,
) -> FeasibilityResult:
    """Apply edits on a copy; refuse with reasons if malformed; otherwise save and return the engine's verdict."""
    updated, errors = apply_edits(state.situation, edits)
    errors = errors or situation_errors(updated)
    if errors:
        raise ToolError("Not changed: " + "; ".join(errors))
    state.situation = updated
    state.log.append(LogEntry(kind=log_kind, detail=detail, data=data or {}))
    ctx.store.save(state)
    return evaluate(updated)


def evaluate(situation: Situation) -> FeasibilityResult:
    try:
        return evaluate_situation(situation)
    except ValueError as exc:
        raise ToolError(f"Situation cannot be evaluated: {exc}") from exc


def offer_for(state: SessionState, label: str) -> Offer:
    offer = state.offer(label)
    if offer is None:
        known = ", ".join(o.label for o in state.offers) or "none — call find_options first"
        raise ToolError(f"No option '{label}'. Current options: {known}")
    return offer


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "item"


def unique(base: str, taken: set) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}_{n}", n + 1
    return candidate
