"""TravelOption -> Choice: the travel tool's translator into situation edits.

Choosing option X to replace commitment R means:
  1. add a journey commitment with X's real door-to-door window,
     same owner and same origin/destination as R
  2. mark R cancelled
  3. move every "requires R" dependency onto the new journey

Uses only the existing edit types, so simulate() and the feasibility engine
are unchanged. Refuses (ValueError) rather than guess when the option and the
commitment do not describe the same trip.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from ..candidate import AddCommitment, AddDependency, Candidate, Edit, RemoveDependency, UpdateCommitment
from ..choice import Choice
from ..models import Commitment, Dependency, Location, Situation
from .option import TravelOption, describe_option

# How far a route may start/end from the situation's location and still count
# as the same place. Routing snaps to the nearest road or path, so not exact.
ENDPOINT_TOLERANCE_M = 500


def _distance_m(lon_lat: List[float], location: Location) -> float:
    """Great-circle distance between a [lon, lat] point and a location."""
    lon1, lat1 = math.radians(lon_lat[0]), math.radians(lon_lat[1])
    lon2, lat2 = math.radians(location.longitude), math.radians(location.latitude)
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def _endpoint_check(
    situation: Situation, location_id: Optional[str], position: Optional[List[float]], end: str
) -> Tuple[bool, Optional[str]]:
    """(checked, error). Not checked when the location or position has no coordinates."""
    location = next((loc for loc in situation.locations if loc.id == location_id), None)
    if location is None or location.latitude is None or location.longitude is None or not position:
        return False, None
    distance = _distance_m(position, location)
    if distance > ENDPOINT_TOLERANCE_M:
        return True, (
            f"option {end}s {distance:.0f} m from '{location.id}' ({location.name}), "
            f"more than {ENDPOINT_TOLERANCE_M} m away"
        )
    return True, None


def travel_option_to_choice(situation: Situation, option: TravelOption, replaces: str) -> Choice:
    """
    Build the Choice "take this journey instead of commitment `replaces`".
    The option must be labelled (see merge_options).
    """
    if not option.label:
        raise ValueError(f"Option '{option.id}' has no label; label options with merge_options first")

    old = next((c for c in situation.commitments if c.id == replaces), None)
    if old is None:
        raise ValueError(f"Unknown commitment '{replaces}'")

    first, last = option.legs[0], option.legs[-1]
    checks = [
        _endpoint_check(situation, old.origin_id, first.from_position, "start"),
        _endpoint_check(situation, old.destination_id, last.to_position, "end"),
    ]
    problems = [error for _, error in checks if error]
    if problems:
        raise ValueError(f"Option {option.label} does not fit '{replaces}': " + "; ".join(problems))

    journey_id = f"c_travel_{option.label}"
    journey = Commitment(
        id=journey_id,
        owner_id=old.owner_id,
        action=f"travel_{option.mode.lower()}",
        start=option.depart_at,
        end=option.arrive_at,
        origin_id=old.origin_id,
        destination_id=old.destination_id,
        meta={"option_id": option.id, "source": option.source, "summary": describe_option(option)},
    )

    edits: List[Edit] = [
        AddCommitment(commitment=journey),
        UpdateCommitment(commitment_id=replaces, status="cancelled"),
    ]
    for dep in situation.dependencies:
        if dep.to_id != replaces:
            continue
        edits.append(RemoveDependency(dependency_id=dep.id))
        edits.append(
            AddDependency(
                dependency=Dependency(
                    id=f"{dep.id}__via_{journey_id}", from_id=dep.from_id, to_id=journey_id, kind=dep.kind
                )
            )
        )

    assumptions = [f"Journey times come from {option.source} ({option.id})"] + list(option.assumptions)
    if not all(checked for checked, _ in checks):
        assumptions.append("Route endpoints could not be checked against the situation's locations")

    return Choice(
        label=option.label,
        summary=describe_option(option),
        candidate=Candidate(
            id=f"cand_travel_{option.label}",
            rationale=f"Replace '{replaces}' with option {option.label}: {describe_option(option)}",
            edits=edits,
            assumptions=assumptions,
        ),
    )
