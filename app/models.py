"""Step 1 — Generic Situation State (domain-agnostic foundation)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Person(BaseModel):
    id: str
    name: str


class Goal(BaseModel):
    id: str
    description: str
    owner_id: Optional[str] = None


class Resource(BaseModel):
    id: str
    type: str = Field(..., description="tv | car | room | train | device | restaurant | ...")
    name: str
    availability: Optional[str] = None


class Commitment(BaseModel):
    """Generic intent in time — movie, flight, meeting, delivery, study, etc."""

    id: str
    owner_id: str
    action: str
    start: Optional[str] = Field(None, description="HH:MM or ISO datetime")
    end: Optional[str] = None
    resource_ids: List[str] = Field(default_factory=list)
    dependency_ids: List[str] = Field(default_factory=list)
    status: str = "planned"
    meta: Dict[str, Any] = Field(default_factory=dict)


class Dependency(BaseModel):
    """from_id depends on to_id (e.g. dinner depends on leave_home)."""

    id: str
    from_id: str
    to_id: str
    kind: str = "requires"


class Constraint(BaseModel):
    id: str
    description: str
    commitment_id: Optional[str] = None
    before: Optional[str] = Field(
        None, description="Deadline moment — commitment must finish at or before this"
    )
    expression: Optional[str] = Field(
        None, description="Future general constraints; not evaluated in Step 2.2"
    )


class Event(BaseModel):
    """Observation about the world (delay, weather, delivery update, ...)."""

    id: str
    type: str
    description: str
    at: Optional[str] = None
    related_ids: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class Situation(BaseModel):
    """One ongoing slice of the world the agent can operate on. N situations allowed."""

    id: str
    name: str
    people: List[Person] = Field(default_factory=list)
    goals: List[Goal] = Field(default_factory=list)
    commitments: List[Commitment] = Field(default_factory=list)
    resources: List[Resource] = Field(default_factory=list)
    dependencies: List[Dependency] = Field(default_factory=list)
    constraints: List[Constraint] = Field(default_factory=list)
    events: List[Event] = Field(default_factory=list)
