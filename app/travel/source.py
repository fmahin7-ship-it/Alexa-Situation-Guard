"""Travel as an option source: any commitment that goes from one known place to another.

Searches Transit (with alternatives) and Car, merges the journeys, labels them
A, B, C, ... and turns each into a Choice that replaces the commitment.
"""

from __future__ import annotations

from typing import List, Optional

from ..choice import Choice
from ..models import Commitment, Location, Situation
from ..options import OptionsFound
from .option import TravelOption, merge_options
from .search import TravelProvider, TravelRequest, TravelSearchError, make_travel_provider, travel_search
from .to_candidate import travel_option_to_choice

# (mode, extra routes besides the best one). Matches the recordings in data/recorded/.
SEARCHES = (("Transit", 2), ("Car", 0))


def _location(situation: Situation, location_id: Optional[str]) -> Optional[Location]:
    return next((loc for loc in situation.locations if loc.id == location_id), None)


def _has_coordinates(location: Optional[Location]) -> bool:
    return location is not None and location.latitude is not None and location.longitude is not None


class TravelSource:
    name = "travel"

    def __init__(self, provider: Optional[TravelProvider] = None):
        self._provider = provider

    def applies_to(self, situation: Situation, commitment: Commitment) -> bool:
        return _has_coordinates(_location(situation, commitment.origin_id)) and _has_coordinates(
            _location(situation, commitment.destination_id)
        )

    def find(
        self,
        situation: Situation,
        commitment: Commitment,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> OptionsFound:
        if after and before:
            raise ValueError("Give at most one of after or before")
        if not before:
            after = after or situation.now or commitment.start
            if not after:
                raise ValueError("No time to search from: give after or before, or set the situation's now")

        provider = self._provider or make_travel_provider()
        origin = _location(situation, commitment.origin_id)
        destination = _location(situation, commitment.destination_id)
        found: List[List[TravelOption]] = []
        notes: List[str] = []

        for mode, alternatives in SEARCHES:
            request = TravelRequest.between(
                origin, destination, mode=mode, depart_at=after, arrive_by=before, max_alternatives=alternatives
            )
            try:
                result = travel_search(request, provider)
            except TravelSearchError as exc:
                notes.append(str(exc))
                continue
            found.append(result.options)
            notes.extend(result.notes)
            notes.extend(
                f"{request.describe()}: route {r.route_index + 1} unusable ({'; '.join(r.reasons)})"
                for r in result.rejected
            )

        if not found:
            raise TravelSearchError("No travel search succeeded: " + " | ".join(notes))

        choices: List[Choice] = []
        for option in merge_options(*found):
            try:
                choices.append(travel_option_to_choice(situation, option, replaces=commitment.id))
            except ValueError as exc:
                notes.append(str(exc))
        return OptionsFound(source=provider.name, live=provider.live, choices=choices, notes=notes)
