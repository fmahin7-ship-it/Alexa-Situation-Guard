"""Reschedule — the generic option source for time clashes.

Works for anything with a time window: meetings, TV shows, study sessions,
deliveries, ... When a commitment clashes with others (same person, or a shared
resource such as one TV), it proposes moving it after them, before them, or
moving each of them after it. It only proposes; the engine decides each one
(e.g. a move into the past, or onto another clash, is rejected by try_option).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from app.core.candidate import Candidate, UpdateCommitment
from app.core.models import Commitment, Situation
from app.core.timeutil import minutes_between, parse_moment, shift_moment, zone_of
from app.options.base import OptionsFound
from app.options.choice import Choice

CANCELLED = "cancelled"


def _window(commitment: Commitment) -> Optional[Tuple[datetime, datetime]]:
    """Start and end when both are readable and the commitment takes time; otherwise None."""
    start, end = parse_moment(commitment.start), parse_moment(commitment.end)
    if start is None or end is None or not start < end:
        return None
    return start, end


def _clock(value: Optional[str]) -> str:
    moment = parse_moment(value)
    if moment is None:
        return value or "?"
    return moment.strftime("%H:%M")


def _who_what(c: Commitment) -> str:
    return f"{c.owner_id}'s {c.action.replace('_', ' ')}"


def _clashes(situation: Situation, commitment: Commitment) -> List[Commitment]:
    """Other live commitments overlapping this one in time that share its person or a resource."""
    mine = _window(commitment)
    if mine is None:
        return []
    limited = {r.id for r in situation.resources if r.capacity is not None}
    clashing = []
    for other in situation.commitments:
        if other.id == commitment.id or other.status == CANCELLED:
            continue
        theirs = _window(other)
        if theirs is None or not (mine[0] < theirs[1] and theirs[0] < mine[1]):
            continue
        same_person = (
            other.owner_id == commitment.owner_id
            and not other.meta.get("concurrent")
            and not commitment.meta.get("concurrent")
        )
        shared_resource = bool(set(other.resource_ids) & set(commitment.resource_ids) & limited)
        if same_person or shared_resource:
            clashing.append(other)
    return clashing


class RescheduleSource:
    name = "reschedule"

    def applies_to(self, situation: Situation, commitment: Commitment) -> bool:
        return commitment.status != CANCELLED and _window(commitment) is not None

    def find(
        self,
        situation: Situation,
        commitment: Commitment,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> OptionsFound:
        clashes = _clashes(situation, commitment)
        if not clashes:
            raise ValueError(
                f"'{commitment.id}' does not clash in time with anything, so there is nothing to reschedule around"
            )
        zone = zone_of(situation.timezone)
        length = minutes_between(commitment.start, commitment.end)

        # (commitment to move, new start, new end, why)
        moves: List[Tuple[Commitment, str, str, str]] = []
        last = max(clashes, key=lambda c: parse_moment(c.end))
        moves.append((commitment, last.end, shift_moment(last.end, length, zone), f"after {_who_what(last)}"))
        first = min(clashes, key=lambda c: parse_moment(c.start))
        moves.append((commitment, shift_moment(first.start, -length, zone), first.start, f"before {_who_what(first)}"))
        for other in clashes:
            other_length = minutes_between(other.start, other.end)
            moves.append(
                (other, commitment.end, shift_moment(commitment.end, other_length, zone), f"after {_who_what(commitment)}")
            )

        not_before, not_after = parse_moment(after), parse_moment(before)
        choices: List[Choice] = []
        seen = set()
        for moved, start, end, why in moves:
            key = (moved.id, start, end)
            if key in seen:
                continue
            if not_before is not None and parse_moment(start) < not_before:
                continue
            if not_after is not None and parse_moment(end) > not_after:
                continue
            seen.add(key)
            label = chr(ord("A") + len(choices))
            summary = f"{label} | Move {_who_what(moved)} to {_clock(start)}-{_clock(end)}, {why}"
            choices.append(
                Choice(
                    label=label,
                    summary=summary,
                    candidate=Candidate(
                        id=f"cand_move_{moved.id}_{label}",
                        rationale=summary,
                        edits=[UpdateCommitment(commitment_id=moved.id, start=start, end=end)],
                        assumptions=[f"{moved.owner_id} can do it at the new time"],
                    ),
                )
            )
        notes = [] if choices else ["Every possible move fell outside the requested time range"]
        return OptionsFound(source=self.name, live=False, choices=choices, notes=notes)
