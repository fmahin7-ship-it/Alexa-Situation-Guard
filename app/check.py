"""CLI: Steps 1 + 2.1 + 2.2

  python -m app.check
"""

from __future__ import annotations

from .engine import describe_situation, list_situation_ids, load_all_situations
from .feasibility import evaluate_situation
from .models import Situation


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
    print("STEP 2 — valid fixtures (deps + optional time)")
    print("=" * 60)
    for sid in sorted(all_situations):
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
    broken_window = _break_impossible_window(all_situations["tv_evening"])
    _print_result(broken_window)
    if evaluate_situation(broken_window).feasible:
        raise SystemExit("FAIL — expected impossible window to be NOT feasible")

    print("STEP 2.2 OK — optional time/deadline checks work without domain branches.")


if __name__ == "__main__":
    main()
