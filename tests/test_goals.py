"""Goals linked to the commitments they protect — across different kinds of situation."""

from __future__ import annotations

from app.candidate import Candidate, UpdateCommitment, simulate
from app.engine import load_situation
from app.feasibility import evaluate_situation, situation_errors
from app.impact import apply_event
from app.models import Event, Goal


def _set(situation, commitment_id, **fields):
    for c in situation.commitments:
        if c.id == commitment_id:
            for key, value in fields.items():
                setattr(c, key, value)
    return situation


def _delayed_trip():
    delay = Event(
        id="ev_train_delay",
        type="delay",
        description="Train running 45 minutes late",
        related_ids=["c_train"],
        meta={"delay_minutes": 45},
    )
    delayed, _, _, _ = apply_event(load_situation("travel_friday"), delay)
    return delayed


# --- you cannot "fix" a situation by cancelling what matters ---------------


def test_cancelling_the_flight_does_not_solve_the_delay():
    cancel_flight = Candidate(
        id="cand_cancel_flight",
        rationale="Dad skips the flight, so nothing is late",
        edits=[UpdateCommitment(commitment_id="c_flight", status="cancelled")],
    )
    result = simulate(_delayed_trip(), cancel_flight)
    assert result.applied and result.feasible is False
    assert "'c_flight' is cancelled, so goal 'goal_flight' (Dad boards the 18:00 flight) is lost" in result.reasons


def test_cancelling_the_submission_loses_the_assignment_goal():
    assignment = _set(load_situation("assignment_week"), "c_submit", status="cancelled")
    result = evaluate_situation(assignment)
    assert not result.feasible
    assert result.affected_goals == ["goal_submit"]
    assert any("goal 'goal_submit'" in r for r in result.reasons)


def test_cancelling_an_unprotected_commitment_is_allowed():
    # The train is not protected by any goal; replacing it is a normal repair.
    trip = _set(load_situation("travel_friday"), "c_train", status="cancelled")
    trip.dependencies = [d for d in trip.dependencies if d.to_id != "c_train"]
    assert evaluate_situation(trip).feasible


# --- affected_goals names only the goals really at risk --------------------


def test_delay_puts_the_flight_goal_at_risk_through_the_dependency_chain():
    # Broken: airport arrival. Flight depends on it, and the goal protects the flight.
    result = evaluate_situation(_delayed_trip())
    assert result.broken_commitment_ids == ["c_airport_arrive"]
    assert result.affected_goals == ["goal_flight"]


def test_only_the_goal_downstream_of_the_problem_is_affected():
    assignment = load_situation("assignment_week")
    assignment.goals.append(
        Goal(id="goal_laptop", description="Laptop arrives", owner_id="me", commitment_ids=["c_delivery"])
    )
    # Laptop arrives Monday: the experiment (Sunday) breaks, and submission depends on it.
    _set(assignment, "c_delivery", start="2026-09-22", end="2026-09-22")
    result = evaluate_situation(assignment)
    assert result.broken_commitment_ids == ["c_experiment"]
    assert result.affected_goals == ["goal_submit"]  # not goal_laptop: the delivery itself is fine


def test_unlinked_goal_still_counts_as_affected():
    trip = _delayed_trip()
    trip.goals.append(Goal(id="goal_dinner", description="Dinner after the airport"))  # no links
    assert evaluate_situation(trip).affected_goals == ["goal_flight", "goal_dinner"]


def test_no_problems_means_no_affected_goals():
    assert evaluate_situation(load_situation("travel_friday")).affected_goals == []


# --- a goal must point at real commitments ---------------------------------


def test_goal_naming_an_unknown_commitment_is_malformed():
    trip = load_situation("travel_friday")
    trip.goals[0].commitment_ids = ["c_helicopter"]
    assert situation_errors(trip) == ["Goal 'goal_flight' references unknown commitment 'c_helicopter'"]

    harmless = Candidate(
        id="cand_noop",
        rationale="Retime nothing important",
        edits=[UpdateCommitment(commitment_id="c_train", status="planned")],
    )
    result = simulate(trip, harmless)
    assert result.applied is False
    assert result.reasons == ["Goal 'goal_flight' references unknown commitment 'c_helicopter'"]


def test_shipped_situations_have_valid_goal_links():
    for situation_id in ("travel_friday", "assignment_week", "tv_evening"):
        situation = load_situation(situation_id)
        assert all(goal.commitment_ids for goal in situation.goals)
        assert situation_errors(situation) == []
