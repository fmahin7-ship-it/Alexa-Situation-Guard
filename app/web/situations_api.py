"""Plain JSON endpoints over the predefined situation files (the original API).

They read data/situation_*.json directly: the starting plans, not saved state.
The agent and the web demo use the MCP tools and /api instead.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.feasibility import FeasibilityResult, evaluate_situation
from app.core.impact import ImpactResult, evaluate_impact
from app.core.models import Event, Situation
from app.situations.fixtures import describe_situation, list_situation_ids, load_all_situations, load_situation

router = APIRouter()


@router.get("/health")
def health():
    return {
        "status": "ok",
        "focus": "event_apply_impact_feasibility",
    }


@router.get("/situations")
def get_situations():
    """List all loaded situation ids (N supported)."""
    return {"situation_ids": list_situation_ids()}


@router.get("/situations/{situation_id}", response_model=Situation)
def get_situation(situation_id: str):
    try:
        return load_situation(situation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/situations/{situation_id}/summary")
def get_situation_summary(situation_id: str):
    try:
        situation = load_situation(situation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"situation_id": situation_id, "summary": describe_situation(situation)}


@router.get("/situations-full")
def get_all_situations():
    return load_all_situations()


@router.get("/situations/{situation_id}/feasibility", response_model=FeasibilityResult)
def get_feasibility(situation_id: str):
    try:
        situation = load_situation(situation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return evaluate_situation(situation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/situations/{situation_id}/impact", response_model=ImpactResult)
def post_impact(situation_id: str, event: Event):
    try:
        situation = load_situation(situation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return evaluate_impact(situation, event)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
