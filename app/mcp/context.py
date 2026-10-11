"""What every MCP tool shares: the store, the option sources, and turning failures into clear tool errors."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from mcp.server.mcpserver.exceptions import ToolError

from app.core.feasibility import FeasibilityResult, evaluate_situation
from app.core.models import Situation
from app.options.base import OptionSource
from app.situations.store import Offer, SessionState, SituationStore


@dataclass
class ToolContext:
    store: SituationStore
    sources: List[OptionSource] = field(default_factory=list)

    def load(self, situation_id: str) -> SessionState:
        try:
            return self.store.load(situation_id)
        except KeyError as exc:
            known = ", ".join(self.store.list_ids())
            raise ToolError(f"Unknown situation '{situation_id}'. Known: {known}") from exc


def evaluate(situation: Situation) -> FeasibilityResult:
    try:
        return evaluate_situation(situation)
    except ValueError as exc:
        raise ToolError(f"Situation cannot be evaluated: {exc}") from exc


def offer_for(state: SessionState, label: str) -> Offer:
    offer = state.offer(label)
    if offer is None:
        known = ", ".join(o.label for o in state.offers) or "none — call find_options first"
        raise ToolError(f"No option '{label}'. Current options: {known}")
    return offer


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "item"


def unique(base: str, taken: set) -> str:
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}_{n}", n + 1
    return candidate
