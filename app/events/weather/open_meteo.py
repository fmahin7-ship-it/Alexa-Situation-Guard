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
from app.events.weather.forecast import (
    Conditions,
    DayForecast,
    Forecast,
    GeoPoint,
    HourlyForecast,
    WeatherError,
    WeatherProvider,
    describe_code,
)
from app.http_json import FetchError, get_json
from app.paths import RECORDED_DIR

WEATHER_ENV = "SITUATION_GUARD_WEATHER"
URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
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


def parse_conditions(data: Dict[str, Any], provider: str, live: bool) -> Conditions:
    """Open-Meteo's `current` and `daily` blocks -> Conditions."""
    try:
        zone = ZoneInfo(data["timezone"])
        current, daily = data["current"], data["daily"]
        days = [
            DayForecast(date=day, conditions=describe_code(code), min_c=low, max_c=high, rain_chance=rain)
            for day, code, low, high, rain in zip(
                daily["time"], daily["weather_code"], daily["temperature_2m_min"],
                daily["temperature_2m_max"], daily["precipitation_probability_max"],
            )
        ]
    except (KeyError, TypeError) as exc:
        raise WeatherError(f"Unexpected weather shape: missing {exc}") from exc
    return Conditions(
        provider=provider,
        live=live,
        time=datetime.fromisoformat(current["time"]).replace(tzinfo=zone).isoformat(),
        temperature_c=current.get("temperature_2m"),
        feels_like_c=current.get("apparent_temperature"),
        conditions=describe_code(current.get("weather_code")),
        days=days,
        attribution=ATTRIBUTION,
    )


def parse_geocoding(data: Dict[str, Any], name: str) -> GeoPoint:
    results = data.get("results") or []
    if not results:
        raise WeatherError(f"No town or city called '{name}' was found")
    best = results[0]
    label = ", ".join(p for p in (best.get("name"), best.get("admin1"), best.get("country")) if p)
    return GeoPoint(name=label, latitude=best["latitude"], longitude=best["longitude"], timezone=best.get("timezone"))


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

    def conditions(self, latitude: float, longitude: float, timezone: str) -> Conditions:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,apparent_temperature,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": timezone,
            "forecast_days": 7,
        }
        try:
            data = get_json(URL, params)
        except FetchError as exc:
            raise WeatherError(f"Open-Meteo could not answer: {exc}") from exc
        return parse_conditions(data, self.name, self.live)

    def find(self, name: str) -> GeoPoint:
        """Open-Meteo's geocoder knows towns and cities, not street addresses; "Sydney, NSW" is tried as "Sydney"."""
        for query in dict.fromkeys([name.strip(), name.split(",")[0].strip()]):
            try:
                data = get_json(GEOCODING_URL, {"name": query, "count": 1, "language": "en", "format": "json"})
            except FetchError as exc:
                raise WeatherError(f"Open-Meteo could not answer: {exc}") from exc
            if data.get("results"):
                return parse_geocoding(data, query)
        raise WeatherError(f"No town or city called '{name}' was found")


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

    def conditions(self, latitude: float, longitude: float, timezone: str) -> Conditions:
        raise WeatherError(f"Today's weather needs a live forecast: set {WEATHER_ENV}=live")

    def find(self, name: str) -> GeoPoint:
        raise WeatherError(f"Finding a town by name needs {WEATHER_ENV}=live")


def make_weather_provider() -> WeatherProvider:
    choice = os.environ.get(WEATHER_ENV, "recorded").strip().lower()
    if choice == "recorded":
        return RecordedWeatherProvider()
    if choice == "live":
        return OpenMeteoProvider()
    raise WeatherError(f"{WEATHER_ENV} must be 'recorded' or 'live', not '{choice}'")
