"""The agent loop: user says something -> LLM -> tool calls -> MCP server -> ... -> reply.

Bounded: at most `max_steps` LLM calls per user turn, so a confused model can
never call tools forever. Tool errors go back to the LLM as error results.

Consent is enforced in code, not only asked for in the prompt: a tool in
NEEDS_APPROVAL runs only if the user's message in this turn is an approval.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.agent.consent import NEEDS_APPROVAL, is_approval
from app.agent.llm import LLMError, LLMProvider, Message, ToolCall, ToolResult, ToolSpec, Usage
from app.agent.prompt import SYSTEM_PROMPT
from app.agent.tool_runner import ToolRunner

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
