"""OpenAI provider — request/response shape with a fake client. No network, no key, no cost."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import openai
import pytest
from mcp import Client
from openai.types.chat import ChatCompletion

from app.agent.llm import LLM_ENV, LLMError, Message, ToolCall, ToolResult, Usage, make_llm_provider
from app.agent.loop import McpToolRunner, run_turn
from app.agent.openai_provider import OpenAIChatProvider, chat_request
from app.mcp_server import build_server
from app.store import SituationStore
from app.travel.recorded import RecordedProvider
from app.travel.source import TravelSource

TRIP = "travel_friday"


def _completion(content=None, tool_calls=None, prompt_tokens=100, completion_tokens=20) -> ChatCompletion:
    """A response built with OpenAI's own types, so the parser sees the real shape."""
    return ChatCompletion.model_validate({
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5-mini",
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls" if tool_calls else "stop",
            "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
        }],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens},
    })


def _tool_call(call_id: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class FakeOpenAI:
    """Stands in for openai.OpenAI(): records each request and replies from a queue."""

    def __init__(self, replies: List[Any]):
        self.requests: List[Dict[str, Any]] = []
        self._replies = list(replies)
        self.chat = self
        self.completions = self

    def create(self, **request):
        self.requests.append(request)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


# --- request shape -----------------------------------------------------------


def test_request_carries_system_tools_and_a_full_tool_round_trip():
    messages = [
        Message(role="user", text="My train is late"),
        Message(role="assistant", tool_calls=[ToolCall(id="call_1", name="get_situation", arguments={"situation_id": TRIP})]),
        Message(role="user", tool_results=[ToolResult(call_id="call_1", name="get_situation", content={"feasible": True})]),
    ]
    from app.agent.llm import ToolSpec

    tools = [ToolSpec(name="get_situation", description="Current plan", input_schema={"type": "object", "properties": {}})]
    request = chat_request("gpt-5-mini", "be Alexa", messages, tools, 4000)

    assert request["model"] == "gpt-5-mini"
    assert request["max_completion_tokens"] == 4000
    assert request["messages"] == [
        {"role": "system", "content": "be Alexa"},
        {"role": "user", "content": "My train is late"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "get_situation", "arguments": json.dumps({"situation_id": TRIP})}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"feasible": True})},
    ]
    assert request["tools"] == [{
        "type": "function",
        "function": {"name": "get_situation", "description": "Current plan", "parameters": {"type": "object", "properties": {}}},
    }]


# --- response parsing --------------------------------------------------------


def test_tool_call_arguments_and_usage_are_parsed():
    fake = FakeOpenAI([_completion(tool_calls=[_tool_call("call_9", "report_change", {"minutes": 45})])])
    reply = OpenAIChatProvider(client=fake).reply("sys", [Message(role="user", text="late")], [])
    assert reply.tool_calls == [ToolCall(id="call_9", name="report_change", arguments={"minutes": 45})]
    assert reply.text is None and reply.usage == Usage(input_tokens=100, output_tokens=20)


def test_plain_answer_is_parsed():
    fake = FakeOpenAI([_completion(content="Option D works. Shall I switch?")])
    assert OpenAIChatProvider(client=fake).reply("sys", [Message(role="user", text="hi")], []).text.startswith("Option D")


def test_malformed_tool_arguments_are_an_llm_error():
    bad = {"id": "c", "type": "function", "function": {"name": "try_option", "arguments": "{not json"}}
    fake = FakeOpenAI([_completion(tool_calls=[bad])])
    with pytest.raises(LLMError, match="malformed arguments"):
        OpenAIChatProvider(client=fake).reply("sys", [Message(role="user", text="hi")], [])


def test_api_errors_become_llm_errors():
    fake = FakeOpenAI([openai.APIConnectionError(request=None)])
    with pytest.raises(LLMError, match="APIConnectionError"):
        OpenAIChatProvider(client=fake).reply("sys", [Message(role="user", text="hi")], [])


def test_missing_key_is_a_clear_error_and_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="OPENAI_API_KEY is not set"):
        OpenAIChatProvider().reply("sys", [Message(role="user", text="hi")], [])


def test_real_client_is_built_without_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    client = OpenAIChatProvider()._get_client()  # constructing the client makes no request
    assert client.max_retries == 0


def test_model_is_a_setting(monkeypatch):
    monkeypatch.delenv("SITUATION_GUARD_OPENAI_MODEL", raising=False)
    assert OpenAIChatProvider().model == "gpt-5-mini"
    monkeypatch.setenv("SITUATION_GUARD_OPENAI_MODEL", "gpt-5.6-terra")
    assert OpenAIChatProvider().name == "openai:gpt-5.6-terra"


def test_openai_only_when_asked(monkeypatch):
    monkeypatch.setenv(LLM_ENV, "openai")
    assert isinstance(make_llm_provider(use_dotenv=False), OpenAIChatProvider)  # created, not called


# --- through the real loop and MCP tools, with a fake model -------------------


def test_fake_openai_model_drives_the_real_tools_end_to_end(tmp_path):
    """The model asks for tools; the loop runs them on the real MCP server; the model then answers."""
    fake = FakeOpenAI([
        _completion(tool_calls=[_tool_call("c1", "report_change", {
            "situation_id": TRIP, "commitment_id": "c_train", "kind": "delay",
            "minutes": 45, "observed_at": "2026-10-02T15:05:00+10:00",
        })]),
        _completion(tool_calls=[_tool_call("c2", "find_options", {
            "situation_id": TRIP, "commitment_id": "c_train", "after": "2026-10-02T15:00:00+10:00",
        })]),
        _completion(tool_calls=[_tool_call("c3", "try_option", {"situation_id": TRIP, "label": "D"})]),
        _completion(content="Your train is late, but the 15:10 T8 still gets you there by 15:32. Shall I switch?"),
    ])
    server = build_server(SituationStore(tmp_path), [TravelSource(RecordedProvider())])

    async def go():
        async with Client(server) as client:
            return await run_turn(OpenAIChatProvider(client=fake), McpToolRunner(client), [], "My train is 45 minutes late")

    turn = asyncio.run(go())
    assert [s.tool for s in turn.steps] == ["report_change", "find_options", "try_option"]
    assert turn.steps[0].result["goals_at_risk"] == ["goal_flight"]
    assert turn.steps[2].result["feasible"] is True  # the engine's verdict, not the model's
    assert turn.reply.startswith("Your train is late")
    assert turn.usage == Usage(input_tokens=400, output_tokens=80)

    # What the model saw in its last request: every tool result, as OpenAI "tool" messages.
    last = fake.requests[-1]["messages"]
    assert [m["role"] for m in last] == ["system", "user", "assistant", "tool", "assistant", "tool", "assistant", "tool"]
    assert json.loads(last[-1]["content"])["feasible"] is True
    assert len(fake.requests[0]["tools"]) == 6
