"""TravelOption — one complete journey a travel tool found.

A TravelOption is not a Candidate. It says "this journey exists"; a Candidate
says "change the situation like this". The agent picks an option by label and
deterministic code turns it into a Candidate.

Times are kept exactly as the tool returned them (ISO with offset). Anything
the tool did not give stays None — nothing here is invented.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

LegKind = Literal["walk", "transit", "drive"]
TravelMode = Literal["Transit", "Car", "Walk"]


class TravelLeg(BaseModel):
    kind: LegKind
    aws_mode: str = Field(..., description="TravelMode exactly as the tool returned it")
    depart_at: str
    arrive_at: str
    from_name: Optional[str] = None
    to_name: Optional[str] = None
    from_position: Optional[List[float]] = Field(None, description="[longitude, latitude]")
    to_position: Optional[List[float]] = Field(None, description="[longitude, latitude]")
    duration_s: Optional[int] = None
    distance_m: Optional[int] = None
    line: Optional[str] = None
    headsign: Optional[str] = None
    agency: Optional[str] = None


class TravelOption(BaseModel):
    id: str = Field(..., description="Where it came from, e.g. 'route_transit_alternatives:route2'")
    label: Optional[str] = Field(None, description="Short name the agent uses: A, B, C, ...")
    source: str = "amazon-location"
    mode: TravelMode
    depart_at: str = Field(..., description="First leg's departure — when the journey really starts")
    arrive_at: str = Field(..., description="Last leg's arrival — when the journey really ends")
    door_to_door_seconds: int = Field(..., description="arrive_at - depart_at, waits included")
    moving_seconds: Optional[int] = Field(None, description="Tool's own duration; excludes waits between legs")
    best_case_seconds: Optional[int] = Field(None, description="Car only: duration with no traffic")
    distance_m: Optional[int] = None
    transfers: int = 0
    legs: List[TravelLeg] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)


def _clock(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%H:%M")


def _leg_key(leg: TravelLeg) -> Tuple[str, str, str, Optional[str]]:
    return (leg.kind, leg.depart_at, leg.arrive_at, leg.line)


def same_journey(a: TravelOption, b: TravelOption) -> bool:
    """Same legs at the same times on the same lines — the same journey, whichever search found it."""
    return [_leg_key(leg) for leg in a.legs] == [_leg_key(leg) for leg in b.legs]


def merge_options(*option_lists: List[TravelOption]) -> List[TravelOption]:
    """
    Combine options from one or more searches, drop repeated journeys (first one wins),
    sort by arrival, and label them A, B, C, ...
    """
    merged: List[TravelOption] = []
    for options in option_lists:
        for option in options:
            if not any(same_journey(option, kept) for kept in merged):
                merged.append(option)

    merged.sort(key=lambda o: (datetime.fromisoformat(o.arrive_at), datetime.fromisoformat(o.depart_at)))
    if len(merged) > 26:
        raise ValueError("More than 26 options cannot be labelled A-Z")
    return [
        option.model_copy(update={"label": chr(ord("A") + index)})
        for index, option in enumerate(merged)
    ]


def describe_option(option: TravelOption) -> str:
    """One line the agent reads instead of the raw tool response."""
    parts = []
    if option.label:
        parts.append(option.label)
    parts.append(option.mode)
    parts.append(f"leave {_clock(option.depart_at)} -> arrive {_clock(option.arrive_at)}")
    parts.append(f"{round(option.door_to_door_seconds / 60)} min")

    transit = [leg for leg in option.legs if leg.kind == "transit"]
    if transit:
        route = transit[0].line or "transit"
        for previous, leg in zip(transit, transit[1:]):
            route += f", change at {previous.to_name or 'a stop'} to {leg.line or 'transit'}"
        parts.append(route)
    elif option.distance_m is not None:
        parts.append(f"{option.distance_m / 1000:.1f} km")
    return " | ".join(parts)
