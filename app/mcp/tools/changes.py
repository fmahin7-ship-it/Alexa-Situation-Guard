"""Reporting that reality changed: a delay or a cancellation. Saved, then judged by the engine."""

from __future__ import annotations

from typing import Literal, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.core.feasibility import find_affected_commitment_ids, time_form_errors
from app.core.impact import CANCELLED_EVENT, apply_event
from app.core.models import Event
from app.mcp.context import ToolContext, evaluate
from app.mcp.views import ChangeView
from app.situations.store import LogEntry


def register(server: MCPServer, ctx: ToolContext) -> None:
    @server.tool()
    def report_change(
        situation_id: str,
        commitment_id: str,
        kind: Literal["delay", "cancelled"],
        observed_at: str,
        minutes: Optional[int] = None,
        description: Optional[str] = None,
    ) -> ChangeView:
        """
        Record that reality changed: a commitment is delayed by `minutes`, or cancelled.
        `observed_at` becomes the situation's current time and must use the situation's time format
        (e.g. 2026-10-02T15:05:00+10:00). Saves the change and returns what broke.
        """
        state = ctx.load(situation_id)
        if kind == "delay" and minutes is None:
            raise ToolError("A delay needs minutes")

        situation = state.situation.model_copy(deep=True)
        situation.now = observed_at
        errors = time_form_errors(situation)
        if errors:
            raise ToolError(f"observed_at is not usable here: {'; '.join(errors)}")

        event = Event(
            id=f"ev_{len(situation.events) + 1}_{kind}_{commitment_id}",
            type=CANCELLED_EVENT if kind == "cancelled" else "delay",
            description=description or f"{commitment_id} {kind}" + (f" {minutes} min" if minutes else ""),
            at=observed_at,
            related_ids=[commitment_id],
            meta={} if kind == "cancelled" else {"delay_minutes": minutes},
        )
        before = evaluate(state.situation)  # as things stood, at the previous time
        updated, changed, applied, notes = apply_event(situation, event)
        if not applied:
            raise ToolError("; ".join(notes))
        after = evaluate(updated)

        state.situation = updated
        state.offers, state.offered_for = [], None  # earlier options were for the old reality
        state.log.append(LogEntry(kind="change_reported", detail=event.description, data={"event_id": event.id}))
        ctx.store.save(state)

        return ChangeView(
            applied=True,
            changed_commitment_ids=changed,
            affected_commitment_ids=find_affected_commitment_ids(updated, set(changed)),
            feasible_before=before.feasible,
            feasible_after=after.feasible,
            goals_at_risk=after.affected_goals,
            reasons=after.reasons,
        )
