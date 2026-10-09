"""Amazon Bedrock Converse provider (default model: Amazon Nova 2 Lite, global profile).

The only module that knows the Converse request/response shape. Uses the
situation-guard AWS profile; one attempt per call (no automatic retries), so a
failure is reported instead of silently re-billed.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .llm import LLMError, LLMReply, Message, ToolCall, ToolSpec, Usage

MODEL_ENV = "SITUATION_GUARD_BEDROCK_MODEL"
DEFAULT_MODEL = "global.amazon.nova-2-lite-v1:0"
PROFILE_ENV = "SITUATION_GUARD_AWS_PROFILE"
REGION_ENV = "SITUATION_GUARD_AWS_REGION"
DEFAULT_PROFILE = "situation-guard"
DEFAULT_REGION = "ap-southeast-2"


def _simplify_schema(schema: Any) -> Any:
    """
    Drop schema noise some models reject: titles, `default: null`, and
    `anyOf: [X, null]` (optional) collapsed to X. Optionality is still
    expressed by the property being absent from `required`.
    """
    if isinstance(schema, list):
        return [_simplify_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: _simplify_schema(v) for k, v in schema.items() if k != "title"}
    if out.get("default", 0) is None:
        out.pop("default")
    variants = out.get("anyOf")
    if isinstance(variants, list):
        non_null = [v for v in variants if v != {"type": "null"}]
        if len(non_null) == 1 and len(non_null) < len(variants):
            out.pop("anyOf")
            out.update(non_null[0])
    return out


def _to_converse_messages(messages: List[Message]) -> List[Dict[str, Any]]:
    converted = []
    for m in messages:
        content: List[Dict[str, Any]] = []
        if m.text:
            content.append({"text": m.text})
        for call in m.tool_calls:
            content.append({"toolUse": {"toolUseId": call.id, "name": call.name, "input": call.arguments}})
        for result in m.tool_results:
            content.append(
                {
                    "toolResult": {
                        "toolUseId": result.call_id,
                        "content": [{"json": result.content}],
                        "status": "error" if result.is_error else "success",
                    }
                }
            )
        converted.append({"role": m.role, "content": content})
    return converted


def converse_request(
    model_id: str, system: str, messages: List[Message], tools: List[ToolSpec], max_tokens: int
) -> Dict[str, Any]:
    """The exact Converse parameters for one turn."""
    request: Dict[str, Any] = {
        "modelId": model_id,
        "messages": _to_converse_messages(messages),
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system:
        request["system"] = [{"text": system}]
    if tools:
        request["toolConfig"] = {
            "tools": [
                {
                    "toolSpec": {
                        "name": t.name,
                        "description": t.description or t.name,
                        "inputSchema": {"json": _simplify_schema(t.input_schema)},
                    }
                }
                for t in tools
            ]
        }
    return request


def parse_converse_response(response: Dict[str, Any]) -> LLMReply:
    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    texts = [b["text"] for b in blocks if "text" in b]
    calls = [
        ToolCall(id=b["toolUse"]["toolUseId"], name=b["toolUse"]["name"], arguments=b["toolUse"].get("input") or {})
        for b in blocks
        if "toolUse" in b
    ]
    usage = response.get("usage") or {}
    return LLMReply(
        text="\n".join(texts) if texts else None,
        tool_calls=calls,
        usage=Usage(input_tokens=usage.get("inputTokens", 0), output_tokens=usage.get("outputTokens", 0)),
    )


class BedrockConverseProvider:
    live = True

    def __init__(
        self,
        client: Any = None,
        model_id: Optional[str] = None,
        profile: Optional[str] = None,
        region: Optional[str] = None,
        max_tokens: int = 512,
    ):
        self.model_id = model_id or os.environ.get(MODEL_ENV, DEFAULT_MODEL)
        self.name = f"bedrock:{self.model_id}"
        self._client = client
        self._profile = profile or os.environ.get(PROFILE_ENV, DEFAULT_PROFILE)
        self._region = region or os.environ.get(REGION_ENV, DEFAULT_REGION)
        self._max_tokens = max_tokens

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import ProfileNotFound

            try:
                session = boto3.Session(profile_name=self._profile, region_name=self._region)
            except ProfileNotFound as exc:
                raise LLMError(f"AWS profile '{self._profile}' not found (set {PROFILE_ENV})") from exc
            self._client = session.client("bedrock-runtime", config=Config(retries={"total_max_attempts": 1}))
        return self._client

    def reply(self, system: str, messages: List[Message], tools: List[ToolSpec]) -> LLMReply:
        from botocore.exceptions import BotoCoreError, ClientError

        request = converse_request(self.model_id, system, messages, tools, self._max_tokens)
        try:
            response = self._get_client().converse(**request)
        except (BotoCoreError, ClientError) as exc:
            raise LLMError(f"Bedrock could not answer ({self.model_id}): {exc}") from exc
        return parse_converse_response(response)
