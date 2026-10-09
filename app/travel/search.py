"""travel_search — the one way the rest of the app asks for journeys.

    travel_search(request) -> provider.search(request) -> TravelOption[]

Providers are interchangeable (Amazon Location today; Google, PTV, ... later).
The default provider replays recordings, so nothing costs money unless
SITUATION_GUARD_TRAVEL=live is set deliberately.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from ..models import Location
from .option import RejectedRoute, RouteParseResult, TravelMode, TravelOption

TRAVEL_ENV = "SITUATION_GUARD_TRAVEL"


class TravelSearchError(Exception):
    """The provider could not answer: no credentials, access denied, bad request, no recording, ..."""


class TravelRequest(BaseModel):
    origin: List[float] = Field(..., min_length=2, max_length=2, description="[longitude, latitude]")
    destination: List[float] = Field(..., min_length=2, max_length=2, description="[longitude, latitude]")
    mode: TravelMode
    depart_at: Optional[str] = Field(None, description="Leave at this moment (ISO with offset)")
    arrive_by: Optional[str] = Field(None, description="Arrive by this moment (ISO with offset)")
    max_alternatives: int = Field(2, ge=0, le=5, description="Extra routes besides the best one")

    @field_validator("depart_at", "arrive_by")
    @classmethod
    def _has_offset(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"'{value}' is not an ISO time") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"'{value}' needs a time zone offset, e.g. +10:00")
        return value

    @model_validator(mode="after")
    def _exactly_one_time(self) -> "TravelRequest":
        if (self.depart_at is None) == (self.arrive_by is None):
            raise ValueError("Give exactly one of depart_at or arrive_by")
        return self

    @classmethod
    def between(cls, origin: Location, destination: Location, **fields) -> "TravelRequest":
        """Build a request from two situation locations, which must have coordinates."""
        for loc in (origin, destination):
            if loc.latitude is None or loc.longitude is None:
                raise ValueError(f"Location '{loc.id}' has no coordinates")
        return cls(
            origin=[origin.longitude, origin.latitude],
            destination=[destination.longitude, destination.latitude],
            **fields,
        )

    def describe(self) -> str:
        when = f"depart {self.depart_at}" if self.depart_at else f"arrive by {self.arrive_by}"
        return f"{self.mode} {when}, up to {self.max_alternatives} alternatives"


class TravelProvider(Protocol):
    name: str
    live: bool

    def search(self, request: TravelRequest) -> RouteParseResult:
        """Return journeys for the request, or raise TravelSearchError."""
        ...


class TravelSearchResult(BaseModel):
    request: TravelRequest
    provider: str
    live: bool = Field(..., description="False when the answer is a replayed recording")
    options: List[TravelOption] = Field(default_factory=list)
    rejected: List[RejectedRoute] = Field(default_factory=list)


def make_travel_provider() -> TravelProvider:
    """recorded (default, free) or live (Amazon Location, costs per request)."""
    choice = os.environ.get(TRAVEL_ENV, "recorded").strip().lower()
    if choice == "recorded":
        from .recorded import RecordedProvider

        return RecordedProvider()
    if choice == "live":
        from .aws_location import AmazonLocationProvider

        return AmazonLocationProvider()
    raise TravelSearchError(f"{TRAVEL_ENV} must be 'recorded' or 'live', not '{choice}'")


def travel_search(request: TravelRequest, provider: Optional[TravelProvider] = None) -> TravelSearchResult:
    provider = provider or make_travel_provider()
    found = provider.search(request)
    return TravelSearchResult(
        request=request,
        provider=provider.name,
        live=provider.live,
        options=found.options,
        rejected=found.rejected,
    )
