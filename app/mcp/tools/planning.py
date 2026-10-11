"""Writing a plan in conversation. Each tool builds an engine edit and runs the same checks as a repair.

Generic: a plan can be a trip, a meeting day, an evening in front of one TV, ... Places are any
names; shared things (a TV, a room, a car) are resources with a capacity; times can be "now".
"""

from __future__ import annotations

from typing import List, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.core.candidate import (
    AddCommitment,
    AddDependency,
    AddGoal,
    AddLocation,
    AddResource,
    Edit,
    UpdateLocation,
)
from app.core.feasibility import time_form_errors
from app.core.models import Commitment, Dependency, Goal, Resource, Situation
from app.core.timeutil import shift_moment, zone_of
from app.mcp.context import ToolContext, apply_and_save, real_now, slug, unique
from app.mcp.views import PlanEditView
from app.situations.places import place_named, plain_place
from app.situations.store import LogEntry, SessionState


def apply_plan_edits(
    ctx: ToolContext, state: SessionState, edits: List[Edit], added_id: str, what: str, notes: Optional[List[str]] = None
) -> PlanEditView:
    """Apply with the engine's checks, save, and report how the plan stands."""
    verdict = apply_and_save(ctx, state, edits, "plan_edited", what, {"added_id": added_id})
    return PlanEditView(
        situation_id=state.situation.id,
        added_id=added_id,
        feasible=verdict.feasible,
        reasons=verdict.reasons,
        notes=notes or [],
    )


def _coordinates(latitude: Optional[float], longitude: Optional[float]) -> Optional[tuple]:
    """Both or neither, and on the globe."""
    if latitude is None and longitude is None:
        return None
    if latitude is None or longitude is None:
        raise ToolError("Give both latitude and longitude, or neither")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ToolError(f"({latitude}, {longitude}) is not a place on Earth")
    return latitude, longitude


def register(server: MCPServer, ctx: ToolContext) -> None:
    @server.tool()
    def create_situation(name: str, timezone: str = "Australia/Sydney", now: Optional[str] = None) -> PlanEditView:
        """
        Start a new, empty plan, e.g. "Dad's airport trip". `now` is "live" for the user's real life
        (the plan then always uses the real current time), or a fixed time with offset
        (e.g. 2026-10-02T09:00:00+10:00) for a pretend or past situation. Returns its situation_id.
        """
        live = now is not None and now.strip().lower() == "live"
        if live:
            now = real_now(timezone)
        errors = time_form_errors(Situation(id="draft", name=name, timezone=timezone, now=now))
        if errors:
            raise ToolError("Not created: " + "; ".join(errors))
        state = ctx.store.create(name, timezone=timezone, now=now)
        state.live_clock = live
        state.log.append(LogEntry(kind="plan_created", detail=name))
        ctx.store.save(state)
        return PlanEditView(situation_id=state.situation.id, added_id=state.situation.id, feasible=True)

    @server.tool()
    def add_place(
        situation_id: str,
        name: str,
        outdoor: bool = False,
        closes_on_public_holidays: bool = False,
        look_up: bool = False,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> PlanEditView:
        """
        Add a place by name: "the drawing room", "the office", "the garden", "Central Station", ...
        Returns its place id. Mark `outdoor` if weather matters there, `closes_on_public_holidays` for
        places like an office. Set `look_up` for real addresses or landmarks so they get map coordinates
        (needed for travel and weather); rooms and nicknames do not need it. Give `latitude` and
        `longitude` when they are known exactly, e.g. the user's current location. Adding an existing
        place again updates those marks (and its coordinates).
        """
        exact = _coordinates(latitude, longitude)
        state = ctx.load(situation_id)
        near = next(([loc.longitude, loc.latitude] for loc in state.situation.locations if loc.latitude is not None), None)
        if exact:
            place, notes = plain_place(name).model_copy(update={"latitude": exact[0], "longitude": exact[1]}), []
        else:
            place, notes = place_named(name, look_up=look_up, lookup=ctx.place_lookup, near=near)
        flags = {"outdoor": outdoor or None, "closes_on_public_holidays": closes_on_public_holidays or None}
        if exact:
            flags.update(latitude=exact[0], longitude=exact[1])
        existing = next((loc for loc in state.situation.locations if loc.id == place.id), None)
        if existing is not None:
            return apply_plan_edits(
                ctx, state, [UpdateLocation(location_id=place.id, **flags)], place.id, f"place {place.name}", notes
            )
        place = place.model_copy(update={"outdoor": outdoor, "closes_on_public_holidays": closes_on_public_holidays})
        if place.latitude is None:
            notes.append(f"'{place.name}' has no map coordinates: travel and weather cannot be checked for it")
        return apply_plan_edits(ctx, state, [AddLocation(location=place)], place.id, f"place {place.name}", notes)

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
