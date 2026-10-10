"""Agent layer: scripted provider, Bedrock request/response shape (stubbed), and the bounded loop.

No live calls: Bedrock is a botocore Stubber, tools are an in-process MCP server with recorded travel data.
"""

from __future__ import annotations

import asyncio
import json
from typing import List

import boto3
import pytest
from botocore.stub import Stubber
from mcp import Client

from app.agent.bedrock import BedrockConverseProvider, _simplify_schema, converse_request
from app.agent.llm import LLM_ENV, LLMError, LLMReply, Message, ToolCall, ToolResult, ToolSpec, Usage, make_llm_provider
from app.agent.loop import McpToolRunner, run_turn
from app.agent.scripted import ScriptedProvider, guided_policy, load_scenario
from app.mcp_server import build_server
from app.store import SituationStore
from app.travel.recorded import RecordedProvider
from app.travel.source import TravelSource

TRIP = "travel_friday"


def _conversation(tmp_path, provider, utterances: List[str]):
    """Run several user turns against a fresh in-process MCP server; return each TurnResult and the store."""
    store = SituationStore(tmp_path)
    server = build_server(store, [TravelSource(RecordedProvider())])

    async def go():
        async with Client(server) as client:
            runner = McpToolRunner(client)
            history: List[Message] = []
            turns = []
            for text in utterances:
                turn = await run_turn(provider, runner, history, text)
                history = turn.messages
                turns.append(turn)
            return turns

    return asyncio.run(go()), store


# --- the scripted stand-in, end to end ---------------------------------------


def test_scripted_alexa_reports_tries_and_recommends_without_changing_the_plan(tmp_path):
    (turn,), store = _conversation(
        tmp_path, ScriptedProvider(guided_policy()), ["My train is running 45 minutes late."]
    )
    assert [s.tool for s in turn.steps] == [
        "report_change", "find_options", "try_option", "try_option", "try_option", "try_option",
    ]
    assert [s.arguments.get("label") for s in turn.steps if s.tool == "try_option"] == ["A", "B", "C", "D"]
    assert [s.result["feasible"] for s in turn.steps if s.tool == "try_option"] == [False, False, False, True]
    assert "option D works" in turn.reply and "Shall I switch" in turn.reply

    statuses = {c.id: c.status for c in store.load(TRIP).situation.commitments}
    assert statuses["c_train"] == "planned"  # nothing confirmed yet


def test_scripted_alexa_confirms_only_after_the_user_agrees(tmp_path):
    (first, second), store = _conversation(
        tmp_path, ScriptedProvider(guided_policy()), ["My train is running 45 minutes late.", "Yes, do it."]
    )
    assert [s.tool for s in second.steps] == ["confirm_option"]
    assert second.steps[0].arguments == {"situation_id": TRIP, "label": "D"}
    assert second.steps[0].result["verified"] is True
    assert "checked it again" in second.reply
    statuses = {c.id: c.status for c in store.load(TRIP).situation.commitments}
    assert statuses["c_train"] == "cancelled" and statuses["c_travel_D"] == "planned"


def test_status_question_after_a_change_only_reads_and_never_reports_the_change_again(tmp_path):
    (first, second), store = _conversation(
        tmp_path,
        ScriptedProvider(guided_policy()),
        ["My train is running 45 minutes late.", "What is my current plan?"],
    )
    assert [s.tool for s in second.steps] == ["get_situation"]
    assert "Dad boards the 18:00 flight" in second.reply

    train = next(c for c in store.load(TRIP).situation.commitments if c.id == "c_train")
    assert (train.start, train.end) == ("2026-10-02T15:45:00+10:00", "2026-10-02T16:30:00+10:00")  # 45 min, not 90
    assert [e.kind for e in store.load(TRIP).log].count("change_reported") == 1


def test_scripted_alexa_reports_honestly_when_nothing_works(tmp_path):
    scenario = load_scenario().model_copy(update={"search_after": "2026-10-02T15:00:00+10:00"})
    late = scenario.model_copy(update={"change": scenario.change.model_copy(update={"observed_at": "2026-10-02T15:40:00+10:00"})})
    (turn,), _ = _conversation(tmp_path, ScriptedProvider(guided_policy(late)), ["Train delayed again."])
    assert [s.result.get("feasible") for s in turn.steps if s.tool == "try_option"] == [False, False, False, False]
    assert "none of them works" in turn.reply


def test_tool_errors_are_reported_not_crashed(tmp_path):
    scenario = load_scenario()
    no_recording = scenario.model_copy(update={"search_after": None})  # searches from 15:05: not recorded
    (turn,), _ = _conversation(tmp_path, ScriptedProvider(guided_policy(no_recording)), ["Train is late."])
    assert turn.steps[-1].tool == "find_options" and turn.steps[-1].is_error
    assert turn.reply.startswith("Sorry, I couldn't do that") and "No recording" in turn.reply


# --- the loop's guards -------------------------------------------------------


class _AlwaysCallsTools:
    name, live = "loopy", False

    def reply(self, system, messages, tools):
        return LLMReply(tool_calls=[ToolCall(id=f"c{len(messages)}", name="list_situations", arguments={})])


class _UnknownTool:
    name, live = "confused", False

    def reply(self, system, messages, tools):
        if messages[-1].tool_results:
            return LLMReply(text=f"got: {messages[-1].tool_results[0].content}")
        return LLMReply(tool_calls=[ToolCall(id="x", name="book_helicopter", arguments={})])


class _Unreachable:
    name, live = "down", True

    def reply(self, system, messages, tools):
        raise LLMError("Operation not allowed")


def test_loop_stops_after_max_steps(tmp_path):
    (turn,), _ = _conversation(tmp_path, _AlwaysCallsTools(), ["hi"])
    assert turn.stopped_early and len(turn.steps) == 10
    assert "too many steps" in turn.reply


def test_unknown_tool_is_fed_back_as_an_error(tmp_path):
    (turn,), _ = _conversation(tmp_path, _UnknownTool(), ["hi"])
    assert turn.steps[0].is_error and "Unknown tool" in turn.reply


def test_unreachable_model_gives_a_spoken_apology(tmp_path):
    (turn,), _ = _conversation(tmp_path, _Unreachable(), ["hi"])
    assert turn.stopped_early and turn.steps == []
    assert "can't reach my language model" in turn.reply


# --- Bedrock Converse shape (stubbed, no network) -----------------------------


def _tool_specs() -> List[ToolSpec]:
    async def go():
        async with Client(build_server(SituationStore("unused"))) as client:
            return await McpToolRunner(client).list_tools()

    return asyncio.run(go())


def test_schema_is_simplified_for_models_that_dislike_null_unions():
    report = next(s for s in _tool_specs() if s.name == "report_change")
    simple = _simplify_schema(report.input_schema)
    assert simple["properties"]["minutes"] == {"type": "integer"}
    assert simple["properties"]["kind"] == {"enum": ["delay", "cancelled"], "type": "string"}
    assert "title" not in json.dumps(simple)
    assert simple["required"] == ["situation_id", "commitment_id", "kind", "observed_at"]


def test_converse_request_shape_with_tools_and_a_tool_round_trip():
    messages = [
        Message(role="user", text="My train is late"),
        Message(role="assistant", tool_calls=[ToolCall(id="t1", name="get_situation", arguments={"situation_id": TRIP})]),
        Message(role="user", tool_results=[ToolResult(call_id="t1", name="get_situation", content={"feasible": True})]),
    ]
    request = converse_request("global.amazon.nova-2-lite-v1:0", "be brief", messages, _tool_specs(), 256)
    assert request["modelId"] == "global.amazon.nova-2-lite-v1:0"
    assert request["system"] == [{"text": "be brief"}]
    assert request["inferenceConfig"] == {"maxTokens": 256}
    assert [t["toolSpec"]["name"] for t in request["toolConfig"]["tools"]][:2] == ["list_situations", "get_situation"]
    assert request["messages"][1]["content"] == [{"toolUse": {"toolUseId": "t1", "name": "get_situation", "input": {"situation_id": TRIP}}}]
    assert request["messages"][2]["content"] == [
        {"toolResult": {"toolUseId": "t1", "content": [{"json": {"feasible": True}}], "status": "success"}}
    ]


def _stubbed_bedrock():
    client = boto3.client(
        "bedrock-runtime", region_name="ap-southeast-2", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    return BedrockConverseProvider(client=client), Stubber(client)


def test_bedrock_provider_parses_a_tool_call_and_usage():
    provider, stub = _stubbed_bedrock()
    messages = [Message(role="user", text="My train is late")]
    reply = {
        "output": {"message": {"role": "assistant", "content": [
            {"text": "Let me check."},
            {"toolUse": {"toolUseId": "tu-1", "name": "get_situation", "input": {"situation_id": TRIP}}},
        ]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 1200, "outputTokens": 40, "totalTokens": 1240},
        "metrics": {"latencyMs": 100},
    }
    stub.add_response("converse", reply, expected_params=converse_request(provider.model_id, "sys", messages, [], 512))
    with stub:
        parsed = provider.reply("sys", messages, [])
    assert parsed.text == "Let me check."
    assert parsed.tool_calls == [ToolCall(id="tu-1", name="get_situation", arguments={"situation_id": TRIP})]
    assert parsed.usage == Usage(input_tokens=1200, output_tokens=40)


def test_bedrock_errors_become_llm_errors():
    provider, stub = _stubbed_bedrock()
    stub.add_client_error("converse", "ValidationException", "Operation not allowed")
    with stub, pytest.raises(LLMError, match="Operation not allowed"):
        provider.reply("sys", [Message(role="user", text="hi")], [])


def test_bedrock_defaults_to_nova_2_lite_with_the_limited_profile():
    provider = BedrockConverseProvider()
    assert provider.model_id == "global.amazon.nova-2-lite-v1:0"
    assert provider._profile == "situation-guard" and provider._region == "ap-southeast-2"


def test_real_client_makes_only_one_attempt():
    provider = BedrockConverseProvider(profile="situation-guard")
    try:
        client = provider._get_client()
    except LLMError:
        pytest.skip("situation-guard profile not configured on this machine")
    assert client.meta.config.retries["total_max_attempts"] == 1


# --- choosing a provider -----------------------------------------------------


def test_default_llm_is_scripted_so_nothing_costs_money(monkeypatch):
    monkeypatch.delenv(LLM_ENV, raising=False)
    assert make_llm_provider(use_dotenv=False).live is False


def test_bedrock_only_when_asked(monkeypatch):
    monkeypatch.setenv(LLM_ENV, "bedrock")
    assert isinstance(make_llm_provider(use_dotenv=False), BedrockConverseProvider)  # created, not called


def test_unknown_llm_setting_is_an_error(monkeypatch):
    monkeypatch.setenv(LLM_ENV, "gemini")
    with pytest.raises(LLMError, match="'scripted', 'bedrock' or 'openai'"):
        make_llm_provider(use_dotenv=False)
