"""Amazon Location (geo-routes CalculateRoutes) response -> TravelOption[].

This is the only module that knows the AWS response shape. The live
travel_search will call AWS and hand the response to the same parser.

Shape this relies on (seen in data/recorded/):
  Routes[].Summary            {Distance, Duration}    Duration excludes waits between legs
  Routes[].Legs[].Type        Pedestrian | Transit | Vehicle
  Routes[].Legs[].<Type>LegDetails
      Departure / Arrival     {Time, Place: {Name?, Position}}
      Summary.Overview        {Distance, Duration, BestCaseDuration?}
      Transport (Transit)     {ShortRouteName, Headsign, ...}
      Agency (Transit)        {Name}

A route that breaks these rules is rejected with reasons, never silently dropped.
NextDepartures is ignored: it has departure times but no arrivals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from .option import LegKind, TravelLeg, TravelOption, TravelMode

_LEG_KINDS: Dict[str, LegKind] = {
    "Pedestrian": "walk",
    "Transit": "transit",
    "Vehicle": "drive",
}

CAR_TRAFFIC_ASSUMPTION = "Car times assume expected traffic; best_case_seconds is the no-traffic duration"


class RejectedRoute(BaseModel):
    route_index: int
    reasons: List[str] = Field(default_factory=list)


class RouteParseResult(BaseModel):
    options: List[TravelOption] = Field(default_factory=list)
    rejected: List[RejectedRoute] = Field(default_factory=list)


def _parse_time(value: Any) -> Optional[datetime]:
    """Parse an ISO time that carries an offset; None if missing, unparseable, or naive."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _as_int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) else None


def _parse_leg(raw: Dict[str, Any], index: int) -> Tuple[Optional[TravelLeg], List[str]]:
    leg_type = raw.get("Type")
    kind = _LEG_KINDS.get(leg_type) if isinstance(leg_type, str) else None
    if kind is None:
        return None, [f"leg {index}: unknown leg type {leg_type!r}"]

    details = raw.get(f"{leg_type}LegDetails")
    if not isinstance(details, dict):
        return None, [f"leg {index}: missing {leg_type}LegDetails"]

    departure = details.get("Departure") or {}
    arrival = details.get("Arrival") or {}
    depart_raw, arrive_raw = departure.get("Time"), arrival.get("Time")

    errors: List[str] = []
    depart, arrive = _parse_time(depart_raw), _parse_time(arrive_raw)
    if depart is None:
        errors.append(f"leg {index}: departure time {depart_raw!r} is missing or has no offset")
    if arrive is None:
        errors.append(f"leg {index}: arrival time {arrive_raw!r} is missing or has no offset")
    if depart is not None and arrive is not None and arrive < depart:
        errors.append(f"leg {index}: arrives {arrive_raw} before it departs {depart_raw}")
    if errors:
        return None, errors

    from_place = departure.get("Place") or {}
    to_place = arrival.get("Place") or {}
    overview = (details.get("Summary") or {}).get("Overview") or {}
    transport = details.get("Transport") or {}
    agency = details.get("Agency") or {}

    leg = TravelLeg(
        kind=kind,
        aws_mode=raw.get("TravelMode") or leg_type,
        depart_at=depart_raw,
        arrive_at=arrive_raw,
        from_name=from_place.get("Name"),
        to_name=to_place.get("Name"),
        from_position=from_place.get("Position"),
        to_position=to_place.get("Position"),
        duration_s=_as_int(overview.get("Duration")),
        distance_m=_as_int(overview.get("Distance")),
        line=transport.get("ShortRouteName") or transport.get("RouteName"),
        headsign=transport.get("Headsign"),
        agency=agency.get("Name"),
    )
    return leg, []


def _best_case_seconds(raw_legs: List[Dict[str, Any]]) -> Optional[int]:
    """Sum of BestCaseDuration, only when every leg reports one."""
    total = 0
    for raw in raw_legs:
        details = raw.get(f"{raw.get('Type')}LegDetails") or {}
        value = _as_int(((details.get("Summary") or {}).get("Overview") or {}).get("BestCaseDuration"))
        if value is None:
            return None
        total += value
    return total


def _mode(legs: List[TravelLeg]) -> TravelMode:
    kinds = {leg.kind for leg in legs}
    if "transit" in kinds:
        return "Transit"
    if "drive" in kinds:
        return "Car"
    return "Walk"


def _parse_route(raw: Dict[str, Any], route_index: int, source_id: str) -> Tuple[Optional[TravelOption], List[str]]:
    raw_legs = raw.get("Legs")
    if not isinstance(raw_legs, list) or not raw_legs:
        return None, ["route has no legs"]

    legs: List[TravelLeg] = []
    errors: List[str] = []
    for index, raw_leg in enumerate(raw_legs):
        leg, leg_errors = _parse_leg(raw_leg, index)
        errors.extend(leg_errors)
        if leg is not None:
            legs.append(leg)
    if errors:
        return None, errors

    for index in range(1, len(legs)):
        previous_arrive = datetime.fromisoformat(legs[index - 1].arrive_at)
        if datetime.fromisoformat(legs[index].depart_at) < previous_arrive:
            errors.append(
                f"leg {index}: departs {legs[index].depart_at} before leg {index - 1} "
                f"arrives {legs[index - 1].arrive_at}"
            )
    if errors:
        return None, errors

    depart_at, arrive_at = legs[0].depart_at, legs[-1].arrive_at
    door_to_door = datetime.fromisoformat(arrive_at) - datetime.fromisoformat(depart_at)
    summary = raw.get("Summary") or {}
    mode = _mode(legs)

    option = TravelOption(
        id=f"{source_id}:route{route_index + 1}",
        mode=mode,
        depart_at=depart_at,
        arrive_at=arrive_at,
        door_to_door_seconds=int(door_to_door.total_seconds()),
        moving_seconds=_as_int(summary.get("Duration")),
        best_case_seconds=_best_case_seconds(raw_legs) if mode == "Car" else None,
        distance_m=_as_int(summary.get("Distance")),
        transfers=max(0, sum(1 for leg in legs if leg.kind == "transit") - 1),
        legs=legs,
        assumptions=[CAR_TRAFFIC_ASSUMPTION] if mode == "Car" else [],
    )
    return option, []


def parse_calculate_routes(response: Dict[str, Any], source_id: str) -> RouteParseResult:
    """
    Convert one CalculateRoutes response into TravelOptions.

    source_id names the request (e.g. the recording's file stem) so every
    option can be traced back to the search that produced it.
    Raises ValueError if the response has no Routes list at all.
    """
    routes = response.get("Routes")
    if not isinstance(routes, list):
        raise ValueError(f"'{source_id}' is not a CalculateRoutes response: no Routes list")

    result = RouteParseResult()
    for route_index, raw_route in enumerate(routes):
        option, errors = _parse_route(raw_route, route_index, source_id)
        if option is None:
            result.rejected.append(RejectedRoute(route_index=route_index, reasons=errors))
        else:
            result.options.append(option)
    return result
