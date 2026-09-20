"""Step 2 — Feasibility evaluation (no AI, no domain branches).

2.1 Dependency (requires): prerequisite end <= dependent start
2.2 Time (optional): commitment start <= end; deadline before when present
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from .models import Commitment, Constraint, Dependency, Situation


class FeasibilityResult(BaseModel):
    feasible: bool
    affected_goals: List[str] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    broken_commitment_ids: List[str] = Field(default_factory=list)
    unevaluated_dependency_ids: List[str] = Field(default_factory=list)
    unevaluated_constraint_ids: List[str] = Field(default_factory=list)


def _parse_moment(value: Optional[str]) -> Optional[datetime]:
    """Parse HH:MM, YYYY-MM-DD, or ISO datetime into a comparable datetime."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%H:%M"):
        try:
            dt = datetime.strptime(value, fmt)
            if fmt == "%H:%M":
                return dt.replace(year=2000, month=1, day=1)
            return dt
        except ValueError:
            continue
    return None


def _commitment_map(situation: Situation) -> Dict[str, Commitment]:
    return {c.id: c for c in situation.commitments}


def _requires_edges(situation: Situation) -> List[Dependency]:
    return [d for d in situation.dependencies if d.kind == "requires"]


def _dependency_time_ok(
    dependent: Commitment, prerequisite: Commitment
) -> Tuple[Optional[bool], str]:
    prereq_end = _parse_moment(prerequisite.end or prerequisite.start)
    dep_start = _parse_moment(dependent.start or dependent.end)

    if prereq_end is None or dep_start is None:
        return None, (
            f"Cannot evaluate dependency: missing times for "
            f"'{prerequisite.id}' and/or '{dependent.id}'"
        )

    if prereq_end <= dep_start:
        return True, (
            f"'{prerequisite.id}' ends by {prerequisite.end or prerequisite.start}, "
            f"before '{dependent.id}' starts at {dependent.start or dependent.end}"
        )

    return False, (
        f"'{dependent.id}' ({dependent.action}) needs '{prerequisite.id}' "
        f"({prerequisite.action}) to finish first, but "
        f"{prerequisite.end or prerequisite.start} is not before "
        f"{dependent.start or dependent.end}"
    )


def _self_time_ok(commitment: Commitment) -> Tuple[Optional[bool], str]:
    """If both start and end exist: require start <= end. Otherwise skip."""
    if not commitment.start or not commitment.end:
        return None, f"Skip self-time check for '{commitment.id}' (missing start or end)"

    start = _parse_moment(commitment.start)
    end = _parse_moment(commitment.end)
    if start is None or end is None:
        return None, f"Cannot parse times for '{commitment.id}'"

    if start <= end:
        return True, f"'{commitment.id}' window {commitment.start}-{commitment.end} is valid"

    return False, (
        f"'{commitment.id}' has impossible window: "
        f"start {commitment.start} is after end {commitment.end}"
    )


def _deadline_ok(
    commitment: Commitment, constraint: Constraint
) -> Tuple[Optional[bool], str]:
    """If constraint has before + commitment_id: commitment finish <= before."""
    if not constraint.before or not constraint.commitment_id:
        return None, f"Skip deadline for '{constraint.id}' (no before/commitment_id)"

    finish_raw = commitment.end or commitment.start
    finish = _parse_moment(finish_raw)
    deadline = _parse_moment(constraint.before)

    if finish is None or deadline is None:
        return None, (
            f"Cannot evaluate deadline '{constraint.id}': "
            f"missing/unparseable time on commitment or before"
        )

    if finish <= deadline:
        return True, (
            f"'{commitment.id}' finishes by {finish_raw}, "
            f"within deadline {constraint.before}"
        )

    return False, (
        f"'{commitment.id}' finishes at {finish_raw}, "
        f"after deadline {constraint.before} ({constraint.description})"
    )


def _goals_affected_by(
    situation: Situation, broken_commitment_ids: Set[str]
) -> List[str]:
    if not broken_commitment_ids:
        return []
    return [g.id for g in situation.goals] if situation.goals else sorted(broken_commitment_ids)


def _check_dependencies(
    situation: Situation,
    commitments: Dict[str, Commitment],
    reasons: List[str],
    broken: Set[str],
    unevaluated_deps: List[str],
) -> None:
    for dep in _requires_edges(situation):
        dependent = commitments.get(dep.from_id)
        prerequisite = commitments.get(dep.to_id)

        if dependent is None or prerequisite is None:
            unevaluated_deps.append(dep.id)
            reasons.append(
                f"Dependency '{dep.id}' references missing commitment "
                f"(from={dep.from_id}, to={dep.to_id})"
            )
            continue

        ok, detail = _dependency_time_ok(dependent, prerequisite)
        if ok is None:
            unevaluated_deps.append(dep.id)
            reasons.append(detail)
            continue
        if ok is False:
            broken.add(dependent.id)
            reasons.append(detail)


def _check_self_times(
    situation: Situation,
    reasons: List[str],
    broken: Set[str],
) -> None:
    for commitment in situation.commitments:
        ok, detail = _self_time_ok(commitment)
        if ok is None:
            continue  # no time dimension — skip silently
        if ok is False:
            broken.add(commitment.id)
            reasons.append(detail)


def _check_deadlines(
    situation: Situation,
    commitments: Dict[str, Commitment],
    reasons: List[str],
    broken: Set[str],
    unevaluated_constraints: List[str],
) -> None:
    for constraint in situation.constraints:
        if not constraint.before or not constraint.commitment_id:
            continue  # no deadline dimension on this constraint

        commitment = commitments.get(constraint.commitment_id)
        if commitment is None:
            unevaluated_constraints.append(constraint.id)
            reasons.append(
                f"Deadline constraint '{constraint.id}' references missing "
                f"commitment '{constraint.commitment_id}'"
            )
            continue

        ok, detail = _deadline_ok(commitment, constraint)
        if ok is None:
            unevaluated_constraints.append(constraint.id)
            reasons.append(detail)
            continue
        if ok is False:
            broken.add(commitment.id)
            reasons.append(detail)


def evaluate_situation(situation: Situation) -> FeasibilityResult:
    """
    Evaluate whether the current situation is feasible.

    Dimensions are optional:
    - dependencies evaluated when present
    - time/deadlines evaluated only when times / before are present
    """
    commitments = _commitment_map(situation)
    reasons: List[str] = []
    broken: Set[str] = set()
    unevaluated_deps: List[str] = []
    unevaluated_constraints: List[str] = []

    _check_dependencies(situation, commitments, reasons, broken, unevaluated_deps)
    _check_self_times(situation, reasons, broken)
    _check_deadlines(situation, commitments, reasons, broken, unevaluated_constraints)

    feasible = len(broken) == 0
    affected = _goals_affected_by(situation, broken)

    # Drop silent "skip" noise; keep real evaluation notes
    reasons = [r for r in reasons if not r.startswith("Skip ")]
    if feasible and not reasons:
        reasons.append("Situation passes evaluated dependency and time checks.")

    return FeasibilityResult(
        feasible=feasible,
        affected_goals=affected,
        reasons=reasons,
        broken_commitment_ids=sorted(broken),
        unevaluated_dependency_ids=unevaluated_deps,
        unevaluated_constraint_ids=unevaluated_constraints,
    )
