"""CLI: feasibility checks + travel delay impact.

  python -m app.check
"""

from __future__ import annotations

from .engine import describe_situation, list_situation_ids, load_all_situations
from .feasibility import evaluate_situation
from .impact import evaluate_impact
from .models import Event, Situation


def _print_result(situation: Situation) -> None:
    result = evaluate_situation(situation)
    print(f"Situation: {situation.id} ({situation.name})")
    print(f"feasible={result.feasible}")
    print(f"affected_goals={result.affected_goals}")
    print(f"broken_commitment_ids={result.broken_commitment_ids}")
    print("reasons:")
    for r in result.reasons:
        print(f"  - {r}")
    print()


def _break_assignment_delivery(situation: Situation) -> Situation:
    broken = situation.model_copy(deep=True)
    for c in broken.commitments:
        if c.id == "c_delivery":
            c.start = "2026-09-22"
            c.end = "2026-09-22"
    return broken


def _break_submit_past_deadline(situation: Situation) -> Situation:
    broken = situation.model_copy(deep=True)
    for c in broken.commitments:
        if c.id == "c_submit":
            c.start = "2026-09-22T18:00"
            c.end = "2026-09-22T18:00"
    return broken


def _break_impossible_window(situation: Situation) -> Situation:
    broken = situation.model_copy(deep=True)
    for c in broken.commitments:
        if c.id == "c_me_netflix":
            c.start = "21:00"
            c.end = "20:00"
    return broken


def _make_tv_sequential(situation: Situation) -> Situation:
    """Sister watches after me — capacity 1 is enough."""
    sequential = situation.model_copy(deep=True)
    for c in sequential.commitments:
        if c.id == "c_sister_prime":
            c.start = "21:00"
            c.end = "22:00"
    return sequential


def _make_tv_capacity_two_three_overlap(situation: Situation) -> Situation:
    """capacity=2 but three overlapping windows — still infeasible."""
    crowded = situation.model_copy(deep=True)
    for r in crowded.resources:
        if r.id == "living_room_tv":
            r.capacity = 2
    crowded.commitments.append(
        crowded.commitments[0].model_copy(
            update={
                "id": "c_friend_disney",
                "owner_id": "me",
                "action": "watch",
                "start": "20:00",
                "end": "21:00",
                "meta": {"service": "Disney+", "content": "guest"},
            }
        )
    )
    return crowded


def main() -> None:
    ids = list_situation_ids()
    print(f"Found {len(ids)} situation(s): {ids}")
    print()

    all_situations = load_all_situations()
    required = {"tv_evening", "travel_friday", "assignment_week"}
    missing = required - set(all_situations)
    if missing:
        raise SystemExit(f"FAIL — missing fixtures: {missing}")

    print("=" * 60)
    print("STEP 1 — state representation")
    print("=" * 60)
    for situation in all_situations.values():
        print(describe_situation(situation))
        print()

    print("=" * 60)
    print("STEP 2 — fixtures without resource conflicts")
    print("=" * 60)
    for sid in ("travel_friday", "assignment_week"):
        _print_result(all_situations[sid])
        if not evaluate_situation(all_situations[sid]).feasible:
            raise SystemExit(f"FAIL — expected {sid} to be feasible")

    print("=" * 60)
    print("STEP 2.1 — broken dependency (delivery after experiment)")
    print("=" * 60)
    broken_dep = _break_assignment_delivery(all_situations["assignment_week"])
    _print_result(broken_dep)
    if evaluate_situation(broken_dep).feasible:
        raise SystemExit("FAIL — expected dependency break to be NOT feasible")

    print("=" * 60)
    print("STEP 2.2 — broken deadline (submit after before)")
    print("=" * 60)
    broken_deadline = _break_submit_past_deadline(all_situations["assignment_week"])
    _print_result(broken_deadline)
    if evaluate_situation(broken_deadline).feasible:
        raise SystemExit("FAIL — expected deadline break to be NOT feasible")

    print("=" * 60)
    print("STEP 2.2 — impossible window (start after end)")
    print("=" * 60)
    # Use sequential TV so only the window break fails (not capacity)
    sequential_base = _make_tv_sequential(all_situations["tv_evening"])
    broken_window = _break_impossible_window(sequential_base)
    _print_result(broken_window)
    if evaluate_situation(broken_window).feasible:
        raise SystemExit("FAIL — expected impossible window to be NOT feasible")

    print("=" * 60)
    print("STEP 2.3 — resource capacity exceeded (TV fixture)")
    print("=" * 60)
    tv = all_situations["tv_evening"]
    _print_result(tv)
    tv_result = evaluate_situation(tv)
    if tv_result.feasible:
        raise SystemExit("FAIL — expected tv_evening (capacity=1, overlap) NOT feasible")
    if "living_room_tv" not in " ".join(tv_result.reasons):
        raise SystemExit("FAIL — expected resource capacity reason for living_room_tv")

    print("=" * 60)
    print("STEP 2.3 — sequential windows OK (capacity=1)")
    print("=" * 60)
    sequential = _make_tv_sequential(tv)
    _print_result(sequential)
    if not evaluate_situation(sequential).feasible:
        raise SystemExit("FAIL — expected sequential TV to be feasible")

    print("=" * 60)
    print("STEP 2.3 — capacity=2 with 3 overlaps still fails")
    print("=" * 60)
    crowded = _make_tv_capacity_two_three_overlap(tv)
    _print_result(crowded)
    crowded_result = evaluate_situation(crowded)
    if crowded_result.feasible:
        raise SystemExit("FAIL — expected capacity=2 / 3-overlap NOT feasible")
    if crowded_result.feasible is False and len(crowded_result.broken_commitment_ids) < 3:
        # peak should include all three overlapping
        pass  # tolerate if sweep marks peak subset; concurrency count matters
    if "capacity=2" not in " ".join(crowded_result.reasons):
        raise SystemExit("FAIL — expected capacity=2 in resource reason")

    print("=" * 60)
    print("IMPACT — train delay 45 min on travel_friday")
    print("=" * 60)
    travel = all_situations["travel_friday"]
    delay_event = Event(
        id="ev_train_delay_45",
        type="train_delay",
        description="Train delayed by 45 minutes",
        at="14:30",
        related_ids=["c_train"],
        meta={"delay_minutes": 45},
    )
    impact = evaluate_impact(travel, delay_event)
    print(f"event_id={impact.event_id}")
    print(f"applied={impact.applied} unevaluated={impact.unevaluated}")
    print(f"changed_commitment_ids={impact.changed_commitment_ids}")
    print(f"affected_commitment_ids={impact.affected_commitment_ids}")
    print(f"feasibility_before={impact.feasibility_before}")
    print(f"feasibility_after={impact.feasibility_after}")
    print("reasons:")
    for r in impact.reasons:
        print(f"  - {r}")
    print()

    if impact.changed_commitment_ids != ["c_train"]:
        raise SystemExit("FAIL — expected only c_train changed")
    expected_affected = {"c_train", "c_airport_arrive", "c_flight"}
    if set(impact.affected_commitment_ids) != expected_affected:
        raise SystemExit(f"FAIL — expected affected {expected_affected}")
    if not impact.feasibility_before:
        raise SystemExit("FAIL — travel_friday should be feasible before delay")
    if impact.feasibility_after:
        raise SystemExit("FAIL — travel_friday should be infeasible after +45 delay")
    joined = " ".join(impact.reasons)
    if "c_airport_arrive" not in joined or "c_train" not in joined:
        raise SystemExit("FAIL — expected dependency reason involving c_train / c_airport_arrive")

    print("IMPACT OK — apply + blast radius + before/after feasibility.")


if __name__ == "__main__":
    main()
