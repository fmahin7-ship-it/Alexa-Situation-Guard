"""Situation State + Step 2.1 dependency feasibility API."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .engine import describe_situation, list_situation_ids, load_all_situations, load_situation
from .feasibility import FeasibilityResult, evaluate_situation
from .models import Situation

app = FastAPI(title="Commitment Graph — Step 2.2 (Feasibility)")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "step": "2.2",
        "focus": "dependency_and_optional_time_feasibility",
    }


@app.get("/situations")
def get_situations():
    """List all loaded situation ids (N supported)."""
    return {"situation_ids": list_situation_ids()}


@app.get("/situations/{situation_id}", response_model=Situation)
def get_situation(situation_id: str):
    try:
        return load_situation(situation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/situations/{situation_id}/summary")
def get_situation_summary(situation_id: str):
    try:
        situation = load_situation(situation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"situation_id": situation_id, "summary": describe_situation(situation)}


@app.get("/situations-full")
def get_all_situations():
    return load_all_situations()


@app.get("/situations/{situation_id}/feasibility", response_model=FeasibilityResult)
def get_feasibility(situation_id: str):
    try:
        situation = load_situation(situation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return evaluate_situation(situation)
