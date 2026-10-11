"""Known places: a few names with verified map coordinates (the points the recorded travel searches used)."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from app.core.models import Location

_KNOWN: List[Dict] = [
    {
        "location": Location(
            id="loc_central",
            name="Central Station",
            address="Eddy Ave, Haymarket NSW 2000",
            latitude=-33.8830,
            longitude=151.2063,
        ),
        "aliases": ["central", "central station", "sydney central"],
    },
    {
        "location": Location(
            id="loc_airport_t1",
            name="Sydney Airport International Terminal (T1)",
            address="Mascot NSW 2020",
            latitude=-33.9361,
            longitude=151.1664,
        ),
        "aliases": ["sydney airport", "airport", "international airport", "t1", "sydney international"],
    },
]


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def known_place_names() -> List[str]:
    return [entry["location"].name for entry in _KNOWN]


def find_place(query: str) -> Optional[Location]:
    """The known place whose name or alias best matches the query, or None."""
    wanted = _normalise(query)
    if not wanted:
        return None
    best: Optional[Location] = None
    best_len = 0
    for entry in _KNOWN:
        names = [_normalise(entry["location"].name)] + [_normalise(a) for a in entry["aliases"]]
        for name in names:
            # The known name inside what was said ("the airport T1 please"), never the other way
            # round: "Sydney" alone is a city, not Sydney Airport.
            matched = name == wanted or re.search(rf"\b{re.escape(name)}\b", wanted)
            if matched and len(name) > best_len:
                best, best_len = entry["location"], len(name)
    return best.model_copy(deep=True) if best else None

