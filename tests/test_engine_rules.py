"""Engine rules for cancelled commitments and "now" — checked across different kinds of situation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.candidate import Candidate, UpdateCommitment, simulate
from app.choice import ChoiceProposer
from app.engine import load_situation
from app.feasibility import evaluate_situation
from app.impact import apply_event
from app.models import Event
from app.recovery import recover
from app.travel.aws_location import parse_calculate_routes
from app.travel.option import merge_options
from app.travel.to_candidate import travel_option_to_choice

RECORDED = Path(__file__).resolve().parent.parent / "data" / "recorded"


def _set(situation, commitment_id, **fields):
    for c in situation.commitments:
        if c.id == commitment_id:
            for key, value in fields.items():
                setattr(c, key, value)
    return situation


def _only_my_show_matters():
    """TV evening where the goal protects only my show, so the sister's can be cancelled."""
    tv = load_situation("tv_evening")
    tv.goals[0].commitment_ids = ["c_me_netflix"]
    return tv


# --- cancelled commitments -------------------------------------------------


def test_tv_cancelled_show_frees_the_tv():
    tv = _only_my_show_matters()
    assert not evaluate_situation(tv).feasible  # both want the TV 20:00-21:00

    _set(tv, "c_sister_prime", status="cancelled")
    result = evaluate_situation(tv)
    assert result.feasible
    assert result.broken_commitment_ids == []


def test_tv_cancelling_a_show_the_goal_protects_frees_the_tv_but_loses_the_goal():
    tv = _set(load_situation("tv_evening"), "c_sister_prime", status="cancelled")  # goal: BOTH watch
    result = evaluate_situation(tv)
    assert not result.feasible
    assert result.reasons == [
        "'c_sister_prime' is cancelled, so goal 'goal_watch' (Both watch our chosen shows tonight) is lost"
    ]  # no TV clash any more — only the goal


def test_assignment_cannot_rely_on_a_cancelled_experiment():
    assignment = _set(load_situation("assignment_week"), "c_experiment", status="cancelled")
    result = evaluate_situation(assignment)
    assert not result.feasible
    assert result.broken_commitment_ids == ["c_submit"]
    assert "'c_submit' (submit_assignment) needs 'c_experiment' (run_experiment), which is cancelled" in result.reasons


def test_cancelled_dependent_no_longer_needs_its_prerequisite():
    # Delivery is late (Monday), which breaks the experiment on Sunday...
    assignment = _set(load_situation("assignment_week"), "c_delivery", start="2026-09-22", end="2026-09-22")
    assert "c_experiment" in evaluate_situation(assignment).broken_commitment_ids
    # ...but once both the experiment and the submission are cancelled, nothing needs the laptop.
    _set(assignment, "c_experiment", status="cancelled")
    _set(assignment, "c_submit", status="cancelled")
    result = evaluate_situation(assignment)
    assert "c_experiment" not in result.broken_commitment_ids


def test_cancelling_the_thing_a_deadline_protects_does_not_meet_it():
    assignment = _set(load_situation("assignment_week"), "c_submit", status="cancelled")
    result = evaluate_situation(assignment)
    assert not result.feasible
    assert result.broken_commitment_ids == ["c_submit"]
    assert any("cannot be met" in r for r in result.reasons)


def test_cancelled_commitment_with_impossible_window_is_not_reported():
    tv = _only_my_show_matters()
    _set(tv, "c_sister_prime", start="21:00", end="20:00", status="cancelled")
    assert evaluate_situation(tv).feasible


# --- now -------------------------------------------------------------------


def test_no_now_means_no_now_check():
    tv = load_situation("tv_evening")
    _set(tv, "c_sister_prime", start="21:00", end="22:00")
    assert tv.now is None
    assert evaluate_situation(tv).feasible


def test_tv_show_moved_to_a_time_already_past_is_rejected():
    tv = load_situation("tv_evening")
    tv.now = "19:30"
    move_earlier = Candidate(
        id="cand_sister_1900",
        rationale="Sister watches 19:00-20:00 instead",
        edits=[UpdateCommitment(commitment_id="c_sister_prime", start="19:00", end="20:00")],
    )
    result = simulate(tv, move_earlier)
    assert result.applied and result.feasible is False
    assert result.broken_commitment_ids == ["c_sister_prime"]
    assert result.reasons == ["'c_sister_prime' (watch) starts 19:00, but it is already 19:30"]


def test_only_planned_commitments_can_be_missed():
    tv = _only_my_show_matters()
    tv.now = "20:30"
    _set(tv, "c_sister_prime", status="cancelled")
    assert "c_me_netflix" in evaluate_situation(tv).broken_commitment_ids  # planned, started 20:00
    _set(tv, "c_me_netflix", status="in_progress")
    assert evaluate_situation(tv).feasible  # already watching — not missed


def test_date_only_commitment_is_missed_only_after_that_day():
    assignment = load_situation("assignment_week")
    assignment.now = "2026-09-21T15:00"
    # Experiment is "on 2026-09-21" — still that day, so not missed.
    assert "c_experiment" not in evaluate_situation(assignment).broken_commitment_ids

    assignment.now = "2026-09-22T09:00"
    result = evaluate_situation(assignment)
    assert "c_experiment" in result.broken_commitment_ids
    assert "c_delivery" in result.broken_commitment_ids


def test_now_must_use_the_situation_time_form():
    trip = load_situation("travel_friday")
    trip.now = "15:05"  # no date, no offset in a Sydney-timezone situation
    with pytest.raises(ValueError):
        evaluate_situation(trip)

    trip.now = "not a time"
    with pytest.raises(ValueError, match="Cannot parse situation now"):
        evaluate_situation(trip)


# --- travel: the demo moment -----------------------------------------------


def _delayed_trip_at(now: str):
    trip = load_situation("travel_friday")
    trip.now = now
    delay = Event(
        id="ev_train_delay",
        type="delay",
        description="Train running 45 minutes late",
        related_ids=["c_train"],
        meta={"delay_minutes": 45},
    )
    delayed, _, applied, _ = apply_event(trip, delay)
    assert applied
    return delayed


def _travel_choices(situation):
    parsed = [
        parse_calculate_routes(json.loads((RECORDED / f"{n}.json").read_text(encoding="utf-8")), n).options
        for n in ("route_transit_alternatives", "route_car_depart_1500")
    ]
    return [travel_option_to_choice(situation, o, replaces="c_train") for o in merge_options(*parsed)]


def test_journeys_that_already_left_are_rejected_and_the_next_one_wins():
    trip = _delayed_trip_at("2026-10-02T15:05:00+10:00")
    outcome = recover(trip, ChoiceProposer(_travel_choices(trip)), max_attempts=10)

    assert [a.candidate.id for a in outcome.attempts] == [
        "cand_travel_A",  # car, 15:00
        "cand_travel_B",  # 15:01
        "cand_travel_C",  # 15:02
        "cand_travel_D",  # 15:10
    ]
    for attempt in outcome.attempts[:3]:
        assert attempt.result.feasible is False
        assert any("but it is already 2026-10-02T15:05:00+10:00" in r for r in attempt.result.reasons)

    assert outcome.solved
    assert outcome.chosen_candidate.id == "cand_travel_D"


def test_replaced_commitment_in_the_past_does_not_block_recovery():
    # The original 15:00 train (now delayed) is cancelled by every choice, so it is never "missed".
    trip = _delayed_trip_at("2026-10-02T15:05:00+10:00")
    choice_d = _travel_choices(trip)[3]
    result = simulate(trip, choice_d.candidate)
    assert result.feasible is True
