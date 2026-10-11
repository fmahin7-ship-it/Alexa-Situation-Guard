"""Provider-neutral forecasts, and turning wet hours into times a place is unavailable."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Protocol

from pydantic import BaseModel, Field

from app.core.models import Unavailability

SOURCE = "weather"
DEFAULT_RAIN_THRESHOLD = 60  # percent chance of rain at which an outdoor place counts as unusable


class WeatherError(Exception):
    """No forecast could be had: no network, no recording near that place, ..."""


class HourlyForecast(BaseModel):
    time: str = Field(..., description="Start of the hour, ISO with the place's offset")
    rain_probability: Optional[int] = None
    rain_mm: Optional[float] = None


class Forecast(BaseModel):
    provider: str
    live: bool = Field(..., description="False when replaying a recorded forecast")
    latitude: float
    longitude: float
    hours: List[HourlyForecast] = Field(default_factory=list)
    attribution: str = ""


class DayForecast(BaseModel):
    date: str
    conditions: str
    min_c: Optional[float] = None
    max_c: Optional[float] = None
    rain_chance: Optional[int] = Field(None, description="Highest hourly chance of rain that day, %")


class Conditions(BaseModel):
    """The weather right now and the days ahead, for answering "what's the weather"."""

    provider: str
    live: bool
    time: str
    temperature_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    conditions: str = ""
    days: List[DayForecast] = Field(default_factory=list)
    attribution: str = ""


class GeoPoint(BaseModel):
    name: str
    latitude: float
    longitude: float
    timezone: Optional[str] = None


class WeatherProvider(Protocol):
    name: str
    live: bool

    def forecast(self, latitude: float, longitude: float, timezone: str) -> Forecast:
        """Hourly forecast for the next days, or raise WeatherError."""
        ...

    def conditions(self, latitude: float, longitude: float, timezone: str) -> Conditions:
        """Now and the next days, or raise WeatherError."""
        ...

    def find(self, name: str) -> GeoPoint:
        """A town or city by name, or raise WeatherError."""
        ...


# WMO weather codes, as used by Open-Meteo, in plain words.
WMO_WORDS = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "heavy showers", 85: "snow showers", 86: "snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}


def describe_code(code: Optional[int]) -> str:
    return WMO_WORDS.get(code, "unknown") if code is not None else "unknown"


def rain_at(forecast: Forecast, moment: datetime) -> Optional[HourlyForecast]:
    """The forecast hour containing `moment`, if the forecast covers it."""
    for hour in forecast.hours:
        start = datetime.fromisoformat(hour.time)
        if start <= moment < start + timedelta(hours=1):
            return hour
    return None


def wet_windows(
    forecast: Forecast,
    day_start: datetime,
    day_end: datetime,
    threshold: int = DEFAULT_RAIN_THRESHOLD,
) -> List[Unavailability]:
    """Consecutive hours in [day_start, day_end) with rain at or above `threshold`%, merged into windows."""
    windows: List[Unavailability] = []
    run_start: Optional[datetime] = None
    run_end: Optional[datetime] = None
    run_peak = 0

    def close_run() -> None:
        if run_start is not None and run_end is not None:
            windows.append(
                Unavailability(
                    start=run_start.isoformat(),
                    end=run_end.isoformat(),
                    reason=f"rain likely (up to {run_peak}%)",
                    source=SOURCE,
                )
            )

    for hour in forecast.hours:
        start = datetime.fromisoformat(hour.time)
        end = start + timedelta(hours=1)
        inside = day_start <= start < day_end
        wet = inside and hour.rain_probability is not None and hour.rain_probability >= threshold
        if wet and run_end == start:
            run_end, run_peak = end, max(run_peak, hour.rain_probability)
        elif wet:
            close_run()
            run_start, run_end, run_peak = start, end, hour.rain_probability
        else:
            close_run()
            run_start = run_end = None
            run_peak = 0
    close_run()
    return windows
