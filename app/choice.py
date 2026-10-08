"""Choices — tool results the agent can pick from, already turned into Candidates.

Generic: any tool (travel, flights, restaurants, rooms, ...) produces options,
and a tool-specific translator turns each one into a Choice. The agent only
ever picks a label; deterministic code has already written the edits, so the
agent cannot invent times, places, or ids.

    tool -> options -> translator -> Choice[] -> proposer picks a label -> Candidate
         -> simulate() -> feasibility engine
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel

from .candidate import Candidate
from .feasibility import FeasibilityResult
from .models import Situation
from .recovery import Attempt


class Choice(BaseModel):
    label: str
    summary: str
    candidate: Candidate


class ChoiceProposer:
    """
    Proposes Choices in a fixed label order, skipping any already tried.
    Stand-in for an LLM that reads the summaries and failure reasons and picks
    the next label — it plugs into recover() through the same propose() signature.
    """

    def __init__(self, choices: List[Choice], order: Optional[List[str]] = None):
        self._by_label: Dict[str, Choice] = {}
        for choice in choices:
            if choice.label in self._by_label:
                raise ValueError(f"Duplicate choice label '{choice.label}'")
            self._by_label[choice.label] = choice

        self._order = list(order) if order is not None else [c.label for c in choices]
        unknown = [label for label in self._order if label not in self._by_label]
        if unknown:
            raise ValueError(f"Order names unknown labels: {', '.join(unknown)}")

    def propose(
        self,
        situation: Situation,
        failure: FeasibilityResult,
        history: List[Attempt],
    ) -> Optional[Candidate]:
        tried = {attempt.candidate.id for attempt in history}
        for label in self._order:
            candidate = self._by_label[label].candidate
            if candidate.id not in tried:
                return candidate
        return None
