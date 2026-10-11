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


class WeatherProvider(Protocol):
    name: str
    live: bool

    def forecast(self, latitude: float, longitude: float, timezone: str) -> Forecast:
        """Hourly forecast for the next days, or raise WeatherError."""
        ...


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
