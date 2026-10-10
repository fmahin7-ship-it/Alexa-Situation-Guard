"""RecordedProvider — replays real Amazon Location responses saved in data/recorded/.

Free and offline: for tests and for a demo that must not depend on the network.

It answers a request that was recorded (same mode, same number of alternatives,
same places within a few metres) at the same moment. For a "depart at" request
with no exact recording, it may use the closest *earlier* departure recording
within FALLBACK_WINDOW — journeys found from an earlier time are still real
journeys, and the engine rejects any that leave before "now". The result always
says so in its notes. Anything else is an error: it never passes off a recording
of a different question.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from .aws_location import parse_calculate_routes
from .option import RouteParseResult
from .search import TravelRequest, TravelSearchError

RECORDED_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "recorded"

# ~10 m in degrees: recordings were made with the same coordinates the situations use.
_SAME_PLACE_DEGREES = 0.0001

# How much earlier a recorded departure search may be than the one asked for.
FALLBACK_WINDOW = timedelta(minutes=15)


def _same_moment(a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return datetime.fromisoformat(a) == datetime.fromisoformat(b)


def _same_place(a: List[float], b: List[float]) -> bool:
    return all(abs(x - y) <= _SAME_PLACE_DEGREES for x, y in zip(a, b))


def _same_question_apart_from_time(asked: TravelRequest, recorded: TravelRequest) -> bool:
    return (
        asked.mode == recorded.mode
        and asked.max_alternatives == recorded.max_alternatives
        and _same_place(asked.origin, recorded.origin)
        and _same_place(asked.destination, recorded.destination)
    )


def _matches(asked: TravelRequest, recorded: TravelRequest) -> bool:
    return (
        _same_question_apart_from_time(asked, recorded)
        and _same_moment(asked.depart_at, recorded.depart_at)
        and _same_moment(asked.arrive_by, recorded.arrive_by)
    )


class RecordedProvider:
    name = "amazon-location (recorded)"
    live = False

    def __init__(self, directory: Path = RECORDED_DIR):
        self._directory = directory
        index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        self._recordings = [
            (entry["file"], TravelRequest(**entry["request"])) for entry in index["recordings"]
        ]

    def _closest_earlier_departure(self, request: TravelRequest) -> Optional[Tuple[str, TravelRequest]]:
        if not request.depart_at:
            return None
        asked = datetime.fromisoformat(request.depart_at)
        candidates = [
            (asked - datetime.fromisoformat(recorded.depart_at), file_name, recorded)
            for file_name, recorded in self._recordings
            if recorded.depart_at and _same_question_apart_from_time(request, recorded)
        ]
        candidates = [c for c in candidates if timedelta(0) < c[0] <= FALLBACK_WINDOW]
        if not candidates:
            return None
        _, file_name, recorded = min(candidates, key=lambda c: c[0])
        return file_name, recorded

    def _replay(self, file_name: str) -> RouteParseResult:
        response = json.loads((self._directory / file_name).read_text(encoding="utf-8"))
        return parse_calculate_routes(response, Path(file_name).stem)

    def search(self, request: TravelRequest) -> RouteParseResult:
        for file_name, recorded in self._recordings:
            if _matches(request, recorded):
                return self._replay(file_name)

        earlier = self._closest_earlier_departure(request)
        if earlier is not None:
            file_name, recorded = earlier
            result = self._replay(file_name)
            result.notes.append(
                f"No recording for {request.mode} departing {request.depart_at}; replayed the closest earlier "
                f"recorded search ({recorded.depart_at}). Journeys that leave before now will be rejected."
            )
            return result

        raise TravelSearchError(
            f"No recording for {request.describe()}. "
            f"Use SITUATION_GUARD_TRAVEL=live, or record this request first."
        )
