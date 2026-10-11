"""Simulated Alexa+ web app: chat and proof panel over the Situation Guard MCP server.

    browser  --POST /api/chat-->  chat()  -->  run_turn()  --MCP over HTTP-->  /mcp tools  -->  engine

chat() is the entry point for a conversation. The agent reaches the tools
through the same /mcp endpoint an outside client (Alexa+, Cursor, ...) would
use, so the demo exercises the real MCP server.

The proof panel reads the saved situation directly (/api/situations/...);
that is a display, not the agent.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any, AsyncContextManager, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent.llm import LLMProvider, Message, make_llm_provider
from .agent.loop import McpToolRunner, Step, run_turn
from .mcp_server import SituationView, situation_view
from .store import LogEntry, SituationStore
from .travel.search import TRAVEL_ENV

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MCP_URL_ENV = "SITUATION_GUARD_MCP_URL"
DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"

router = APIRouter()

# Conversation history per browser session. Kept in memory: a demo, not a database.
# The plan itself is saved by the store and survives restarts.
_sessions: Dict[str, List[Message]] = {}
_locks: Dict[str, asyncio.Lock] = {}


def _connect_over_http() -> AsyncContextManager[Any]:
    from mcp import Client

    return Client(os.environ.get(MCP_URL_ENV, DEFAULT_MCP_URL), mode="legacy")


def _tool_connector(request: Request) -> Callable[[], AsyncContextManager[Any]]:
    return getattr(request.app.state, "tool_connector", None) or _connect_over_http


def _llm(request: Request) -> LLMProvider:
    factory = getattr(request.app.state, "llm_factory", None) or make_llm_provider
    return factory()


def _store(request: Request) -> SituationStore:
    return getattr(request.app.state, "store", None) or SituationStore()


# --- API -----------------------------------------------------------------------


class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    steps: List[Step]
    brain: str
    input_tokens: int
    output_tokens: int
    stopped_early: bool


class Config(BaseModel):
    brain: str
    brain_live: bool
    travel: str
    situations: List[Dict[str, str]]


@router.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@router.get("/api/config", response_model=Config)
def config(request: Request):
    try:
        provider = _llm(request)
        brain, live = provider.name, provider.live
    except Exception as exc:  # misconfigured .env must not take the page down
        brain, live = f"unavailable ({exc})", False
    store = _store(request)
    situations = [{"id": sid, "name": store.load(sid).situation.name} for sid in store.list_ids()]
    return Config(
        brain=brain,
        brain_live=live,
        travel=os.environ.get(TRAVEL_ENV, "recorded"),
        situations=situations,
    )


@router.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request):
    """Entry point for one user turn: text in, Alexa's reply and the tool steps out."""
    session_id = body.session_id or uuid.uuid4().hex
    lock = _locks.setdefault(session_id, asyncio.Lock())
    async with lock:  # one turn at a time per conversation
        history = _sessions.get(session_id, [])
        provider = _llm(request)
        try:
            async with _tool_connector(request)() as client:
                turn = await run_turn(provider, McpToolRunner(client), history, body.text)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach the MCP server: {exc}") from exc
        _sessions[session_id] = turn.messages
    return ChatResponse(
        session_id=session_id,
        reply=turn.reply,
        steps=turn.steps,
        brain=provider.name,
        input_tokens=turn.usage.input_tokens,
        output_tokens=turn.usage.output_tokens,
        stopped_early=turn.stopped_early,
    )


class SituationPanel(BaseModel):
    view: SituationView
    log: List[LogEntry]


@router.get("/api/situations/{situation_id}", response_model=SituationPanel)
def situation_panel(situation_id: str, request: Request):
    store = _store(request)
    try:
        state = store.load(situation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown situation '{situation_id}'") from exc
    return SituationPanel(view=situation_view(state), log=state.log[-20:])


class ResetRequest(BaseModel):
    situation_id: str
    session_id: Optional[str] = None


@router.post("/api/reset")
def reset(body: ResetRequest, request: Request):
    """Demo retake: restore the situation's starting file and forget the conversation."""
    try:
        _store(request).reset(body.situation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown situation '{body.situation_id}'") from exc
    if body.session_id:
        _sessions.pop(body.session_id, None)
    return {"reset": body.situation_id}
