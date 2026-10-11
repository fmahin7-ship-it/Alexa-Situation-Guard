"""Google Calendar: read a day's events, add meetings, and move them — in the user's real calendar.

One-time sign-in (opens your browser; you approve on Google's own page):

    .venv/Scripts/python -m app.calendars.google login

It needs an OAuth client of type "Desktop app" from Google Cloud, saved as JSON at
SITUATION_GUARD_GOOGLE_CLIENT (default secrets/google_client.json, gitignored). The
sign-in token is kept at SITUATION_GUARD_GOOGLE_TOKEN (default secrets/google_token.json,
gitignored) — never under state/, where every *.json is a saved situation. Only calendar
events are in scope: nothing else in the Google account can be read.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.paths import REPO_ROOT, SECRETS_DIR, STATE_DIR
from app.situations.importers.calendar_ics import CalendarEvent

CLIENT_ENV = "SITUATION_GUARD_GOOGLE_CLIENT"
DEFAULT_CLIENT = SECRETS_DIR / "google_client.json"
TOKEN_ENV = "SITUATION_GUARD_GOOGLE_TOKEN"
DEFAULT_TOKEN = SECRETS_DIR / "google_token.json"
LEGACY_TOKEN = STATE_DIR / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
LOGIN_HINT = ".venv/Scripts/python -m app.calendars.google login"


class GoogleCalendarError(Exception):
    """Not signed in, no permission, or Google refused the request."""


def token_location() -> Path:
    """Where the sign-in token lives. A token still at the old state/ location is moved here once."""
    path = Path(os.environ.get(TOKEN_ENV) or DEFAULT_TOKEN)
    if not path.exists() and LEGACY_TOKEN.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(LEGACY_TOKEN), str(path))
    return path


def _moment(value: Dict[str, Any]) -> Optional[datetime]:
    text = value.get("dateTime")
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def parse_google_events(items: List[Dict[str, Any]]) -> List[CalendarEvent]:
    """Google's event resources -> CalendarEvent. Cancelled events are dropped; all-day ones are marked."""
    events = []
    for item in items:
        if item.get("status") == "cancelled":
            continue
        start, end = _moment(item.get("start", {})), _moment(item.get("end", {}))
        all_day = start is None
        if all_day:
            day = date.fromisoformat(item["start"]["date"])
            start = datetime.combine(day, time(0))
            end = start + timedelta(days=1)
        events.append(CalendarEvent(
            uid=item["id"],
            title=item.get("summary") or "(no title)",
            start=start,
            end=end or start,
            all_day=all_day,
            location=item.get("location") or None,
            repeats="recurringEventId" in item,
        ))
    return events


class GoogleCalendar:
    """The user's primary Google Calendar. `session` is an authorised requests-like session (tests pass a fake)."""

    name = "google-calendar"
    live = True

    def __init__(self, session: Any = None, token_path: Optional[Path] = None):
        self._session = session
        self._token_path = token_path

    def _token(self) -> Path:
        return self._token_path or token_location()

    @property
    def signed_in(self) -> bool:
        return self._session is not None or self._token().exists()

    def _authorised(self) -> Any:
        if self._session is not None:
            return self._session
        token = self._token()
        if not token.exists():
            raise GoogleCalendarError(f"Google Calendar is not signed in yet. Run: {LOGIN_HINT}")
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import AuthorizedSession, Request
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_file(str(token), SCOPES)
        if not credentials.valid:
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                raise GoogleCalendarError(f"Google sign-in expired. Run: {LOGIN_HINT}") from exc
            token.write_text(credentials.to_json(), encoding="utf-8")
        self._session = AuthorizedSession(credentials)
        return self._session

    def _call(self, method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            response = self._authorised().request(method, url, timeout=15, **kwargs)
        except GoogleCalendarError:
            raise
        except Exception as exc:  # network down, DNS, ...
            raise GoogleCalendarError(f"Could not reach Google Calendar: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json()["error"]["message"]
            except Exception:
                message = response.text[:200]
            raise GoogleCalendarError(f"Google Calendar refused ({response.status_code}): {message}")
        return response.json() if response.content else {}

    def events_on(self, day: date, zone: ZoneInfo) -> List[CalendarEvent]:
        """Timed events starting that local day, in time order (all-day ones left out, as for .ics)."""
        start = datetime.combine(day, time(0), tzinfo=zone)
        data = self._call("GET", EVENTS_URL, params={
            "timeMin": start.isoformat(),
            "timeMax": (start + timedelta(days=1)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 100,
        })
        events = [e for e in parse_google_events(data.get("items", [])) if not e.all_day]
        return [e for e in events if e.start.astimezone(zone).date() == day]

    def create(self, title: str, start: str, end: str, timezone: str, location: Optional[str] = None) -> str:
        """Add an event; returns Google's event id."""
        body: Dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
        }
        if location:
            body["location"] = location
        return self._call("POST", EVENTS_URL, json=body)["id"]

    def move(self, event_id: str, start: str, end: str, timezone: str) -> None:
        self._call("PATCH", f"{EVENTS_URL}/{event_id}", json={
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
        })


CALENDAR_ENV = "SITUATION_GUARD_CALENDAR"


def make_calendar() -> Optional[GoogleCalendar]:
    """SITUATION_GUARD_CALENDAR=google uses Google Calendar; off (default) means .ics files only."""
    choice = os.environ.get(CALENDAR_ENV, "off").strip().lower()
    if choice == "off":
        return None
    if choice == "google":
        return GoogleCalendar()
    raise ValueError(f"{CALENDAR_ENV} must be 'google' or 'off', not '{choice}'")


def login(client_path: Optional[Path] = None, token_path: Optional[Path] = None) -> None:
    """Open Google's consent page in the browser and save the token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    client = Path(client_path or os.environ.get(CLIENT_ENV) or DEFAULT_CLIENT)
    if not client.exists():
        raise SystemExit(f"No OAuth client file at {client}. Download it from Google Cloud (Desktop app).")
    credentials = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES).run_local_server(port=0)
    token_path = token_path or token_location()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Signed in. Token saved to {token_path}")


if __name__ == "__main__":
    if sys.argv[1:] != ["login"]:
        raise SystemExit(f"Usage: {LOGIN_HINT}")
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    login()
