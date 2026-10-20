"""Writing a plan in conversation, then guarding it — through the real MCP tools. No LLM, no network."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest
from mcp import Client

from app.core.candidate import AddGoal, AddLocation, Candidate, simulate
from app.situations.fixtures import load_situation
from app.mcp.server import build_server
from app.core.models import Goal, Location
from app.situations.places import find_place
from app.situations.places.amazon_location import PlaceLookupError
from app.situations.store import SituationStore
from app.options.travel.recorded import RecordedProvider
from app.options.travel.source import TravelSource

MORNING = "2026-10-02T09:00:00+10:00"


class ToolFailed(Exception):
    pass


class Tools:
    def __init__(self, tmp_path, place_lookup=None):
        self.store = SituationStore(tmp_path)
        self.server = build_server(self.store, [TravelSource(RecordedProvider())], place_lookup=place_lookup)

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


def _write_dads_trip(tools: Tools) -> Dict[str, str]:
    """What Alexa would do for: "Dad flies at 6pm from Sydney airport; I'll take him from Central"."""
    sid = tools("create_situation", name="Dad's airport trip", now=MORNING)["situation_id"]
    central = tools("add_place", situation_id=sid, name="Central Station")["added_id"]
    airport = tools("add_place", situation_id=sid, name="Sydney Airport")["added_id"]
    flight = tools("add_commitment", situation_id=sid, owner="dad", action="board flight",
                   start="2026-10-02T18:00:00+10:00", at_place=airport)["added_id"]
    arrive = tools("add_commitment", situation_id=sid, owner="me", action="arrive airport",
                   start="2026-10-02T16:00:00+10:00", at_place=airport)["added_id"]
    journey = tools("add_commitment", situation_id=sid, owner="me", action="travel to airport",
                    from_place=central, to_place=airport)["added_id"]
    tools("add_goal", situation_id=sid, description="Dad boards the 6pm flight", protects=[flight])
    tools("add_dependency", situation_id=sid, commitment_id=flight, needs=arrive)
    tools("add_dependency", situation_id=sid, commitment_id=arrive, needs=journey)
    return {"sid": sid, "flight": flight, "arrive": arrive, "journey": journey}


# --- the whole story ----------------------------------------------------------


def test_morning_plan_then_afternoon_repair(tools):
    ids = _write_dads_trip(tools)
    sid = ids["sid"]
    view = tools("get_situation", situation_id=sid)
    assert view["feasible"] is True  # the journey has no times yet, so nothing is broken
    assert [g["description"] for g in view["goals"]] == ["Dad boards the 6pm flight"]

    # Morning: fill the journey so we arrive by 16:00.
    found = tools("find_options", situation_id=sid, commitment_id=ids["journey"], before="2026-10-02T16:00:00+10:00")
    assert [o["summary"] for o in found["options"]] == ["A | Transit | leave 15:31 -> arrive 15:53 | 22 min | T8"]
    assert tools("try_option", situation_id=sid, label="A")["feasible"] is True
    assert tools("confirm_option", situation_id=sid, label="A")["verified"] is True

    plan = {c["id"]: c for c in tools("get_situation", situation_id=sid)["commitments"]}
    assert plan["c_travel_A"]["start"] == "2026-10-02T15:31:00+10:00"
    assert plan[ids["journey"]]["status"] == "cancelled"  # the placeholder, replaced by the real journey

    # Afternoon: the 15:31 train is 45 minutes late, noticed at 15:05.
    change = tools("report_change", situation_id=sid, commitment_id="c_travel_A", kind="delay",
                   minutes=45, observed_at="2026-10-02T15:05:00+10:00")
    assert change["feasible_after"] is False
    assert change["goals_at_risk"] == [g["id"] for g in tools("get_situation", situation_id=sid)["goals"]]

    found = tools("find_options", situation_id=sid, commitment_id="c_travel_A")
    assert [o["label"] for o in found["options"]] == ["A", "B", "C", "D"]
    verdicts = [tools("try_option", situation_id=sid, label=x)["feasible"] for x in "ABCD"]
    assert verdicts == [False, False, False, True]
    assert tools("confirm_option", situation_id=sid, label="D")["verified"] is True

    plan = {c["id"]: c for c in tools("get_situation", situation_id=sid)["commitments"]}
    assert plan["c_travel_D"]["status"] == "planned"  # no clash with the morning's c_travel_A
    assert plan["c_travel_A"]["status"] == "cancelled"
    assert tools("get_situation", situation_id=sid)["feasible"] is True


def test_new_plan_is_listed_and_survives_a_new_server(tmp_path):
    first = Tools(tmp_path)
    sid = _write_dads_trip(first)["sid"]
    second = Tools(tmp_path)
    assert sid in [s["id"] for s in second("list_situations")]
    assert second("get_situation", situation_id=sid)["name"] == "Dad's airport trip"


# --- the engine refuses broken plan edits --------------------------------------


def test_plan_edits_are_checked_before_saving(tools):
    sid = tools("create_situation", name="Check me", now=MORNING)["situation_id"]
    with pytest.raises(ToolFailed, match="no offset"):
        tools("add_commitment", situation_id=sid, owner="me", action="meet", start="2026-10-02T10:00")
    with pytest.raises(ToolFailed, match="unknown location 'loc_nowhere'"):
        tools("add_commitment", situation_id=sid, owner="me", action="meet",
              start="2026-10-02T10:00:00+10:00", at_place="loc_nowhere")
    with pytest.raises(ToolFailed, match="unknown commitment"):
        tools("add_goal", situation_id=sid, description="x", protects=["c_ghost"])
    with pytest.raises(ToolFailed, match="unknown commitment"):
        tools("add_dependency", situation_id=sid, commitment_id="c_a", needs="c_b")
    assert tools("get_situation", situation_id=sid)["commitments"] == []  # nothing half-saved


def test_create_refuses_a_bad_time_or_zone(tools):
    with pytest.raises(ToolFailed, match="Not created"):
        tools("create_situation", name="x", now="2026-10-02T09:00")
    with pytest.raises(ToolFailed, match="Unknown timezone"):
        tools("create_situation", name="x", timezone="Mars/Olympus", now=MORNING)


def test_adding_the_same_place_twice_is_harmless(tools):
    sid = tools("create_situation", name="Twice", now=MORNING)["situation_id"]
    assert tools("add_place", situation_id=sid, name="central")["added_id"] == "loc_central"
    assert tools("add_place", situation_id=sid, name="Central Station")["added_id"] == "loc_central"
    assert len(tools.store.load(sid).situation.locations) == 1


def test_names_are_made_unique(tools):
    sid = tools("create_situation", name="Twice", now=MORNING)["situation_id"]
    again = tools("create_situation", name="Twice", now=MORNING)["situation_id"]
    assert (sid, again) == ("plan_twice", "plan_twice_2")
    a = tools("add_commitment", situation_id=sid, owner="me", action="meet", start=MORNING)["added_id"]
    b = tools("add_commitment", situation_id=sid, owner="me", action="meet", start=MORNING)["added_id"]
    assert (a, b) == ("c_meet", "c_meet_2")


# --- generic pieces ---------------------------------------------------------------


def test_add_location_and_goal_edits_work_like_any_other_edit():
    tv = load_situation("tv_evening")
    lounge = Location(id="loc_lounge", name="Lounge")
    candidate = Candidate(
        id="c1", rationale="describe the room and what matters",
        edits=[AddLocation(location=lounge), AddGoal(goal=Goal(id="goal_me", description="I watch", commitment_ids=["c_me_netflix"]))],
    )
    assert simulate(tv, candidate).applied is True
    duplicate = Candidate(id="c2", rationale="dup", edits=[AddLocation(location=lounge), AddLocation(location=lounge)])
    assert "already exists" in simulate(tv, duplicate).reasons[0]


def test_places_match_how_people_say_them():
    assert find_place("the airport").id == "loc_airport_t1"
    assert find_place("Sydney International Airport T1").id == "loc_airport_t1"
    assert find_place("Central").id == "loc_central"
    assert find_place("Melbourne") is None
    # A generic word inside another place's name is that other place, not a known one.
    assert find_place("Melbourne Airport") is None
    assert find_place("Melbourne Central") is None


def test_forget_deletes_only_plans_made_in_conversation(tools):
    sid = tools("create_situation", name="Temporary", now=MORNING)["situation_id"]
    tools.store.forget(sid)
    assert sid not in tools.store.list_ids()
    with pytest.raises(ValueError, match="predefined"):
        tools.store.forget("travel_friday")


class FlakyLookup:
    """Refuses like an AWS user without geo-places:SearchText, until the permission is granted."""

    name, live = "fake-places", False

    def __init__(self):
        self.allowed = False

    def look_up(self, name, near=None):
        if not self.allowed:
            raise PlaceLookupError("Amazon Location refused (AccessDeniedException)")
        return Location(id="loc_collins_st_melbourne_vic", name="Collins St, Melbourne VIC 3000",
                        latitude=-37.8146, longitude=144.9655)


def test_a_trip_to_an_unmapped_place_says_why_and_looking_it_up_again_fixes_it(tmp_path):
    lookup = FlakyLookup()
    tools = Tools(tmp_path, place_lookup=lookup)
    sid = tools("create_situation", name="My day Mon 12 Oct", now="2026-10-11T21:00:00+11:00")["situation_id"]
    here = tools("add_place", situation_id=sid, name="Current location", latitude=-37.69266, longitude=144.92994)
    refused = tools("add_place", situation_id=sid, name="Colin Street Melbourne", look_up=True)
    assert "AccessDeniedException" in refused["notes"][0]
    assert tools.store.load(sid).log[-1].data["notes"] == refused["notes"]  # the reason is kept in the plan
    trip = tools("add_commitment", situation_id=sid, owner="me", action="travel to Colin Street",
                 from_place=here["added_id"], to_place=refused["added_id"])["added_id"]

    with pytest.raises(ToolFailed, match="Colin Street Melbourne has no map coordinates"):
        tools("find_options", situation_id=sid, commitment_id=trip, after="2026-10-12T11:00:00+11:00")

    lookup.allowed = True
    found = tools("add_place", situation_id=sid, name="Colin Street Melbourne", look_up=True)
    assert found["added_id"] == refused["added_id"]  # same place, so the trip already points at it
    place = next(loc for loc in tools.store.load(sid).situation.locations if loc.id == found["added_id"])
    assert (place.latitude, place.longitude) == (-37.8146, 144.9655)
    # Routes are now searched (and this recorded provider has no Melbourne recording).
    with pytest.raises(ToolFailed, match="No travel search succeeded"):
        tools("find_options", situation_id=sid, commitment_id=trip, after="2026-10-12T11:00:00+11:00")
