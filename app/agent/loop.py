"""The agent loop: user says something -> LLM -> tool calls -> MCP server -> ... -> reply.

Bounded: at most `max_steps` LLM calls per user turn, so a confused model can
never call tools forever. Tool errors go back to the LLM as error results.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Protocol, Tuple

from pydantic import BaseModel, Field

from .llm import LLMError, LLMProvider, Message, ToolCall, ToolResult, ToolSpec, Usage

SYSTEM_PROMPT = (
    "You are Alexa, helping keep the user's plans working when reality changes. "
    "Use the Situation Guard tools. When something changes, call report_change, then find_options for the "
    "broken commitment, then try_option. Only the tools decide whether an option works: never claim an option "
    "works unless try_option returned feasible true. Never call confirm_option until the user agrees. "
    "Reply briefly, in plain spoken language, as Alexa would."
)


class ToolRunner(Protocol):
    async def list_tools(self) -> List[ToolSpec]:
        ...

    async def call(self, name: str, arguments: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """(is_error, content)"""
        ...


class McpToolRunner:
    """Tools from an MCP server: a URL (Streamable HTTP) or an in-process server for tests."""

    def __init__(self, client: Any):
        self._client = client  # an open mcp.Client

    async def list_tools(self) -> List[ToolSpec]:
        listed = await self._client.list_tools()
        return [ToolSpec(name=t.name, description=t.description or "", input_schema=t.input_schema) for t in listed.tools]

    async def call(self, name: str, arguments: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        result = await self._client.call_tool(name, arguments)
        if result.is_error:
            text = result.content[0].text if result.content else "tool failed"
            return True, {"error": text}
        return False, result.structured_content or {}


class Step(BaseModel):
    """One tool call and what came back — what the proof panel shows."""

    tool: str
    arguments: Dict[str, Any]
    is_error: bool
    result: Dict[str, Any]


class TurnResult(BaseModel):
    reply: str
    steps: List[Step] = Field(default_factory=list)
    messages: List[Message] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    stopped_early: bool = False


async def run_turn(
    provider: LLMProvider,
    tools: ToolRunner,
    history: List[Message],
    user_text: str,
    system: str = SYSTEM_PROMPT,
    max_steps: int = 10,
    tool_specs: Optional[List[ToolSpec]] = None,
) -> TurnResult:
    """Run one user turn to a final spoken reply. `history` is not modified."""
    specs = tool_specs if tool_specs is not None else await tools.list_tools()
    known = {s.name for s in specs}
    messages = list(history) + [Message(role="user", text=user_text)]
    steps: List[Step] = []
    usage = Usage()

    for _ in range(max_steps):
        try:
            reply = await asyncio.to_thread(provider.reply, system, messages, specs)
        except LLMError as exc:
            text = f"Sorry, I can't reach my language model right now. ({exc})"
            return TurnResult(reply=text, steps=steps, messages=messages, usage=usage, stopped_early=True)
        usage.input_tokens += reply.usage.input_tokens
        usage.output_tokens += reply.usage.output_tokens
        messages.append(Message(role="assistant", text=reply.text, tool_calls=reply.tool_calls))

        if not reply.tool_calls:
            return TurnResult(reply=reply.text or "", steps=steps, messages=messages, usage=usage)

        results: List[ToolResult] = []
        for call in reply.tool_calls:
            is_error, content = await _run_call(tools, known, call)
            steps.append(Step(tool=call.name, arguments=call.arguments, is_error=is_error, result=content))
            results.append(ToolResult(call_id=call.id, name=call.name, is_error=is_error, content=content))
        messages.append(Message(role="user", tool_results=results))

    text = "Sorry, that took too many steps, so I stopped before finishing."
    return TurnResult(reply=text, steps=steps, messages=messages, usage=usage, stopped_early=True)


async def _run_call(tools: ToolRunner, known: set, call: ToolCall) -> Tuple[bool, Dict[str, Any]]:
    if call.name not in known:
        return True, {"error": f"Unknown tool '{call.name}'"}
    try:
        return await tools.call(call.name, call.arguments)
    except Exception as exc:  # a tool transport failure must not crash the conversation
        return True, {"error": f"{type(exc).__name__}: {exc}"}
