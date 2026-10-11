"""Reading tools: what situations exist, and how one stands right now. They change nothing."""

from __future__ import annotations

from typing import List

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from app.mcp.context import ToolContext, evaluate
from app.mcp.views import SituationBrief, SituationView, situation_view
from app.situations.store import UnreadableSituation


def register(server: MCPServer, ctx: ToolContext) -> None:
    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def list_situations() -> List[SituationBrief]:
        """List the situations being guarded, with their current time and whether they still work."""
        briefs = []
        for situation_id in ctx.store.list_ids():
            try:
                situation = ctx.store.load(situation_id).situation
            except UnreadableSituation:
                continue  # a damaged saved copy must not hide every other plan
            briefs.append(
                SituationBrief(id=situation.id, name=situation.name, now=situation.now, feasible=evaluate(situation).feasible)
            )
        return briefs

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def get_situation(situation_id: str) -> SituationView:
        """Current plan: commitments, goals at risk, the engine's reasons, and any options on offer."""
        state = ctx.load(situation_id)
        return situation_view(state, evaluate(state.situation))
