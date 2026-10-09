"""Amazon Location (geo-routes CalculateRoutes) <-> TravelRequest / TravelOption[].

This is the only module that knows the AWS request and response shapes.
AmazonLocationProvider calls AWS live; recordings go through the same parser.

Credentials come from the AWS profile named by SITUATION_GUARD_AWS_PROFILE
(default "situation-guard"), never from the repository.

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

import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .option import LegKind, RejectedRoute, RouteParseResult, TravelLeg, TravelOption, TravelMode
from .search import TravelSearchError

if TYPE_CHECKING:
    from .search import TravelRequest

_LEG_KINDS: Dict[str, LegKind] = {
    "Pedestrian": "walk",
    "Transit": "transit",
    "Vehicle": "drive",
}

SOURCE = "amazon-location"

CAR_TRAFFIC_ASSUMPTION ="Car times assume expected traffic; best_case_seconds is the no-traffic duration"


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
        source_mode=raw.get("TravelMode") or leg_type,
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
        source=SOURCE,
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


# --- live calls --------------------------------------------------------------

PROFILE_ENV = "SITUATION_GUARD_AWS_PROFILE"
REGION_ENV = "SITUATION_GUARD_AWS_REGION"
DEFAULT_PROFILE = "situation-guard"
DEFAULT_REGION = "ap-southeast-2"

_AWS_TRAVEL_MODES = {"Transit": "Transit", "Car": "Car", "Walk": "Pedestrian"}


def calculate_routes_params(request: "TravelRequest") -> Dict[str, Any]:
    """The exact CalculateRoutes parameters for a request."""
    params: Dict[str, Any] = {
        "Origin": list(request.origin),
        "Destination": list(request.destination),
        "TravelMode": _AWS_TRAVEL_MODES[request.mode],
        "LegAdditionalFeatures": ["Summary"],
    }
    if request.depart_at:
        params["DepartureTime"] = request.depart_at
    else:
        params["ArrivalTime"] = request.arrive_by
    if request.max_alternatives:
        params["MaxAlternatives"] = request.max_alternatives
    return params


def _source_id(request: "TravelRequest") -> str:
    when = f"depart-{request.depart_at}" if request.depart_at else f"arrive-{request.arrive_by}"
    return f"amazon-location:{request.mode.lower()}:{when}"


class AmazonLocationProvider:
    """Live Amazon Location routes. Every search is one billed CalculateRoutes request."""

    name = SOURCE
    live = True

    def __init__(self, client: Any = None, profile: Optional[str] = None, region: Optional[str] = None):
        self._client = client
        self._profile = profile or os.environ.get(PROFILE_ENV, DEFAULT_PROFILE)
        self._region = region or os.environ.get(REGION_ENV, DEFAULT_REGION)

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.exceptions import ProfileNotFound

            try:
                session = boto3.Session(profile_name=self._profile, region_name=self._region)
            except ProfileNotFound as exc:
                raise TravelSearchError(
                    f"AWS profile '{self._profile}' not found. Run: aws configure --profile {self._profile} "
                    f"(or set {PROFILE_ENV})"
                ) from exc
            self._client = session.client("geo-routes")
        return self._client

    def search(self, request: "TravelRequest") -> RouteParseResult:
        from botocore.exceptions import BotoCoreError, ClientError

        client = self._get_client()
        try:
            response = client.calculate_routes(**calculate_routes_params(request))
        except (BotoCoreError, ClientError) as exc:
            raise TravelSearchError(f"Amazon Location could not answer ({request.describe()}): {exc}") from exc
        response.pop("ResponseMetadata", None)
        return parse_calculate_routes(response, _source_id(request))
