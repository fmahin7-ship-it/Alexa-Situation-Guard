"""Situation Guard MCP server — the engine as tools an agent (Alexa+) can call.

The agent proposes; these tools decide. It can only pick labelled options that
code has already turned into edits, and only the feasibility engine says
whether a plan works. Streamable HTTP, mounted by app.main at /mcp.

    reading   list_situations  get_situation
    changes   report_change
    options   find_options     try_option      confirm_option
    planning  create_situation add_place  add_resource  add_commitment  add_goal  add_dependency
    world     check_weather    check_public_holidays          import_calendar

Situations start from predefined files (data/) or are written in conversation;
every change to a plan goes through the engine's checks before it is saved (state/).
"""

from __future__ import annotations

from typing import Optional, Sequence

from mcp.server.mcpserver import MCPServer

from app.mcp.context import ToolContext
from app.events.holidays.calendar import HolidayProvider
from app.events.holidays.nager import make_holiday_provider
from app.events.weather.forecast import WeatherProvider
from app.events.weather.open_meteo import make_weather_provider
from app.mcp.tools import changes, options, planning, reading, world
from app.options.base import OptionSource
from app.options.reschedule import RescheduleSource
from app.options.travel.source import TravelSource
from app.situations.places import PlaceLookup, make_place_lookup
from app.situations.store import SituationStore

INSTRUCTIONS = (
    "Situation Guard keeps a person's plans working when reality changes. "
    "Use report_change when something changes (a delay or a cancellation), then find_options for the "
    "broken commitment, then try_option on the labels you think best. try_option is the engine's verdict: "
    "never tell the user an option works unless try_option said feasible. Only confirm_option changes the plan; "
    "ask the user before confirming."
)


def default_sources() -> list:
    """Asked in order; the first that applies to a commitment answers. Travel needs places with
    coordinates; reschedule handles any time clash (meetings, a shared TV, ...)."""
    return [TravelSource(), RescheduleSource()]


def build_server(
    store: Optional[SituationStore] = None,
    sources: Optional[Sequence[OptionSource]] = None,
    weather: Optional[WeatherProvider] = None,
    holidays: Optional[HolidayProvider] = None,
    place_lookup: Optional[PlaceLookup] = None,
) -> MCPServer:
    """Anything not given is chosen from the environment (recorded/offline by default)."""
    ctx = ToolContext(
        store=store or SituationStore(),
        sources=list(sources) if sources is not None else default_sources(),
        weather=weather or make_weather_provider(),
        holidays=holidays or make_holiday_provider(),
        place_lookup=place_lookup or make_place_lookup(),
    )
    server = MCPServer(name="situation-guard", version="0.1.0", instructions=INSTRUCTIONS)
    for group in (reading, changes, options, planning, world):  # order = the order clients see the tools
        group.register(server, ctx)
    return server


mcp_server = build_server()
