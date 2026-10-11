"""Distances on the Earth's surface. Pure maths, no I/O."""

from __future__ import annotations

import math
from typing import Sequence

EARTH_RADIUS_M = 6_371_000


def distance_m(lon_lat_a: Sequence[float], lon_lat_b: Sequence[float]) -> float:
    """Great-circle distance in metres between two [longitude, latitude] points."""
    lon1, lat1 = math.radians(lon_lat_a[0]), math.radians(lon_lat_a[1])
    lon2, lat2 = math.radians(lon_lat_b[0]), math.radians(lon_lat_b[1])
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.asin(math.sqrt(a))
