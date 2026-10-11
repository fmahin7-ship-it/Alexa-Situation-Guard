"""Candidate → Simulate → Feasibility.

A Candidate is a proposed recovery: a list of structured edits to a Situation.
The proposer (later an LLM) only suggests edits; the feasibility engine decides.

Edits are applied in order to a copy — the real Situation is never touched.
A candidate whose edits cannot be applied, or that leaves the situation
structurally broken (mixed time forms, wrong offsets, unknown locations),
is malformed, not infeasible.
"""

from __future__ import annotations

from typing import Annotated, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field

from app.core.feasibility import evaluate_situation, situation_errors
from app.core.models import Commitment, Dependency, Goal, Location, Resource, Situation, Unavailability


class UpdateCommitment(BaseModel):
    op: Literal["update_commitment"] = "update_commitment"
    commitment_id: str
    start: Optional[str] = None
    end: Optional[str] = None
    status: Optional[str] = None


class AddCommitment(BaseModel):
    op: Literal["add_commitment"] = "add_commitment"
    commitment: Commitment


class AddDependency(BaseModel):
    op: Literal["add_dependency"] = "add_dependency"
    dependency: Dependency


class AddLocation(BaseModel):
    op: Literal["add_location"] = "add_location"
    location: Location


class AddResource(BaseModel):
    op: Literal["add_resource"] = "add_resource"
    resource: Resource


class UpdateLocation(BaseModel):
    """Change what is known about a place (only the given fields)."""

    op: Literal["update_location"] = "update_location"
    location_id: str
    outdoor: Optional[bool] = None
    closes_on_public_holidays: Optional[bool] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class SetUnavailability(BaseModel):
    """Replace the windows one source (e.g. weather) put on a place; other sources' windows stay."""

    op: Literal["set_unavailability"] = "set_unavailability"
    location_id: str
    source: str
    windows: List[Unavailability] = Field(default_factory=list)


class AddGoal(BaseModel):
    op: Literal["add_goal"] = "add_goal"
    goal: Goal


class RemoveDependency(BaseModel):
    op: Literal["remove_dependency"] = "remove_dependency"
    dependency_id: str


Edit = Annotated[
    Union[
        UpdateCommitment, AddCommitment, AddDependency, RemoveDependency,
        AddLocation, UpdateLocation, AddResource, AddGoal, SetUnavailability,
    ],
    Field(discriminator="op"),
]


class Candidate(BaseModel):
    id: str
    rationale: str
    edits: List[Edit] = Field(default_factory=list)
    assumptions: List[str] = Field(
        default_factory=list,
        description="Facts the edits rely on but the engine cannot verify",
    )


class SimulationResult(BaseModel):
    candidate_id: str
    applied: bool
    feasible: Optional[bool] = Field(None, description="None when edits were not applied")
    broken_commitment_ids: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)


def _find_commitment(situation: Situation, commitment_id: str) -> Optional[Commitment]:
    return next((c for c in situation.commitments if c.id == commitment_id), None)


def _apply_edit(situation: Situation, edit: Edit) -> Optional[str]:
    """Mutate situation in place. Returns an error message if the edit is invalid."""
    if isinstance(edit, UpdateCommitment):
        commitment = _find_commitment(situation, edit.commitment_id)
        if commitment is None:
            return f"update_commitment: unknown commitment '{edit.commitment_id}'"
        if edit.start is None and edit.end is None and edit.status is None:
            return f"update_commitment: no fields to change on '{edit.commitment_id}'"
        if edit.start is not None:
            commitment.start = edit.start
        if edit.end is not None:
            commitment.end = edit.end
        if edit.status is not None:
            commitment.status = edit.status
        return None

    if isinstance(edit, AddCommitment):
        if _find_commitment(situation, edit.commitment.id) is not None:
            return f"add_commitment: commitment '{edit.commitment.id}' already exists"
        situation.commitments.append(edit.commitment.model_copy(deep=True))
        return None

    if isinstance(edit, AddDependency):
        dep = edit.dependency
        if any(d.id == dep.id for d in situation.dependencies):
            return f"add_dependency: dependency '{dep.id}' already exists"
        dependent = _find_commitment(situation, dep.from_id)
        if dependent is None:
            return f"add_dependency: unknown commitment '{dep.from_id}'"
        if _find_commitment(situation, dep.to_id) is None:
            return f"add_dependency: unknown commitment '{dep.to_id}'"
        situation.dependencies.append(dep.model_copy(deep=True))
        if dep.id not in dependent.dependency_ids:
            dependent.dependency_ids.append(dep.id)
        return None

    if isinstance(edit, AddLocation):
        if any(loc.id == edit.location.id for loc in situation.locations):
            return f"add_location: location '{edit.location.id}' already exists"
        situation.locations.append(edit.location.model_copy(deep=True))
        return None

    if isinstance(edit, AddResource):
        if any(r.id == edit.resource.id for r in situation.resources):
            return f"add_resource: resource '{edit.resource.id}' already exists"
        situation.resources.append(edit.resource.model_copy(deep=True))
        return None

    if isinstance(edit, UpdateLocation):
        place = next((loc for loc in situation.locations if loc.id == edit.location_id), None)
        if place is None:
            return f"update_location: unknown location '{edit.location_id}'"
        if edit.outdoor is not None:
            place.outdoor = edit.outdoor
        if edit.closes_on_public_holidays is not None:
            place.closes_on_public_holidays = edit.closes_on_public_holidays
        if edit.latitude is not None and edit.longitude is not None:
            place.latitude, place.longitude = edit.latitude, edit.longitude
        return None

    if isinstance(edit, SetUnavailability):
        place = next((loc for loc in situation.locations if loc.id == edit.location_id), None)
        if place is None:
            return f"set_unavailability: unknown location '{edit.location_id}'"
        if any(w.source != edit.source for w in edit.windows):
            return "set_unavailability: every window must come from the same source as the edit"
        kept = [w for w in place.unavailable if w.source != edit.source]
        place.unavailable = kept + [w.model_copy(deep=True) for w in edit.windows]
        return None

    if isinstance(edit, AddGoal):
        if any(g.id == edit.goal.id for g in situation.goals):
            return f"add_goal: goal '{edit.goal.id}' already exists"
        situation.goals.append(edit.goal.model_copy(deep=True))
        return None

    if isinstance(edit, RemoveDependency):
        if not any(d.id == edit.dependency_id for d in situation.dependencies):
            return f"remove_dependency: unknown dependency '{edit.dependency_id}'"
        situation.dependencies = [
            d for d in situation.dependencies if d.id != edit.dependency_id
        ]
        for c in situation.commitments:
            if edit.dependency_id in c.dependency_ids:
                c.dependency_ids.remove(edit.dependency_id)
        return None

    return f"unsupported edit: {edit!r}"


def apply_edits(situation: Situation, edits: List[Edit]) -> Tuple[Situation, List[str]]:
    """Apply edits in order to a copy. Stops at the first invalid edit."""
    updated = situation.model_copy(deep=True)
    for index, edit in enumerate(edits):
        error = _apply_edit(updated, edit)
        if error is not None:
            return updated, [f"edit {index}: {error}"]
    return updated, []


def simulate(situation: Situation, candidate: Candidate) -> SimulationResult:
    if not candidate.edits:
        return SimulationResult(
            candidate_id=candidate.id,
            applied=False,
            reasons=["Candidate has no edits"],
            assumptions=candidate.assumptions,
        )

    updated, errors = apply_edits(situation, candidate.edits)
    if not errors:
        errors = situation_errors(updated)
    if errors:
        return SimulationResult(
            candidate_id=candidate.id,
            applied=False,
            reasons=errors,
            assumptions=candidate.assumptions,
        )

    result = evaluate_situation(updated)
    return SimulationResult(
        candidate_id=candidate.id,
        applied=True,
        feasible=result.feasible,
        broken_commitment_ids=result.broken_commitment_ids,
        reasons=result.reasons,
        assumptions=candidate.assumptions,
    )
