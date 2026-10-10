"""The agent loop: user says something -> LLM -> tool calls -> MCP server -> ... -> reply.

Bounded: at most `max_steps` LLM calls per user turn, so a confused model can
never call tools forever. Tool errors go back to the LLM as error results.

Consent is enforced in code, not only asked for in the prompt: a tool in
NEEDS_APPROVAL runs only if the user's message in this turn is an approval.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Protocol, Tuple

from pydantic import BaseModel, Field

from .llm import LLMError, LLMProvider, Message, ToolCall, ToolResult, ToolSpec, Usage

SYSTEM_PROMPT = (
    "You are Alexa, keeping the user's plans working when reality changes. You can ONLY do what the "
    "Situation Guard tools do: read a situation, record a change (delay or cancellation), find alternatives, "
    "check an alternative with try_option, and switch to one with confirm_option. Never offer anything else "
    "(no booking taxis, contacting airlines, or monitoring).\n"
    "When something changes: find the situation (list_situations), call report_change with times in the "
    "situation's format and offset, then find_options for the broken commitment, then try_option on the options "
    "in order until one works. Only try_option decides whether an option works: never claim an option works "
    "unless it returned feasible true. If find_options fails, say plainly that no alternatives were found.\n"
    "Before changing the plan, end with ONE clear yes/no question naming the option. Call confirm_option only "
    "after the user says yes.\n"
    "Speak like Alexa: two or three short sentences, no lists, no markdown."
)

# Tools that change the user's plan: allowed only right after the user approves.
NEEDS_APPROVAL = {"confirm_option"}

_APPROVAL = re.compile(
    r"\b(yes|yeah|yep|sure|confirm|confirmed|do it|go ahead|switch|book it|sounds good|please do|ok(ay)?)\b", re.I
)
_REFUSAL = re.compile(r"\b(no|don'?t|do not|stop|wait|cancel|not yet)\b", re.I)


def is_approval(text: Optional[str]) -> bool:
    """A clear yes, and no 'no' / 'don't' / 'wait' in the same message."""
    return bool(text) and bool(_APPROVAL.search(text)) and not _REFUSAL.search(text)


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
            if call.name in NEEDS_APPROVAL and not is_approval(user_text):
                is_error, content = True, {
                    "error": f"Blocked: {call.name} changes the plan and the user has not approved it. "
                    "Ask a clear yes/no question first."
                }
            else:
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
