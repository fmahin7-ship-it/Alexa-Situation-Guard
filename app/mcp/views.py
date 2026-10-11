"""What the MCP tools return. Plain data shapes, built from the saved state and the engine's verdict."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.feasibility import FeasibilityResult
from app.situations.store import SessionState


class GoalView(BaseModel):
    id: str
    description: str
    at_risk: bool


class CommitmentView(BaseModel):
    id: str
    owner_id: str
    action: str
    start: Optional[str] = None
    end: Optional[str] = None
    status: str
    origin_id: Optional[str] = None
    destination_id: Optional[str] = None
    location_id: Optional[str] = None
    resource_ids: List[str] = Field(default_factory=list)


class OfferView(BaseModel):
    label: str
    summary: str
    assumptions: List[str] = Field(default_factory=list)


class SituationBrief(BaseModel):
    id: str
    name: str
    now: Optional[str] = None
    feasible: bool


class SituationView(BaseModel):
    id: str
    name: str
    timezone: Optional[str] = None
    now: Optional[str] = None
    feasible: bool
    reasons: List[str]
    broken_commitment_ids: List[str]
    goals: List[GoalView]
    commitments: List[CommitmentView]
    offered_for: Optional[str] = None
    offers: List[OfferView] = Field(default_factory=list)


class ChangeView(BaseModel):
    applied: bool
    changed_commitment_ids: List[str]
    affected_commitment_ids: List[str]
    feasible_before: bool
    feasible_after: bool
    goals_at_risk: List[str]
    reasons: List[str]


class OptionsView(BaseModel):
    commitment_id: str
    source: str
    live: bool = Field(..., description="True only when options come from a live external service (e.g. live travel search)")
    options: List[OfferView]
    notes: List[str] = Field(default_factory=list)


class TryView(BaseModel):
    label: str
    summary: str
    applied: bool = Field(..., description="False when the option could not even be applied (malformed)")
    feasible: Optional[bool] = None
    broken_commitment_ids: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)


class ConfirmView(BaseModel):
    label: str
    confirmed: bool
    verified: bool = Field(..., description="Saved plan was reloaded and re-checked by the engine")
    feasible: Optional[bool] = None
    reasons: List[str] = Field(default_factory=list)


class PlanEditView(BaseModel):
    situation_id: str
    added_id: str = Field(..., description="Id of what was added; use it in later calls")
    feasible: bool = Field(..., description="Whether the plan works so far (a plan being written may not yet)")
    reasons: List[str] = Field(default_factory=list)


def offer_views(state: SessionState) -> List[OfferView]:
    return [OfferView(label=o.label, summary=o.summary, assumptions=o.candidate.assumptions) for o in state.offers]


def situation_view(state: SessionState, verdict: FeasibilityResult) -> SituationView:
    situation = state.situation
    return SituationView(
        id=situation.id,
        name=situation.name,
        timezone=situation.timezone,
        now=situation.now,
        feasible=verdict.feasible,
        reasons=verdict.reasons,
        broken_commitment_ids=verdict.broken_commitment_ids,
        goals=[
            GoalView(id=g.id, description=g.description, at_risk=g.id in verdict.affected_goals)
            for g in situation.goals
        ],
        commitments=[
            CommitmentView(**c.model_dump(include=set(CommitmentView.model_fields))) for c in situation.commitments
        ],
        offered_for=state.offered_for,
        offers=offer_views(state),
    )
