"""Recovery loop: propose → simulate → verify → feed back → repeat.

The proposer only suggests candidates; simulate() and the feasibility engine decide.
Any proposer with the same propose() signature can replace ScriptedProposer —
an LLM-backed proposer later plugs in without changing the loop.

Every attempt is simulated against the same broken situation: attempts are
independent alternatives, not cumulative edits.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Protocol

from pydantic import BaseModel, Field

from .candidate import Candidate, SimulationResult, UpdateCommitment, simulate
from .feasibility import FeasibilityResult, evaluate_situation
from .models import Situation


StopReason = Literal[
    "already_feasible",
    "found_feasible",
    "max_attempts",
    "no_more_candidates",
]


class Attempt(BaseModel):
    candidate: Candidate
    result: SimulationResult


class RecoveryResult(BaseModel):
    solved: bool
    stop_reason: StopReason
    chosen_candidate: Optional[Candidate] = None
    initial_reasons: List[str] = Field(default_factory=list)
    attempts: List[Attempt] = Field(default_factory=list)


class Proposer(Protocol):
    def propose(
        self,
        situation: Situation,
        failure: FeasibilityResult,
        history: List[Attempt],
    ) -> Optional[Candidate]:
        """Return the next candidate to try, or None when out of ideas."""
        ...


def recover(
    situation: Situation,
    proposer: Proposer,
    max_attempts: int = 3,
) -> RecoveryResult:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    initial = evaluate_situation(situation)
    if initial.feasible:
        return RecoveryResult(
            solved=True,
            stop_reason="already_feasible",
            initial_reasons=initial.reasons,
        )

    attempts: List[Attempt] = []
    while len(attempts) < max_attempts:
        candidate = proposer.propose(situation, initial, list(attempts))
        if candidate is None:
            return RecoveryResult(
                solved=False,
                stop_reason="no_more_candidates",
                initial_reasons=initial.reasons,
                attempts=attempts,
            )

        result = simulate(situation, candidate)
        attempts.append(Attempt(candidate=candidate, result=result))

        if result.applied and result.feasible:
            return RecoveryResult(
                solved=True,
                stop_reason="found_feasible",
                chosen_candidate=candidate,
                initial_reasons=initial.reasons,
                attempts=attempts,
            )

    return RecoveryResult(
        solved=False,
        stop_reason="max_attempts",
        initial_reasons=initial.reasons,
        attempts=attempts,
    )


def _retimed_commitment_ids(candidate: Candidate) -> List[str]:
    return [
        e.commitment_id
        for e in candidate.edits
        if isinstance(e, UpdateCommitment) and (e.start is not None or e.end is not None)
    ]


class ScriptedProposer:
    """
    Stand-in for an LLM: proposes from a fixed list, in order, but reacts to feedback.

    After a failed attempt, it skips any remaining candidate that retimes a
    commitment the engine reported as broken — it stops shifting the part of
    the plan that just failed. Status-only edits (e.g. cancelling that
    commitment and routing around it) are still tried.
    """

    def __init__(self, candidates: List[Candidate]):
        self._queue = list(candidates)
        self.received_histories: List[List[Attempt]] = []
        self.skipped_ids: List[str] = []

    def propose(
        self,
        situation: Situation,
        failure: FeasibilityResult,
        history: List[Attempt],
    ) -> Optional[Candidate]:
        self.received_histories.append(history)

        broken: set = set()
        for attempt in history:
            if attempt.result.applied and attempt.result.feasible is False:
                broken.update(attempt.result.broken_commitment_ids)

        while self._queue:
            candidate = self._queue.pop(0)
            if broken & set(_retimed_commitment_ids(candidate)):
                self.skipped_ids.append(candidate.id)
                continue
            return candidate
        return None
