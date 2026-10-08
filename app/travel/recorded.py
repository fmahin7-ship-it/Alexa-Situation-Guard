"""RecordedProvider — replays real Amazon Location responses saved in data/recorded/.

Free and offline: for tests and for a demo that must not depend on the network.
It only answers a request that was actually recorded (same mode, same time,
same number of alternatives, same places within a few metres). Anything else
is an error — it never passes off a recording of a different question.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .aws_location import parse_calculate_routes
from .option import RouteParseResult
from .search import TravelRequest, TravelSearchError

RECORDED_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "recorded"

# ~10 m in degrees: recordings were made with the same coordinates the situations use.
_SAME_PLACE_DEGREES = 0.0001


def _same_moment(a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return datetime.fromisoformat(a) == datetime.fromisoformat(b)


def _same_place(a: List[float], b: List[float]) -> bool:
    return all(abs(x - y) <= _SAME_PLACE_DEGREES for x, y in zip(a, b))


def _matches(asked: TravelRequest, recorded: TravelRequest) -> bool:
    return (
        asked.mode == recorded.mode
        and asked.max_alternatives == recorded.max_alternatives
        and _same_moment(asked.depart_at, recorded.depart_at)
        and _same_moment(asked.arrive_by, recorded.arrive_by)
        and _same_place(asked.origin, recorded.origin)
        and _same_place(asked.destination, recorded.destination)
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

    def search(self, request: TravelRequest) -> RouteParseResult:
        for file_name, recorded in self._recordings:
            if _matches(request, recorded):
                response = json.loads((self._directory / file_name).read_text(encoding="utf-8"))
                return parse_calculate_routes(response, Path(file_name).stem)
        raise TravelSearchError(
            f"No recording for {request.describe()}. "
            f"Use SITUATION_GUARD_TRAVEL=live, or record this request first."
        )
