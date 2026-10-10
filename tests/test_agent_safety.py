"""Consent safeguard, recorded fallback limits, and the OpenAI reasoning setting. No network."""

from __future__ import annotations

import asyncio

import pytest
from mcp import Client

from app.agent.llm import LLMReply, Message, ToolCall
from app.agent.loop import McpToolRunner, is_approval, run_turn
from app.agent.openai_provider import OpenAIChatProvider, chat_request
from app.mcp_server import build_server
from app.store import SituationStore
from app.travel.recorded import RecordedProvider
from app.travel.search import TravelRequest, TravelSearchError, travel_search
from app.travel.source import TravelSource

TRIP = "travel_friday"
CENTRAL, AIRPORT = [151.2063, -33.883], [151.1664, -33.9361]


# --- what counts as approval -------------------------------------------------


@pytest.mark.parametrize("text", ["Yes, do it.", "yes", "OK", "Sure, switch to D", "go ahead please"])
def test_clear_yes_is_approval(text):
    assert is_approval(text)


@pytest.mark.parametrize(
    "text",
    ["No, don't.", "wait", "yes but wait", "Not yet", "What are my options?", "My train is late", "", None],
)
def test_anything_else_is_not_approval(text):
    assert not is_approval(text)


# --- the safeguard, in code -------------------------------------------------


class EagerModel:
    """A badly-behaved model: tries to switch the plan immediately, whatever the user said."""

    name, live = "eager", False

    def reply(self, system, messages, tools):
        last = messages[-1]
        if last.tool_results:
            return LLMReply(text=str(last.tool_results[-1].content)[:200])
        return LLMReply(tool_calls=[ToolCall(id="c", name="confirm_option", arguments={"situation_id": TRIP, "label": "D"})])


def _prepared_session(tmp_path):
    """A server where the train is late and options A-D are on offer, so D could be confirmed."""
    store = SituationStore(tmp_path)
    server = build_server(store, [TravelSource(RecordedProvider())])

    async def prepare():
        async with Client(server) as client:
            await client.call_tool("report_change", {
                "situation_id": TRIP, "commitment_id": "c_train", "kind": "delay",
                "minutes": 45, "observed_at": "2026-10-02T15:05:00+10:00",
            })
            await client.call_tool("find_options", {"situation_id": TRIP, "commitment_id": "c_train"})

    asyncio.run(prepare())
    return store, server


def _turn(server, text):
    async def go():
        async with Client(server) as client:
            return await run_turn(EagerModel(), McpToolRunner(client), [], text)

    return asyncio.run(go())


def test_model_cannot_change_the_plan_without_the_users_yes(tmp_path):
    store, server = _prepared_session(tmp_path)
    turn = _turn(server, "What are my options?")
    assert turn.steps[0].tool == "confirm_option" and turn.steps[0].is_error
    assert "has not approved" in turn.steps[0].result["error"]
    statuses = {c.id: c.status for c in store.load(TRIP).situation.commitments}
    assert statuses["c_train"] == "planned" and "c_travel_D" not in statuses


def test_a_no_is_never_taken_as_yes(tmp_path):
    store, server = _prepared_session(tmp_path)
    assert _turn(server, "No, don't switch yet").steps[0].is_error
    assert "c_travel_D" not in {c.id for c in store.load(TRIP).situation.commitments}


def test_after_a_clear_yes_the_change_goes_through(tmp_path):
    store, server = _prepared_session(tmp_path)
    turn = _turn(server, "Yes, do it.")
    assert not turn.steps[0].is_error and turn.steps[0].result["verified"] is True
    assert "c_travel_D" in {c.id for c in store.load(TRIP).situation.commitments}


# --- recorded fallback limits ------------------------------------------------


def _request(depart_at=None, arrive_by=None, alternatives=2):
    return TravelRequest(origin=CENTRAL, destination=AIRPORT, mode="Transit",
                         depart_at=depart_at, arrive_by=arrive_by, max_alternatives=alternatives)


def test_exact_recording_has_no_fallback_note():
    result = travel_search(_request(depart_at="2026-10-02T15:00:00+10:00"), RecordedProvider())
    assert result.notes == []


def test_up_to_15_minutes_later_replays_the_earlier_recording_with_a_note():
    result = travel_search(_request(depart_at="2026-10-02T15:15:00+10:00"), RecordedProvider())
    assert len(result.options) == 3
    assert "closest earlier recorded search" in result.notes[0]


def test_more_than_15_minutes_later_is_refused():
    with pytest.raises(TravelSearchError, match="No recording"):
        travel_search(_request(depart_at="2026-10-02T15:16:00+10:00"), RecordedProvider())


def test_earlier_than_any_recording_is_refused():
    with pytest.raises(TravelSearchError, match="No recording"):
        travel_search(_request(depart_at="2026-10-02T14:55:00+10:00"), RecordedProvider())


def test_arrive_by_never_falls_back():
    with pytest.raises(TravelSearchError, match="No recording"):
        travel_search(_request(arrive_by="2026-10-02T16:05:00+10:00", alternatives=0), RecordedProvider())


# --- OpenAI reasoning setting ------------------------------------------------


def test_reasoning_defaults_to_low_and_is_sent(monkeypatch):
    monkeypatch.delenv("SITUATION_GUARD_OPENAI_REASONING", raising=False)
    provider = OpenAIChatProvider()
    assert provider.reasoning_effort == "low"
    request = chat_request("gpt-5-mini", "s", [Message(role="user", text="hi")], [], 100, provider.reasoning_effort)
    assert request["reasoning_effort"] == "low"


def test_reasoning_none_omits_the_setting(monkeypatch):
    monkeypatch.setenv("SITUATION_GUARD_OPENAI_REASONING", "none")
    provider = OpenAIChatProvider()
    assert provider.reasoning_effort is None
    assert "reasoning_effort" not in chat_request("m", "s", [Message(role="user", text="hi")], [], 100, None)
