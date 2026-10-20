"""Places — turn a spoken name into a Location.

Any name is a place ("the drawing room", "the office"). Coordinates, which only
travel and weather need, come from:
  - live (SITUATION_GUARD_PLACES=live): Amazon Location Places, searched near the user
    when asked to look the name up (billed per lookup). Nothing is built in, so
    "the airport" is whichever airport is near you.
  - offline (known, the default): a few built-in places with verified coordinates,
    the points the recorded demo routes were made between (Sydney Central, Sydney Airport).
Otherwise the place is kept without coordinates.

    SITUATION_GUARD_PLACES=known   (default) known places only, free and offline
    SITUATION_GUARD_PLACES=live    also look names up with Amazon Location
    SITUATION_GUARD_HOME_ADDRESS   what "home" means when it is looked up (kept in .env, not in code)
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Protocol, Tuple

from app.core.models import Location
from app.situations.places.known import find_place, known_place_names

__all__ = ["PlaceLookup", "find_place", "known_place_names", "make_place_lookup", "place_named"]

PLACES_ENV = "SITUATION_GUARD_PLACES"
HOME_ENV = "SITUATION_GUARD_HOME_ADDRESS"
HOME_WORDS = {"home", "my home", "my house", "house"}


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
    (place, notes). With a live lookup, names asked to be looked up are searched near the user and
    the built-in places are never used. Offline, the built-in places (made for the recorded demo)
    come with coordinates. Anything else is a plain place without coordinates.
    """
    if " ".join(name.lower().split()) in HOME_WORDS:
        return _home(look_up, lookup, near)
    if lookup is None:
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


def _home(look_up: bool, lookup: Optional[PlaceLookup], near: Optional[List[float]]) -> Tuple[Location, List[str]]:
    """Home, found from the address in SITUATION_GUARD_HOME_ADDRESS when it is looked up."""
    home = Location(id="loc_home", name="Home")
    address = os.environ.get(HOME_ENV, "").strip()
    if not look_up:
        return home, []
    if not address:
        return home, [f"No home address is set ({HOME_ENV}), so home has no map coordinates; ask for the suburb"]
    if lookup is None:
        return home, [f"Looking up home is off ({PLACES_ENV}=known), so it has no map coordinates"]
    try:
        found = lookup.look_up(address, near)
    except Exception as exc:
        return home, [f"Could not look up the home address: {exc}"]
    return found.model_copy(update={"id": "loc_home", "name": "Home"}), [f"Home found with {lookup.name}"]
