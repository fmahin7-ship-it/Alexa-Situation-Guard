"""Web demo API: page, config, chat over MCP, proof panel, reset. Scripted brain, recorded travel, temp state."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from app.agent.scripted import ScriptedProvider, guided_policy
from app.main import app
from app.mcp_server import build_server
from app.store import SituationStore
from app.travel.recorded import RecordedProvider
from app.travel.source import TravelSource

TRIP = "travel_friday"
DELAY = "It's 3:05pm on Friday 2 October and my train is running 45 minutes late."


@pytest.fixture
def client(tmp_path):
    store = SituationStore(tmp_path)
    server = build_server(store, [TravelSource(RecordedProvider())])
    app.state.store = store
    app.state.tool_connector = lambda: Client(server)  # in-process instead of HTTP to :8000
    app.state.llm_factory = lambda: ScriptedProvider(guided_policy())
    # No `with`: the app's lifespan (the HTTP /mcp session manager) can only start once per
    # process and is not needed here, because tools are reached in-process.
    yield TestClient(app)
    for name in ("store", "tool_connector", "llm_factory"):
        delattr(app.state, name)


def test_page_and_its_files_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "<title>Situation Guard</title>" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_config_shows_brain_travel_and_situations(client, monkeypatch):
    monkeypatch.delenv("SITUATION_GUARD_TRAVEL", raising=False)
    config = client.get("/api/config").json()
    assert config["brain"] == "scripted" and config["brain_live"] is False
    assert config["travel"] == "recorded"
    assert {"id": TRIP, "name": "Friday airport trip"} in config["situations"]


def test_two_turn_conversation_through_the_mcp_tools(client):
    first = client.post("/api/chat", json={"text": DELAY}).json()
    assert [s["tool"] for s in first["steps"]] == [
        "report_change", "find_options", "try_option", "try_option", "try_option", "try_option",
    ]
    assert "option D works" in first["reply"]
    assert first["brain"] == "scripted"

    second = client.post("/api/chat", json={"text": "Yes, do it.", "session_id": first["session_id"]}).json()
    assert second["session_id"] == first["session_id"]
    assert [s["tool"] for s in second["steps"]] == ["confirm_option"]
    assert second["steps"][0]["result"]["verified"] is True

    panel = client.get(f"/api/situations/{TRIP}").json()
    assert panel["view"]["feasible"] is True
    statuses = {c["id"]: c["status"] for c in panel["view"]["commitments"]}
    assert statuses["c_train"] == "cancelled" and statuses["c_travel_D"] == "planned"
    assert [e["kind"] for e in panel["log"]][-1] == "option_confirmed"


def test_a_new_session_does_not_inherit_another_conversation(client):
    client.post("/api/chat", json={"text": DELAY})
    # No session_id: a fresh conversation, so "yes" has nothing approved to confirm yet.
    fresh = client.post("/api/chat", json={"text": "Yes, do it."}).json()
    assert "confirm_option" not in [s["tool"] for s in fresh["steps"]]


def test_reset_restores_the_starting_plan(client):
    first = client.post("/api/chat", json={"text": DELAY}).json()
    assert client.get(f"/api/situations/{TRIP}").json()["view"]["feasible"] is False
    assert client.post("/api/reset", json={"situation_id": TRIP, "session_id": first["session_id"]}).status_code == 200
    view = client.get(f"/api/situations/{TRIP}").json()["view"]
    assert view["feasible"] is True and view["now"] is None


def test_unknown_situation_is_404(client):
    assert client.get("/api/situations/nope").status_code == 404
    assert client.post("/api/reset", json={"situation_id": "nope"}).status_code == 404


def test_empty_message_is_rejected(client):
    assert client.post("/api/chat", json={"text": ""}).status_code == 422


def test_unreachable_mcp_server_is_a_clear_error(client):
    @asynccontextmanager
    async def broken():
        raise ConnectionError("connection refused")
        yield  # pragma: no cover

    app.state.tool_connector = broken
    response = client.post("/api/chat", json={"text": DELAY})
    assert response.status_code == 502
    assert "Could not reach the MCP server" in response.json()["detail"]
