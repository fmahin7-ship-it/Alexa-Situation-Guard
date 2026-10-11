"""Open-Meteo forecasts: live (free, keyless, non-commercial, CC BY 4.0) or replayed from a recording.

    SITUATION_GUARD_WEATHER=recorded   (default) replay data/recorded/weather/, free and offline
    SITUATION_GUARD_WEATHER=live       ask api.open-meteo.com
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from app.core.geo import distance_m
from app.events.weather.forecast import Forecast, HourlyForecast, WeatherError, WeatherProvider
from app.http_json import FetchError, get_json
from app.paths import RECORDED_DIR

WEATHER_ENV = "SITUATION_GUARD_WEATHER"
URL = "https://api.open-meteo.com/v1/forecast"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
RECORDING = RECORDED_DIR / "weather" / "sydney_central_forecast.json"
RECORDING_RADIUS_M = 50_000  # a recorded city forecast stands in for places within this distance


def parse_open_meteo(data: Dict[str, Any], provider: str, live: bool) -> Forecast:
    """Open-Meteo's hourly arrays (local times without offset) -> hours with the zone's offset."""
    try:
        zone = ZoneInfo(data["timezone"])
        hourly = data["hourly"]
        times: List[str] = hourly["time"]
        chances = hourly.get("precipitation_probability") or [None] * len(times)
        amounts = hourly.get("precipitation") or [None] * len(times)
    except (KeyError, TypeError) as exc:
        raise WeatherError(f"Unexpected forecast shape: missing {exc}") from exc
    hours = [
        HourlyForecast(
            time=datetime.fromisoformat(t).replace(tzinfo=zone).isoformat(),
            rain_probability=chance,
            rain_mm=amount,
        )
        for t, chance, amount in zip(times, chances, amounts)
    ]
    return Forecast(
        provider=provider,
        live=live,
        latitude=data.get("latitude", 0.0),
        longitude=data.get("longitude", 0.0),
        hours=hours,
        attribution=ATTRIBUTION,
    )


class OpenMeteoProvider:
    name = "open-meteo"
    live = True

    def forecast(self, latitude: float, longitude: float, timezone: str) -> Forecast:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "precipitation_probability,precipitation",
            "timezone": timezone,
            "forecast_days": 7,
        }
        try:
            data = get_json(URL, params)
        except FetchError as exc:
            raise WeatherError(f"Open-Meteo could not answer: {exc}") from exc
        return parse_open_meteo(data, self.name, self.live)


class RecordedWeatherProvider:
    """Replays one real Open-Meteo forecast. Refuses places too far from where it was recorded."""

    name = "open-meteo (recorded)"
    live = False

    def __init__(self, recording: Path = RECORDING):
        self._data = json.loads(recording.read_text(encoding="utf-8"))

    def forecast(self, latitude: float, longitude: float, timezone: str) -> Forecast:
        here = [self._data["longitude"], self._data["latitude"]]
        if distance_m([longitude, latitude], here) > RECORDING_RADIUS_M:
            raise WeatherError(
                f"No recorded forecast near ({latitude}, {longitude}); use {WEATHER_ENV}=live"
            )
        return parse_open_meteo(self._data, self.name, self.live)


def make_weather_provider() -> WeatherProvider:
    choice = os.environ.get(WEATHER_ENV, "recorded").strip().lower()
    if choice == "recorded":
        return RecordedWeatherProvider()
    if choice == "live":
        return OpenMeteoProvider()
    raise WeatherError(f"{WEATHER_ENV} must be 'recorded' or 'live', not '{choice}'")
