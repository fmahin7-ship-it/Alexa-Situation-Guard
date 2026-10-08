"""Option -> Choice -> recover(): real recorded journeys repairing the delayed airport trip.

Offline: recorded AWS responses only, no live calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.candidate import AddCommitment, AddDependency, Candidate, RemoveDependency, UpdateCommitment
from app.choice import Choice, ChoiceProposer
from app.engine import load_situation
from app.feasibility import evaluate_situation
from app.impact import apply_event
from app.models import Event
from app.recovery import recover
from app.travel.aws_location import parse_calculate_routes
from app.travel.option import merge_options
from app.travel.to_candidate import travel_option_to_choice

RECORDED = Path(__file__).resolve().parent.parent / "data" / "recorded"


def _options(*names: str):
    parsed = [
        parse_calculate_routes(json.loads((RECORDED / f"{n}.json").read_text(encoding="utf-8")), n).options
        for n in names
    ]
    return merge_options(*parsed)


def _delayed_trip():
    """travel_friday with the 15:00 train running 45 minutes late — airport arrival is broken."""
    situation = load_situation("travel_friday")
    delay = Event(
        id="ev_train_delay",
        type="delay",
        description="Train running 45 minutes late",
        related_ids=["c_train"],
        meta={"delay_minutes": 45},
    )
    delayed, _, applied, _ = apply_event(situation, delay)
    assert applied
    return delayed


def _choices(situation):
    options = _options("route_transit_alternatives", "route_car_depart_1500")
    return [travel_option_to_choice(situation, o, replaces="c_train") for o in options]


def _set_arrival_deadline(situation, hhmm: str):
    """Tighten the trip: be at the airport by hhmm instead of 16:00."""
    moment = f"2026-10-02T{hhmm}:00+10:00"
    tightened = situation.model_copy(deep=True)
    for c in tightened.commitments:
        if c.id == "c_airport_arrive":
            c.start = c.end = moment
    for constraint in tightened.constraints:
        if constraint.id == "con_arrive_before_flight":
            constraint.before = moment
    return tightened


# --- the translator --------------------------------------------------------


def test_choice_summaries_are_what_the_agent_reads():
    assert [(c.label, c.summary) for c in _choices(_delayed_trip())] == [
        ("A", "A | Car | leave 15:00 -> arrive 15:21 | 21 min | 10.5 km"),
        ("B", "B | Transit | leave 15:01 -> arrive 15:23 | 22 min | T8"),
        ("C", "C | Transit | leave 15:02 -> arrive 15:29 | 27 min | T4, change at Wolli Creek Station to T8"),
        ("D", "D | Transit | leave 15:10 -> arrive 15:32 | 22 min | T8"),
    ]


def test_choice_adds_journey_cancels_old_commitment_and_moves_dependency():
    choice_c = _choices(_delayed_trip())[2]
    add, cancel, remove, link = choice_c.candidate.edits

    assert isinstance(add, AddCommitment)
    journey = add.commitment
    assert journey.id == "c_travel_C"
    assert (journey.start, journey.end) == ("2026-10-02T15:02:00+10:00", "2026-10-02T15:29:00+10:00")
    assert (journey.owner_id, journey.origin_id, journey.destination_id) == ("me", "loc_central", "loc_airport_t1")
    assert journey.action == "travel_transit"
    assert journey.meta["option_id"] == "route_transit_alternatives:route3"

    assert isinstance(cancel, UpdateCommitment)
    assert (cancel.commitment_id, cancel.status) == ("c_train", "cancelled")

    assert isinstance(remove, RemoveDependency) and remove.dependency_id == "dep_airport_needs_train"
    assert isinstance(link, AddDependency)
    assert (link.dependency.from_id, link.dependency.to_id) == ("c_airport_arrive", "c_travel_C")

    assert choice_c.candidate.assumptions == [
        "Journey times come from amazon-location (route_transit_alternatives:route3)"
    ]


def test_car_choice_carries_the_traffic_assumption():
    car = _choices(_delayed_trip())[0]
    assert any("expected traffic" in a for a in car.candidate.assumptions)


def test_option_for_a_different_place_is_refused():
    situation = _delayed_trip()
    for loc in situation.locations:
        if loc.id == "loc_airport_t1":  # pretend the trip is to Melbourne Airport
            loc.latitude, loc.longitude = -37.6690, 144.8410
    option = _options("route_transit_alternatives")[0]
    with pytest.raises(ValueError, match="does not fit 'c_train'"):
        travel_option_to_choice(situation, option, replaces="c_train")


def test_location_without_coordinates_is_flagged_not_refused():
    situation = _delayed_trip()
    for loc in situation.locations:
        loc.latitude = loc.longitude = None
    choice = travel_option_to_choice(situation, _options("route_transit_alternatives")[0], replaces="c_train")
    assert "Route endpoints could not be checked against the situation's locations" in choice.candidate.assumptions


def test_unlabelled_option_or_unknown_commitment_is_refused():
    situation = _delayed_trip()
    labelled = _options("route_transit_alternatives")[0]
    with pytest.raises(ValueError, match="no label"):
        travel_option_to_choice(situation, labelled.model_copy(update={"label": None}), replaces="c_train")
    with pytest.raises(ValueError, match="Unknown commitment"):
        travel_option_to_choice(situation, labelled, replaces="c_helicopter")


# --- through the existing recovery loop ------------------------------------


def test_delayed_trip_is_broken_before_recovery():
    result = evaluate_situation(_delayed_trip())
    assert not result.feasible
    assert result.broken_commitment_ids == ["c_airport_arrive"]


def test_real_journey_repairs_the_delayed_trip():
    situation = _delayed_trip()
    outcome = recover(situation, ChoiceProposer(_choices(situation)))
    assert outcome.solved and outcome.stop_reason == "found_feasible"
    assert outcome.chosen_candidate.id == "cand_travel_A"
    assert len(outcome.attempts) == 1


def test_late_option_fails_then_engine_feedback_leads_to_one_that_works():
    # Must reach the airport by 15:30: D (arrives 15:32) is too late, B (15:23) is fine.
    situation = _set_arrival_deadline(_delayed_trip(), "15:30")
    outcome = recover(situation, ChoiceProposer(_choices(situation), order=["D", "B"]))

    first, second = outcome.attempts
    assert first.candidate.id == "cand_travel_D"
    assert first.result.applied and first.result.feasible is False
    assert "c_airport_arrive" in first.result.broken_commitment_ids
    assert any("c_travel_D" in reason for reason in first.result.reasons)

    assert second.candidate.id == "cand_travel_B"
    assert second.result.feasible is True
    assert outcome.chosen_candidate.id == "cand_travel_B"


def test_every_option_too_late_ends_without_a_plan():
    situation = _set_arrival_deadline(_delayed_trip(), "15:15")
    outcome = recover(situation, ChoiceProposer(_choices(situation)), max_attempts=10)
    assert not outcome.solved
    assert outcome.stop_reason == "no_more_candidates"
    assert [a.candidate.id for a in outcome.attempts] == [
        "cand_travel_A",
        "cand_travel_B",
        "cand_travel_C",
        "cand_travel_D",
    ]


def test_recovery_never_touches_the_real_situation():
    situation = _delayed_trip()
    before = situation.model_dump()
    recover(situation, ChoiceProposer(_choices(situation)))
    assert situation.model_dump() == before


# --- ChoiceProposer is generic, not travel-specific ------------------------


def test_choice_proposer_works_for_a_non_travel_situation():
    tv = load_situation("tv_evening")
    assert not evaluate_situation(tv).feasible
    watch_later = Choice(
        label="A",
        summary="A | Sister watches 21:00-22:00 instead",
        candidate=Candidate(
            id="cand_sister_later",
            rationale="Sister watches after me",
            edits=[UpdateCommitment(commitment_id="c_sister_prime", start="21:00", end="22:00")],
        ),
    )
    outcome = recover(tv, ChoiceProposer([watch_later]))
    assert outcome.solved and outcome.chosen_candidate.id == "cand_sister_later"


def test_choice_proposer_rejects_bad_labels():
    choice = _choices(_delayed_trip())[0]
    with pytest.raises(ValueError, match="Duplicate"):
        ChoiceProposer([choice, choice])
    with pytest.raises(ValueError, match="unknown labels: Z"):
        ChoiceProposer([choice], order=["Z"])
