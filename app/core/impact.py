"""Event → Apply → Impact → Feasibility (layer around the existing verifier).

Known effects:
  type "cancelled"   → mark related commitments cancelled
  meta.delay_minutes → shift related commitment start/end by that many minutes

Unknown effects: do not invent mutations; report unevaluated.
"""

from __future__ import annotations

from typing import Dict, List
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.core.feasibility import CANCELLED, evaluate_situation, find_affected_commitment_ids
from app.core.models import Commitment, Event, Situation
from app.core.timeutil import shift_moment

CANCELLED_EVENT = "cancelled"


class ImpactResult(BaseModel):
    event_id: str
    changed_commitment_ids: List[str] = Field(default_factory=list)
    affected_commitment_ids: List[str] = Field(default_factory=list)
    feasibility_before: bool
    feasibility_after: bool
    reasons: List[str] = Field(default_factory=list)
    applied: bool = True
    unevaluated: bool = False


def _commitment_map(situation: Situation) -> Dict[str, Commitment]:
    return {c.id: c for c in situation.commitments}


def apply_event(situation: Situation, event: Event) -> tuple[Situation, List[str], bool, List[str]]:
    """
    Apply a known event effect to a deep copy of the situation.

    Returns: (updated_situation, changed_commitment_ids, applied, notes)
    """
    updated = situation.model_copy(deep=True)
    notes: List[str] = []

    if event.id not in {e.id for e in updated.events}:
        updated.events.append(event)

    cancelling = event.type == CANCELLED_EVENT
    delay = 0
    if not cancelling:
        if "delay_minutes" not in event.meta:
            notes.append(
                f"Event '{event.id}' has no known effect "
                f"(expected type '{CANCELLED_EVENT}' or meta.delay_minutes) — not applied"
            )
            return updated, [], False, notes
        try:
            delay = int(event.meta["delay_minutes"])
        except (TypeError, ValueError):
            notes.append(
                f"Event '{event.id}' has unusable delay_minutes={event.meta.get('delay_minutes')!r} — not applied"
            )
            return updated, [], False, notes

    commitments = _commitment_map(updated)
    changed: List[str] = []
    zone = ZoneInfo(updated.timezone) if updated.timezone else None

    for related_id in event.related_ids:
        commitment = commitments.get(related_id)
        if commitment is None:
            notes.append(f"related_id '{related_id}' is not a commitment — skipped")
            continue

        before = (commitment.start, commitment.end, commitment.status)
        if cancelling:
            commitment.status = CANCELLED
        else:
            if commitment.start:
                commitment.start = shift_moment(commitment.start, delay, zone)
            if commitment.end:
                commitment.end = shift_moment(commitment.end, delay, zone)

        after = (commitment.start, commitment.end, commitment.status)
        # A 0-minute delay or a repeat cancellation still counts as applied to a known commitment.
        if (after != before or cancelling or delay == 0) and related_id not in changed:
            changed.append(related_id)

    if not changed and event.related_ids:
        notes.append(
            f"Event '{event.id}' related_ids did not match any commitment — not applied"
        )
        return updated, [], False, notes

    if not event.related_ids:
        notes.append(f"Event '{event.id}' has empty related_ids — not applied")
        return updated, [], False, notes

    return updated, changed, True, notes


def evaluate_impact(situation: Situation, event: Event) -> ImpactResult:
    """Full pipeline: before → apply → impact walk → after."""
    before = evaluate_situation(situation)
    updated, changed, applied, notes = apply_event(situation, event)

    if not applied:
        return ImpactResult(
            event_id=event.id,
            changed_commitment_ids=[],
            affected_commitment_ids=[],
            feasibility_before=before.feasible,
            feasibility_after=before.feasible,
            reasons=notes or ["Event was not applied; situation unchanged."],
            applied=False,
            unevaluated=True,
        )

    after = evaluate_situation(updated)
    affected = find_affected_commitment_ids(updated, set(changed))

    reasons = list(notes)
    if not after.feasible:
        reasons.extend(after.reasons)
    elif before.feasible and after.feasible:
        reasons.append(
            "Situation remains feasible after applying the event."
        )
    else:
        reasons.extend(after.reasons)

    return ImpactResult(
        event_id=event.id,
        changed_commitment_ids=changed,
        affected_commitment_ids=affected,
        feasibility_before=before.feasible,
        feasibility_after=after.feasible,
        reasons=reasons,
        applied=True,
        unevaluated=False,
    )
