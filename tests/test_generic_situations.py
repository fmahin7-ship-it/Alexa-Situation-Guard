"""Not just travel: an evening with one TV, a day of meetings — written and repaired with the same tools."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest
from mcp import Client

from app.agent.prompt import system_prompt
from app.core.feasibility import evaluate_situation
from app.core.models import Commitment, Situation
from app.core.timeutil import minutes_between, shift_moment, zone_of
from app.mcp.server import build_server
from app.options.reschedule import RescheduleSource
from app.options.travel.recorded import RecordedProvider
from app.options.travel.source import TravelSource
from app.situations.places import place_named
from app.situations.store import SituationStore

EVENING = "2026-10-11T19:00:00+11:00"  # Sunday evening, Sydney daylight saving
MONDAY_10AM = "2026-10-12T10:00:00+11:00"


class ToolFailed(Exception):
    pass


class Tools:
    def __init__(self, tmp_path):
        self.store = SituationStore(tmp_path)
        self.server = build_server(self.store, [TravelSource(RecordedProvider()), RescheduleSource()])

    def __call__(self, tool: str, **arguments: Any) -> Dict[str, Any]:
        async def go():
            async with Client(self.server) as client:
                return await client.call_tool(tool, arguments)

        result = asyncio.run(go())
        if result.is_error:
            raise ToolFailed(result.content[0].text)
        content = result.structured_content
        return content["result"] if set(content) == {"result"} else content


@pytest.fixture
def tools(tmp_path):
    return Tools(tmp_path)


# --- "I want to watch Traitors 2" ... "I want to watch Traitors 1" -----------------


def test_two_people_one_tv_no_times_given(tools):
    sid = tools("create_situation", name="Sunday evening TV", now=EVENING)["situation_id"]
    room = tools("add_place", situation_id=sid, name="drawing room")["added_id"]
    tv = tools("add_resource", situation_id=sid, name="drawing room TV", kind="tv", capacity=1)["added_id"]

    mine = tools("add_commitment", situation_id=sid, owner="me", action="watch Traitors 2",
                 start="now", duration_minutes=60, at_place=room, uses=[tv])
    assert mine["feasible"] is True
    hers = tools("add_commitment", situation_id=sid, owner="wife", action="watch Traitors 1",
                 start="now", duration_minutes=60, at_place=room, uses=[tv])
    assert hers["feasible"] is False
    assert any("capacity=1" in r and "overlap" in r for r in hers["reasons"])

    found = tools("find_options", situation_id=sid, commitment_id=hers["added_id"])
    assert found["source"] == "reschedule"
    assert [o["summary"] for o in found["options"]] == [
        "A | Move wife's watch traitors 1 to 20:00-21:00, after me's watch traitors 2",
        "B | Move wife's watch traitors 1 to 18:00-19:00, before me's watch traitors 2",
        "C | Move me's watch traitors 2 to 20:00-21:00, after wife's watch traitors 1",
    ]
    a, b, c = (tools("try_option", situation_id=sid, label=x) for x in "ABC")
    assert a["feasible"] is True
    assert b["feasible"] is False and "already" in b["reasons"][0]  # 18:00 is in the past
    assert c["feasible"] is True

    assert tools("confirm_option", situation_id=sid, label="A")["verified"] is True
    plan = {c["id"]: c for c in tools("get_situation", situation_id=sid)["commitments"]}
    assert plan[hers["added_id"]]["start"] == "2026-10-11T20:00:00+11:00"
    assert tools("get_situation", situation_id=sid)["feasible"] is True


# --- "meeting at 2pm" ... boss: "another meeting at 1pm for 2 hours" -----------------


def test_one_person_two_meetings(tools):
    sid = tools("create_situation", name="Monday at work", now=MONDAY_10AM)["situation_id"]
    office = tools("add_place", situation_id=sid, name="office")["added_id"]
    team = tools("add_commitment", situation_id=sid, owner="me", action="team meeting",
                 start="2026-10-12T14:00:00+11:00", duration_minutes=60, at_place=office)
    assert team["feasible"] is True

    boss = tools("add_commitment", situation_id=sid, owner="me", action="meeting with boss",
                 start="2026-10-12T13:00:00+11:00", duration_minutes=120, at_place=office)
    assert boss["feasible"] is False
    assert any("'me' cannot do two things at once" in r for r in boss["reasons"])

    found = tools("find_options", situation_id=sid, commitment_id=team["added_id"])
    assert [o["summary"] for o in found["options"]] == [
        "A | Move me's team meeting to 15:00-16:00, after me's meeting with boss",
        "B | Move me's team meeting to 12:00-13:00, before me's meeting with boss",
        "C | Move me's meeting with boss to 15:00-17:00, after me's team meeting",
    ]
    assert [tools("try_option", situation_id=sid, label=x)["feasible"] for x in "ABC"] == [True, True, True]
    assert tools("confirm_option", situation_id=sid, label="A")["verified"] is True


def test_nothing_to_reschedule_without_a_clash(tools):
    sid = tools("create_situation", name="Quiet day", now=MONDAY_10AM)["situation_id"]
    meeting = tools("add_commitment", situation_id=sid, owner="me", action="meeting",
                    start="2026-10-12T14:00:00+11:00", duration_minutes=30)["added_id"]
    with pytest.raises(ToolFailed, match="does not clash"):
        tools("find_options", situation_id=sid, commitment_id=meeting)


def test_travel_still_answers_for_journeys_between_known_places(tools):
    # The same server: a journey between places with coordinates goes to travel, not reschedule.
    found = tools("find_options", situation_id="travel_friday", commitment_id="c_train",
                  after="2026-10-02T15:00:00+10:00")
    assert found["source"] == "amazon-location (recorded)"


# --- "now", durations, resources ---------------------------------------------------------


def test_now_needs_a_current_time(tools):
    sid = tools("create_situation", name="No clock")["situation_id"]
    with pytest.raises(ToolFailed, match="no current time"):
        tools("add_commitment", situation_id=sid, owner="me", action="read", start="now", duration_minutes=30)


def test_duration_rules(tools):
    sid = tools("create_situation", name="Durations", now=EVENING)["situation_id"]
    with pytest.raises(ToolFailed, match="positive"):
        tools("add_commitment", situation_id=sid, owner="me", action="x", start="now", duration_minutes=0)
    with pytest.raises(ToolFailed, match="not both"):
        tools("add_commitment", situation_id=sid, owner="me", action="x", start="now",
              end="2026-10-11T20:00:00+11:00", duration_minutes=30)


def test_unknown_resource_is_refused(tools):
    sid = tools("create_situation", name="Resources", now=EVENING)["situation_id"]
    with pytest.raises(ToolFailed, match="unknown resource 'res_ghost'"):
        tools("add_commitment", situation_id=sid, owner="me", action="x", start="now", duration_minutes=30,
              uses=["res_ghost"])
    with pytest.raises(ToolFailed, match="capacity"):
        tools("add_resource", situation_id=sid, name="TV", capacity=0)


# --- the person rule, in the engine -------------------------------------------------------


def _day(*commitments: Commitment) -> Situation:
    return Situation(id="day", name="Day", commitments=list(commitments))


def _c(cid, owner, start, end, **extra) -> Commitment:
    return Commitment(id=cid, owner_id=owner, action=cid, start=start, end=end, **extra)


def test_same_person_overlapping_is_broken():
    result = evaluate_situation(_day(_c("a", "me", "13:00", "15:00"), _c("b", "me", "14:00", "15:00")))
    assert not result.feasible and result.broken_commitment_ids == ["a", "b"]


def test_different_people_instants_cancelled_and_concurrent_are_fine():
    assert evaluate_situation(_day(_c("a", "me", "13:00", "15:00"), _c("b", "you", "14:00", "15:00"))).feasible
    assert evaluate_situation(_day(_c("a", "me", "13:00", "15:00"), _c("b", "me", "14:00", "14:00"))).feasible
    assert evaluate_situation(_day(_c("a", "me", "13:00", "15:00"), _c("b", "me", "14:00", "15:00", status="cancelled"))).feasible
    assert evaluate_situation(
        _day(_c("drive", "me", "13:00", "15:00"), _c("podcast", "me", "14:00", "15:00", meta={"concurrent": True}))
    ).feasible


def test_back_to_back_is_not_a_clash():
    assert evaluate_situation(_day(_c("a", "me", "13:00", "14:00"), _c("b", "me", "14:00", "15:00"))).feasible


# --- small generic helpers ---------------------------------------------------------------


def test_any_name_is_a_place_but_only_known_ones_have_coordinates():
    room = place_named("Drawing Room")
    assert (room.id, room.name, room.latitude) == ("loc_drawing_room", "Drawing Room", None)
    assert place_named("Central").latitude is not None


def test_time_shift_keeps_the_form_and_crosses_daylight_saving():
    assert shift_moment("20:00", 60) == "21:00"
    # Sydney clocks go forward at 02:00 on 4 Oct 2026.
    assert shift_moment("2026-10-04T01:30:00+10:00", 60, zone_of("Australia/Sydney")) == "2026-10-04T03:30:00+11:00"
    assert minutes_between("13:00", "15:00") == 120


def test_alexa_is_told_the_current_time():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    text = system_prompt("Australia/Sydney", datetime(2026, 10, 11, 19, 0, tzinfo=ZoneInfo("Australia/Sydney")))
    assert "The current time is 2026-10-11T19:00:00+11:00 (Australia/Sydney), Sunday 11 October 2026" in text
