"""LLM providers — swappable brains for the agent, like travel providers.

The agent loop only talks to an LLMProvider: give it the conversation and the
tools, get back text and/or tool calls. Which model answers is a setting:

    SITUATION_GUARD_LLM=scripted   (default) rule-based stand-in, free, offline
    SITUATION_GUARD_LLM=bedrock    Amazon Bedrock Converse (billed per token)
    SITUATION_GUARD_LLM=openai     OpenAI Chat Completions (billed per token)

Settings and OPENAI_API_KEY may also come from a gitignored .env file.

The LLM never decides whether a plan works — the engine does, through the tools.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Literal, Optional, Protocol

from pydantic import BaseModel, Field

LLM_ENV = "SITUATION_GUARD_LLM"


class LLMError(Exception):
    """The provider could not answer: no access, throttled, bad request, ..."""


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    call_id: str
    name: str
    is_error: bool = False
    content: Dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """One turn. A user turn carries text or tool results; an assistant turn carries text and/or tool calls."""

    role: Literal["user", "assistant"]
    text: Optional[str] = None
    tool_calls: List[ToolCall] = Field(default_factory=list)
    tool_results: List[ToolResult] = Field(default_factory=list)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LLMReply(BaseModel):
    text: Optional[str] = None
    tool_calls: List[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


class LLMProvider(Protocol):
    name: str
    live: bool

    def reply(self, system: str, messages: List[Message], tools: List[ToolSpec]) -> LLMReply:
        """Next assistant turn. Raise LLMError when the model cannot be reached."""
        ...


def make_llm_provider(use_dotenv: bool = True) -> LLMProvider:
    """scripted (default, free), bedrock or openai (live, billed)."""
    if use_dotenv:
        from dotenv import load_dotenv

        load_dotenv(override=False)  # a real environment variable always wins over .env
    choice = os.environ.get(LLM_ENV, "scripted").strip().lower()
    if choice == "scripted":
        from .scripted import ScriptedProvider, guided_policy

        return ScriptedProvider(guided_policy())
    if choice == "bedrock":
        from .bedrock import BedrockConverseProvider

        return BedrockConverseProvider()
    if choice == "openai":
        from .openai_provider import OpenAIChatProvider

        return OpenAIChatProvider()
    raise LLMError(f"{LLM_ENV} must be 'scripted', 'bedrock' or 'openai', not '{choice}'")
