"""MCP tools end to end — in-process client, recorded travel data, temporary state. No AWS calls."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from app.candidate import Candidate, UpdateCommitment
from app.choice import Choice
from app.mcp_server import build_server
from app.models import Commitment, Situation
from app.options import OptionsFound
from app.store import SituationStore
from app.travel.recorded import RecordedProvider
from app.travel.source import TravelSource

TRIP = "travel_friday"
AT_1505 = "2026-10-02T15:05:00+10:00"
FROM_1500 = "2026-10-02T15:00:00+10:00"


class Session:
    """Calls tools on a fresh server whose state lives in a temporary folder."""

    def __init__(self, state_dir, sources=None):
        self.store = SituationStore(state_dir)
        self.server = build_server(self.store, sources or [TravelSource(RecordedProvider())])

    def call(self, tool: str, **arguments: Any) -> Dict[str, Any]:
        result = asyncio.run(self._call(tool, arguments))
        if result.is_error:
            raise ToolFailed(result.content[0].text)
        content = result.structured_content
        return content["result"] if set(content) == {"result"} else content

    async def _call(self, tool, arguments):
        async with Client(self.server) as client:
            return await client.call_tool(tool, arguments)


class ToolFailed(Exception):
    pass


@pytest.fixture
def session(tmp_path):
    return Session(tmp_path)


def _delay_train(session: Session):
    return session.call(
        "report_change", situation_id=TRIP, commitment_id="c_train", kind="delay", minutes=45, observed_at=AT_1505
    )


# --- the whole loop --------------------------------------------------------


def test_tools_are_listed(session):
    async def names():
        async with Client(session.server) as client:
            return [t.name for t in (await client.list_tools()).tools]

    assert asyncio.run(names()) == [
        "list_situations",
        "get_situation",
        "report_change",
        "find_options",
        "try_option",
        "confirm_option",
    ]


def test_delay_then_reject_three_then_confirm_the_fourth(session):
    change = _delay_train(session)
    assert change["feasible_before"] is True and change["feasible_after"] is False
    assert change["goals_at_risk"] == ["goal_flight"]

    found = session.call("find_options", situation_id=TRIP, commitment_id="c_train", after=FROM_1500)
    assert found["live"] is False
    assert [o["label"] for o in found["options"]] == ["A", "B", "C", "D"]

    for label in ("A", "B", "C"):
        tried = session.call("try_option", situation_id=TRIP, label=label)
        assert tried["feasible"] is False
        assert any(f"but it is already {AT_1505}" in r for r in tried["reasons"])
    assert session.call("try_option", situation_id=TRIP, label="D")["feasible"] is True

    confirmed = session.call("confirm_option", situation_id=TRIP, label="D")
    assert confirmed["confirmed"] is True and confirmed["verified"] is True

    view = session.call("get_situation", situation_id=TRIP)
    assert view["feasible"] is True and view["offers"] == []
    statuses = {c["id"]: c["status"] for c in view["commitments"]}
    assert statuses["c_train"] == "cancelled" and statuses["c_travel_D"] == "planned"


def test_state_survives_a_new_server(tmp_path):
    first = Session(tmp_path)
    _delay_train(first)
    first.call("find_options", situation_id=TRIP, commitment_id="c_train", after=FROM_1500)

    second = Session(tmp_path)  # e.g. server restarted, new conversation
    view = second.call("get_situation", situation_id=TRIP)
    assert view["now"] == AT_1505 and view["feasible"] is False
    assert [o["label"] for o in view["offers"]] == ["A", "B", "C", "D"]
    assert second.call("try_option", situation_id=TRIP, label="D")["feasible"] is True


def test_tries_and_changes_are_logged(session):
    _delay_train(session)
    session.call("find_options", situation_id=TRIP, commitment_id="c_train", after=FROM_1500)
    session.call("try_option", situation_id=TRIP, label="A")
    state = session.store.load(TRIP)
    assert [e.kind for e in state.log] == ["change_reported", "options_found", "option_tried"]
    assert state.tries[0].label == "A" and state.tries[0].feasible is False and state.tries[0].at == AT_1505


# --- the engine stays in charge --------------------------------------------


def test_confirm_refuses_an_option_the_engine_rejects(session):
    _delay_train(session)
    session.call("find_options", situation_id=TRIP, commitment_id="c_train", after=FROM_1500)
    refused = session.call("confirm_option", situation_id=TRIP, label="B")
    assert refused["confirmed"] is False and refused["verified"] is False
    statuses = {c["id"]: c["status"] for c in session.call("get_situation", situation_id=TRIP)["commitments"]}
    assert statuses["c_train"] == "planned" and "c_travel_B" not in statuses


def test_try_option_never_changes_the_plan(session):
    _delay_train(session)
    session.call("find_options", situation_id=TRIP, commitment_id="c_train", after=FROM_1500)
    before = session.store.load(TRIP).situation
    session.call("try_option", situation_id=TRIP, label="D")
    assert session.store.load(TRIP).situation == before


def test_a_new_change_withdraws_old_options(session):
    _delay_train(session)
    session.call("find_options", situation_id=TRIP, commitment_id="c_train", after=FROM_1500)
    session.call(
        "report_change", situation_id=TRIP, commitment_id="c_train", kind="delay", minutes=10, observed_at=AT_1505
    )
    with pytest.raises(ToolFailed, match="call find_options first"):
        session.call("try_option", situation_id=TRIP, label="D")


# --- cancelled through the tools ---------------------------------------------


def test_reporting_a_cancelled_flight_is_saved_and_loses_the_goal(session):
    change = session.call(
        "report_change", situation_id=TRIP, commitment_id="c_flight", kind="cancelled", observed_at=AT_1505
    )
    assert change["feasible_after"] is False
    assert any("goal 'goal_flight'" in r for r in change["reasons"])
    statuses = {c["id"]: c["status"] for c in session.call("get_situation", situation_id=TRIP)["commitments"]}
    assert statuses["c_flight"] == "cancelled"


# --- clear errors, nothing saved -------------------------------------------


def test_errors_are_clear_and_save_nothing(session, tmp_path):
    with pytest.raises(ToolFailed, match="Unknown situation 'nope'"):
        session.call("get_situation", situation_id="nope")
    with pytest.raises(ToolFailed, match="needs minutes"):
        session.call("report_change", situation_id=TRIP, commitment_id="c_train", kind="delay", observed_at=AT_1505)
    with pytest.raises(ToolFailed, match="no offset"):
        session.call(
            "report_change", situation_id=TRIP, commitment_id="c_train", kind="delay", minutes=5,
            observed_at="2026-10-02T15:05",
        )
    with pytest.raises(ToolFailed, match="not a commitment"):
        session.call("report_change", situation_id=TRIP, commitment_id="c_zz", kind="cancelled", observed_at=AT_1505)
    with pytest.raises(ToolFailed, match="call find_options first"):
        session.call("try_option", situation_id=TRIP, label="A")
    assert list(tmp_path.iterdir()) == []


def test_search_time_without_a_recording_is_reported_honestly(session):
    _delay_train(session)  # now = 15:05; only 15:00 searches were recorded
    with pytest.raises(ToolFailed, match="No recording"):
        session.call("find_options", situation_id=TRIP, commitment_id="c_train")


# --- generic: tools do not care what domain the options come from ----------


class SisterWatchesLater:
    """A non-travel option source, to prove the tools are domain-free."""

    name = "household-tv"

    def applies_to(self, situation: Situation, commitment: Commitment) -> bool:
        return "living_room_tv" in commitment.resource_ids

    def find(self, situation, commitment, after: Optional[str] = None, before: Optional[str] = None) -> OptionsFound:
        later = Candidate(
            id="cand_sister_2100",
            rationale="Sister watches after me",
            edits=[UpdateCommitment(commitment_id=commitment.id, start="21:00", end="22:00")],
        )
        return OptionsFound(source=self.name, live=False, choices=[Choice(label="A", summary="A | 21:00-22:00", candidate=later)])


def test_same_tools_work_for_a_non_travel_situation(tmp_path):
    session = Session(tmp_path, sources=[TravelSource(RecordedProvider()), SisterWatchesLater()])
    assert session.call("get_situation", situation_id="tv_evening")["feasible"] is False
    found = session.call("find_options", situation_id="tv_evening", commitment_id="c_sister_prime")
    assert found["source"] == "household-tv"
    assert session.call("try_option", situation_id="tv_evening", label="A")["feasible"] is True
    assert session.call("confirm_option", situation_id="tv_evening", label="A")["verified"] is True


def test_no_source_for_a_commitment_is_a_clear_error(session):
    with pytest.raises(ToolFailed, match="No option source"):
        session.call("find_options", situation_id="tv_evening", commitment_id="c_sister_prime")


# --- over real Streamable HTTP, inside the FastAPI app ----------------------


def test_http_mcp_speaks_protocol_2025_11_25_and_keeps_old_endpoints():
    from app.main import app

    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

    def rpc(client, body, session_id=None):
        extra = {"mcp-session-id": session_id} if session_id else {}
        response = client.post("/mcp", content=json.dumps(body), headers={**headers, **extra})
        assert response.status_code in (200, 202), response.text
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return response, (json.loads(data[0]) if data else None)

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/situations").status_code == 200  # existing endpoint untouched

        response, init = rpc(client, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
        })
        assert init["result"]["protocolVersion"] == "2025-11-25"
        assert init["result"]["serverInfo"]["name"] == "situation-guard"
        session_id = response.headers["mcp-session-id"]

        rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
        _, listed = rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session_id)
        assert len(listed["result"]["tools"]) == 6
