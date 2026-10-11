"""Places — turn a spoken name into a Location.

Any name is a place ("the drawing room", "the office"). Coordinates, which only
travel and weather need, come from:
  1. known places (verified, free), then
  2. Amazon Location Places, when asked to look the name up and
     SITUATION_GUARD_PLACES=live (billed per lookup).
Otherwise the place is kept without coordinates.

    SITUATION_GUARD_PLACES=known   (default) known places only, free and offline
    SITUATION_GUARD_PLACES=live    also look names up with Amazon Location
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Protocol, Tuple

from app.core.models import Location
from app.situations.places.known import find_place, known_place_names

__all__ = ["PlaceLookup", "find_place", "known_place_names", "make_place_lookup", "place_named"]

PLACES_ENV = "SITUATION_GUARD_PLACES"


class PlaceLookup(Protocol):
    name: str
    live: bool

    def look_up(self, name: str, near: Optional[List[float]] = None) -> Location:
        """A place with coordinates, or raise an error explaining why not."""
        ...


def make_place_lookup() -> Optional[PlaceLookup]:
    choice = os.environ.get(PLACES_ENV, "known").strip().lower()
    if choice == "known":
        return None
    if choice == "live":
        from app.situations.places.amazon_location import AmazonPlaceLookup

        return AmazonPlaceLookup()
    raise ValueError(f"{PLACES_ENV} must be 'known' or 'live', not '{choice}'")


def plain_place(name: str) -> Location:
    clean = " ".join(name.split()) or "place"
    slug = re.sub(r"[^a-z0-9]+", "_", clean.lower()).strip("_")[:30] or "place"
    return Location(id=f"loc_{slug}", name=clean)


def place_named(
    name: str,
    look_up: bool = False,
    lookup: Optional[PlaceLookup] = None,
    near: Optional[List[float]] = None,
) -> Tuple[Location, List[str]]:
    """
    (place, notes). Known places come with coordinates; with look_up=True and a lookup
    available, other names are searched; anything else is a plain place without coordinates.
    """
    known = find_place(name)
    if known is not None:
        return known, []
    if not look_up:
        return plain_place(name), []
    if lookup is None:
        return plain_place(name), [
            f"Looking up '{name}' is off ({PLACES_ENV}=known), so it has no map coordinates"
        ]
    try:
        return lookup.look_up(name, near), [f"Found with {lookup.name}"]
    except Exception as exc:  # a failed lookup still leaves a usable place
        return plain_place(name), [f"Could not look up '{name}': {exc}. Kept without map coordinates"]
