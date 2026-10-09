"""Saved situation state, and 'cancelled' as a reported change."""

from __future__ import annotations

import pytest

from app.engine import load_situation
from app.impact import apply_event, evaluate_impact
from app.models import Event
from app.store import LogEntry, SituationStore


def _cancel(commitment_id: str) -> Event:
    return Event(id=f"ev_cancel_{commitment_id}", type="cancelled", description="cancelled", related_ids=[commitment_id])


# --- cancelled -------------------------------------------------------------


def test_cancelling_the_experiment_breaks_the_submission():
    impact = evaluate_impact(load_situation("assignment_week"), _cancel("c_experiment"))
    assert impact.applied
    assert impact.changed_commitment_ids == ["c_experiment"]
    assert impact.affected_commitment_ids == ["c_experiment", "c_submit"]
    assert impact.feasibility_before is True and impact.feasibility_after is False
    assert any("which is cancelled" in r for r in impact.reasons)


def test_cancelling_the_sisters_show_frees_the_tv_but_loses_the_shared_goal():
    impact = evaluate_impact(load_situation("tv_evening"), _cancel("c_sister_prime"))
    assert impact.feasibility_before is False and impact.feasibility_after is False
    assert impact.reasons == [
        "'c_sister_prime' is cancelled, so goal 'goal_watch' (Both watch our chosen shows tonight) is lost"
    ]


def test_cancel_updates_a_copy_and_records_the_event():
    original = load_situation("travel_friday")
    updated, changed, applied, _ = apply_event(original, _cancel("c_train"))
    assert applied and changed == ["c_train"]
    assert next(c for c in updated.commitments if c.id == "c_train").status == "cancelled"
    assert next(c for c in original.commitments if c.id == "c_train").status == "planned"
    assert updated.events[-1].id == "ev_cancel_c_train"


def test_cancelling_an_unknown_commitment_is_not_applied():
    _, changed, applied, notes = apply_event(load_situation("travel_friday"), _cancel("c_helicopter"))
    assert not applied and changed == []
    assert any("not a commitment" in n for n in notes)


def test_delay_on_a_commitment_without_times_is_still_not_applied():
    trip = load_situation("travel_friday")
    for c in trip.commitments:
        if c.id == "c_train":
            c.start = c.end = None
    delay = Event(id="ev_d", type="delay", description="late", related_ids=["c_train"], meta={"delay_minutes": 10})
    _, changed, applied, _ = apply_event(trip, delay)
    assert not applied and changed == []


# --- store -----------------------------------------------------------------


def test_load_starts_from_the_situation_file_without_writing(tmp_path):
    store = SituationStore(tmp_path)
    state = store.load("travel_friday")
    assert state.situation == load_situation("travel_friday")
    assert state.offers == [] and state.tries == []
    assert list(tmp_path.iterdir()) == []


def test_saved_changes_survive_a_new_store(tmp_path):
    state = SituationStore(tmp_path).load("travel_friday")
    state.situation.now = "2026-10-02T15:05:00+10:00"
    state.log.append(LogEntry(kind="note", detail="hello"))
    SituationStore(tmp_path).save(state)

    reloaded = SituationStore(tmp_path).load("travel_friday")
    assert reloaded.situation.now == "2026-10-02T15:05:00+10:00"
    assert reloaded.log[-1].detail == "hello"
    assert not list(tmp_path.glob("*.tmp"))  # atomic write leaves no temp file


def test_reset_restores_the_starting_file(tmp_path):
    store = SituationStore(tmp_path)
    state = store.load("travel_friday")
    state.situation.now = "2026-10-02T15:05:00+10:00"
    store.save(state)
    assert store.reset("travel_friday").situation.now is None
    assert store.load("travel_friday").situation.now is None


def test_unknown_or_unsafe_situation_ids_are_refused(tmp_path):
    store = SituationStore(tmp_path)
    with pytest.raises(KeyError):
        store.load("no_such_situation")
    with pytest.raises(KeyError):
        store.load("../secrets")
