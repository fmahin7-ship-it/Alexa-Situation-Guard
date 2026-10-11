"""Writing a plan in conversation. Each tool builds an engine edit and runs the same checks as a repair.

Generic: a plan can be a trip, a meeting day, an evening in front of one TV, ... Places are any
names; shared things (a TV, a room, a car) are resources with a capacity; times can be "now".
"""

from __future__ import annotations

from typing import List, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.core.candidate import AddCommitment, AddDependency, AddGoal, AddLocation, AddResource, Edit, apply_edits
from app.core.feasibility import situation_errors, time_form_errors
from app.core.models import Commitment, Dependency, Goal, Resource, Situation
from app.core.timeutil import shift_moment, zone_of
from app.mcp.context import ToolContext, evaluate, slug, unique
from app.mcp.views import PlanEditView
from app.situations.places import place_named
from app.situations.store import LogEntry, SessionState


def apply_plan_edits(ctx: ToolContext, state: SessionState, edits: List[Edit], added_id: str, what: str) -> PlanEditView:
    """Apply on a copy; refuse with reasons if malformed; otherwise save and report how the plan stands."""
    updated, errors = apply_edits(state.situation, edits)
    errors = errors or situation_errors(updated)
    if errors:
        raise ToolError("Not added: " + "; ".join(errors))
    state.situation = updated
    state.log.append(LogEntry(kind="plan_edited", detail=what, data={"added_id": added_id}))
    ctx.store.save(state)
    verdict = evaluate(updated)
    return PlanEditView(situation_id=updated.id, added_id=added_id, feasible=verdict.feasible, reasons=verdict.reasons)


def register(server: MCPServer, ctx: ToolContext) -> None:
    @server.tool()
    def create_situation(name: str, timezone: str = "Australia/Sydney", now: Optional[str] = None) -> PlanEditView:
        """
        Start a new, empty plan, e.g. "Dad's airport trip". `now` is the current time with offset
        (e.g. 2026-10-02T09:00:00+10:00). Returns its situation_id for the other tools.
        """
        errors = time_form_errors(Situation(id="draft", name=name, timezone=timezone, now=now))
        if errors:
            raise ToolError("Not created: " + "; ".join(errors))
        state = ctx.store.create(name, timezone=timezone, now=now)
        state.log.append(LogEntry(kind="plan_created", detail=name))
        ctx.store.save(state)
        return PlanEditView(situation_id=state.situation.id, added_id=state.situation.id, feasible=True)

    @server.tool()
    def add_place(situation_id: str, name: str) -> PlanEditView:
        """
        Add a place by name: "the drawing room", "the office", "Central Station", ... Returns its place id.
        Only places with known map coordinates can be used for travel search.
        """
        state = ctx.load(situation_id)
        place = place_named(name)
        if any(loc.id == place.id for loc in state.situation.locations):
            verdict = evaluate(state.situation)
            return PlanEditView(situation_id=situation_id, added_id=place.id, feasible=verdict.feasible, reasons=verdict.reasons)
        return apply_plan_edits(ctx, state, [AddLocation(location=place)], place.id, f"place {place.name}")

    @server.tool()
    def add_resource(situation_id: str, name: str, kind: str = "thing", capacity: int = 1) -> PlanEditView:
        """
        Add something that can only be used by `capacity` commitments at once, e.g. the living-room TV
        (capacity 1), a meeting room, the family car. Returns its resource id for add_commitment's `uses`.
        """
        if capacity < 1:
            raise ToolError("capacity must be at least 1")
        state = ctx.load(situation_id)
        resource_id = unique(f"res_{slug(name)}", {r.id for r in state.situation.resources})
        resource = Resource(id=resource_id, type=slug(kind), name=name, capacity=capacity)
        return apply_plan_edits(ctx, state, [AddResource(resource=resource)], resource_id, f"resource {name}")

    @server.tool()
    def add_commitment(
        situation_id: str,
        owner: str,
        action: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        at_place: Optional[str] = None,
        from_place: Optional[str] = None,
        to_place: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        uses: Optional[List[str]] = None,
    ) -> PlanEditView:
        """
        Add something someone will do: a meeting, a show, a flight, a journey, ...
        `start` is a time in the plan's form (e.g. 2026-10-02T14:00:00+10:00) or "now".
        Give `end` or `duration_minutes`; with neither, it is an instant (like "arrive by 4pm").
        `at_place` / `from_place` / `to_place` are place ids from add_place; `uses` lists resource ids
        from add_resource (e.g. the TV). A journey whose times are not known yet gets from_place and
        to_place and no times: find_options can then fill it with a real route.
        """
        state = ctx.load(situation_id)
        situation = state.situation
        if start is not None and start.strip().lower() == "now":
            if not situation.now:
                raise ToolError("This plan has no current time yet; give a clock time instead of 'now'")
            start = situation.now
        if duration_minutes is not None:
            if duration_minutes <= 0 or start is None:
                raise ToolError("duration_minutes needs a start and must be positive")
            if end is not None:
                raise ToolError("Give end or duration_minutes, not both")
            end = shift_moment(start, duration_minutes, zone_of(situation.timezone))
        commitment_id = unique(f"c_{slug(action)}", {c.id for c in situation.commitments})
        commitment = Commitment(
            id=commitment_id,
            owner_id=owner,
            action=slug(action),
            start=start,
            end=end if end is not None else start,
            location_id=at_place,
            origin_id=from_place,
            destination_id=to_place,
            resource_ids=list(uses or []),
        )
        return apply_plan_edits(ctx, state, [AddCommitment(commitment=commitment)], commitment_id, f"{owner}: {action}")

    @server.tool()
    def add_goal(situation_id: str, description: str, protects: List[str]) -> PlanEditView:
        """Say what matters most, e.g. "Dad boards the 6pm flight", and which commitment ids it protects."""
        state = ctx.load(situation_id)
        goal_id = unique(f"goal_{slug(description)}", {g.id for g in state.situation.goals})
        goal = Goal(id=goal_id, description=description, commitment_ids=protects)
        return apply_plan_edits(ctx, state, [AddGoal(goal=goal)], goal_id, f"goal: {description}")

    @server.tool()
    def add_dependency(situation_id: str, commitment_id: str, needs: str) -> PlanEditView:
        """`commitment_id` can only happen after `needs` has finished, e.g. the flight needs the airport arrival."""
        state = ctx.load(situation_id)
        dep_id = unique(f"dep_{commitment_id}_needs_{needs}", {d.id for d in state.situation.dependencies})
        dependency = Dependency(id=dep_id, from_id=commitment_id, to_id=needs)
        return apply_plan_edits(ctx, state, [AddDependency(dependency=dependency)], dep_id, f"{commitment_id} needs {needs}")
