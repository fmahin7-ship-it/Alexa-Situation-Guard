"""OpenAI Chat Completions provider (default model: gpt-5-mini).

The only module that knows the OpenAI request/response shape. The API key is
read from OPENAI_API_KEY (environment or a gitignored .env file), never from
code. One attempt per call (max_retries=0), so a failure is reported instead
of silently re-billed.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .llm import LLMError, LLMReply, Message, ToolCall, ToolSpec, Usage

MODEL_ENV = "SITUATION_GUARD_OPENAI_MODEL"
DEFAULT_MODEL = "gpt-5-mini"  # some newer models (e.g. GPT-6 Astra, GPT-6.1 Sol) do not support tools on Chat Completions
KEY_ENV = "OPENAI_API_KEY"
REASONING_ENV = "SITUATION_GUARD_OPENAI_REASONING"
DEFAULT_REASONING = "low"  # supported values are model-dependent; "none" = do not send the setting

# Reasoning models spend part of this budget thinking before they answer;
# too small a cap returns an empty reply.
DEFAULT_MAX_COMPLETION_TOKENS = 4000


def _to_openai_messages(system: str, messages: List[Message]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        if m.role == "assistant":
            entry: Dict[str, Any] = {"role": "assistant", "content": m.text}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in m.tool_calls
                ]
            out.append(entry)
            continue
        if m.text is not None:
            out.append({"role": "user", "content": m.text})
        for result in m.tool_results:
            out.append({"role": "tool", "tool_call_id": result.call_id, "content": json.dumps(result.content)})
    return out


def chat_request(
    model: str,
    system: str,
    messages: List[Message],
    tools: List[ToolSpec],
    max_completion_tokens: int,
    reasoning_effort: Optional[str] = None,
) -> Dict[str, Any]:
    """The exact Chat Completions parameters for one turn."""
    request: Dict[str, Any] = {
        "model": model,
        "messages": _to_openai_messages(system, messages),
        "max_completion_tokens": max_completion_tokens,
    }
    if reasoning_effort:
        request["reasoning_effort"] = reasoning_effort
    if tools:
        request["tools"] = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description or t.name, "parameters": t.input_schema},
            }
            for t in tools
        ]
    return request


def parse_chat_response(response: Any) -> LLMReply:
    if not response.choices:
        raise LLMError("OpenAI returned no choices")
    message = response.choices[0].message
    calls: List[ToolCall] = []
    for call in message.tool_calls or []:
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model sent malformed arguments for '{call.function.name}'") from exc
        calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
    usage = response.usage
    return LLMReply(
        text=message.content or None,
        tool_calls=calls,
        usage=Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        ),
    )


class OpenAIChatProvider:
    live = True

    def __init__(
        self,
        client: Any = None,
        model: Optional[str] = None,
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
        reasoning_effort: Optional[str] = None,
    ):
        self.model = model or os.environ.get(MODEL_ENV, DEFAULT_MODEL)
        effort = (reasoning_effort or os.environ.get(REASONING_ENV, DEFAULT_REASONING)).strip().lower()
        self.reasoning_effort = None if effort == "none" else effort
        self.name = f"openai:{self.model}"
        self._client = client
        self._max_completion_tokens = max_completion_tokens

    def _get_client(self) -> Any:
        if self._client is None:
            if not os.environ.get(KEY_ENV):
                raise LLMError(f"{KEY_ENV} is not set. Put it in .env (see .env.example), never in code.")
            from openai import OpenAI

            self._client = OpenAI(max_retries=0, timeout=60)
        return self._client

    def reply(self, system: str, messages: List[Message], tools: List[ToolSpec]) -> LLMReply:
        import openai

        request = chat_request(
            self.model, system, messages, tools, self._max_completion_tokens, self.reasoning_effort
        )
        try:
            response = self._get_client().chat.completions.create(**request)
        except openai.OpenAIError as exc:
            raise LLMError(f"OpenAI could not answer ({self.model}): {type(exc).__name__}: {exc}") from exc
        return parse_chat_response(response)
