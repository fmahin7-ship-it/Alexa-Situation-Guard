"""Write confirmed plan changes back to the user's real calendar.

Only commitments linked to a calendar event (meta google_event_id) are touched, and
only after the user approved the change and the engine verified it.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from app.calendars.google import GoogleCalendar, GoogleCalendarError
from app.core.models import Situation

EVENT_ID = "google_event_id"


def _clock(moment: str) -> str:
    return datetime.fromisoformat(moment).strftime("%H:%M")


def push_moves(calendar: Optional[GoogleCalendar], before: Situation, after: Situation) -> List[str]:
    """Move each linked event whose times changed; one line per event saying what happened."""
    if calendar is None:
        return []
    previous = {c.id: c for c in before.commitments}
    updates = []
    for commitment in after.commitments:
        event_id = commitment.meta.get(EVENT_ID)
        old = previous.get(commitment.id)
        if not event_id or old is None or (old.start, old.end) == (commitment.start, commitment.end):
            continue
        title = commitment.meta.get("title") or commitment.action.replace("_", " ")
        try:
            calendar.move(event_id, commitment.start, commitment.end, after.timezone or "UTC")
            updates.append(f"Moved '{title}' in Google Calendar to {_clock(commitment.start)}-{_clock(commitment.end)}")
        except GoogleCalendarError as exc:
            updates.append(f"Could not move '{title}' in Google Calendar: {exc}")
    return updates
