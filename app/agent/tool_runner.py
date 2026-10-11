"""How the agent reaches its tools: any MCP client (over HTTP, or in-process for tests)."""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, Tuple

from app.agent.llm import ToolSpec


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
