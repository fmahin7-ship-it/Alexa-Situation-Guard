"""Place lookup with Amazon Location Places (SearchText): a name or address -> coordinates.

Each lookup is one billed request. Results are saved in the plan, so the request
says IntendedUse=Storage, as AWS requires for stored results (higher price tier).
Uses the situation-guard AWS profile; one attempt, no automatic retries.
Needs IAM: geo-places:SearchText on arn:aws:geo-places:<region>::provider/default.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from app.core.models import Location

PROFILE_ENV = "SITUATION_GUARD_AWS_PROFILE"
REGION_ENV = "SITUATION_GUARD_AWS_REGION"
COUNTRY_ENV = "SITUATION_GUARD_PLACES_COUNTRY"
DEFAULT_PROFILE = "situation-guard"
DEFAULT_REGION = "ap-southeast-2"
DEFAULT_COUNTRY = "AUS"
SYDNEY = [151.2093, -33.8688]  # [lon, lat]: where results are biased towards when nothing else is known


class PlaceLookupError(Exception):
    """The lookup failed: no profile, no permission, no match, ..."""


def search_text_params(name: str, near: Optional[List[float]] = None) -> Dict[str, Any]:
    return {
        "QueryText": name,
        "BiasPosition": list(near or SYDNEY),
        "Filter": {"IncludeCountries": [os.environ.get(COUNTRY_ENV, DEFAULT_COUNTRY)]},
        "MaxResults": 1,
        "IntendedUse": "Storage",
    }


def parse_search_text(name: str, response: Dict[str, Any]) -> Location:
    items = response.get("ResultItems") or []
    if not items:
        raise PlaceLookupError(f"No place found for '{name}'")
    item = items[0]
    position = item.get("Position")
    if not position or len(position) != 2:
        raise PlaceLookupError(f"'{name}' was found but has no position")
    title = item.get("Title") or name
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:30] or "place"
    return Location(
        id=f"loc_{slug}",
        name=title,
        address=(item.get("Address") or {}).get("Label"),
        longitude=position[0],
        latitude=position[1],
    )


class AmazonPlaceLookup:
    name = "amazon-location-places"
    live = True

    def __init__(self, client: Any = None):
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import ProfileNotFound

            profile = os.environ.get(PROFILE_ENV, DEFAULT_PROFILE)
            try:
                session = boto3.Session(profile_name=profile, region_name=os.environ.get(REGION_ENV, DEFAULT_REGION))
            except ProfileNotFound as exc:
                raise PlaceLookupError(f"AWS profile '{profile}' not found (set {PROFILE_ENV})") from exc
            self._client = session.client("geo-places", config=Config(retries={"total_max_attempts": 1}))
        return self._client

    def look_up(self, name: str, near: Optional[List[float]] = None) -> Location:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = self._get_client().search_text(**search_text_params(name, near))
        except (BotoCoreError, ClientError) as exc:
            raise PlaceLookupError(f"Amazon Location could not look up '{name}': {exc}") from exc
        return parse_search_text(name, response)
