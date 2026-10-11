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
from typing import Any, AsyncContextManager, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.agent.llm import LLMProvider, Message, make_llm_provider
from app.calendars.google import make_calendar
from app.agent.loop import Step, run_turn
from app.agent.prompt import system_prompt
from app.agent.tool_runner import McpToolRunner
from app.core.feasibility import evaluate_situation
from app.mcp.views import SituationView, situation_view
from app.situations.store import LogEntry, SituationStore
from app.events.holidays.nager import HOLIDAYS_ENV
from app.events.weather.open_meteo import WEATHER_ENV
from app.options.travel.search import TRAVEL_ENV
from app.situations.places import PLACES_ENV
from app.paths import STATIC_DIR

MCP_URL_ENV = "SITUATION_GUARD_MCP_URL"
DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
TIMEZONE_ENV = "SITUATION_GUARD_TIMEZONE"
DEFAULT_TIMEZONE = "Australia/Sydney"

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


class Position(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None
    location: Optional[Position] = Field(None, description="Where the device is, if the user shared it")


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
    modes: Dict[str, str] = Field(default_factory=dict, description="Each outside source: recorded/known or live")
    calendar: str = Field("off", description="off, google, or google-signed-out")
    situations: List[Dict[str, Any]] = Field(..., description="id, name, and whether it is predefined (reset) or made in conversation (delete)")


def _calendar_status() -> str:
    try:
        calendar = make_calendar()
    except ValueError:
        return "off"
    if calendar is None:
        return "off"
    return "google" if calendar.signed_in else "google-signed-out"


@router.get("/")
def index():
    """The page, with its script and styles linked by version so a browser never mixes old and new files."""
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        version = int((STATIC_DIR / name).stat().st_mtime)
        page = page.replace(f"/static/{name}", f"/static/{name}?v={version}")
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@router.get("/api/config", response_model=Config)
def config(request: Request):
    try:
        provider = _llm(request)
        brain, live = provider.name, provider.live
    except Exception as exc:  # misconfigured .env must not take the page down
        brain, live = f"unavailable ({exc})", False
    store = _store(request)
    situations = [
        {"id": sid, "name": store.load(sid).situation.name, "predefined": store.is_predefined(sid)}
        for sid in store.list_ids()
    ]
    return Config(
        brain=brain,
        brain_live=live,
        travel=os.environ.get(TRAVEL_ENV, "recorded"),
        modes={
            "travel": os.environ.get(TRAVEL_ENV, "recorded"),
            "weather": os.environ.get(WEATHER_ENV, "recorded"),
            "holidays": os.environ.get(HOLIDAYS_ENV, "recorded"),
            "places": os.environ.get(PLACES_ENV, "known"),
        },
        calendar=_calendar_status(),
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
                here = (body.location.latitude, body.location.longitude) if body.location else None
                instructions = system_prompt(os.environ.get(TIMEZONE_ENV, DEFAULT_TIMEZONE), location=here)
                turn = await run_turn(provider, McpToolRunner(client), history, body.text, system=instructions)
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
    try:
        verdict = evaluate_situation(state.situation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Situation cannot be evaluated: {exc}") from exc
    return SituationPanel(view=situation_view(state, verdict), log=state.log[-20:])


class ResetRequest(BaseModel):
    situation_id: str
    session_id: Optional[str] = None


@router.post("/api/reset")
def reset(body: ResetRequest, request: Request):
    """
    Demo retake: a predefined situation goes back to its starting file; one created in
    conversation is deleted. The conversation is forgotten either way.
    """
    store = _store(request)
    try:
        if store.is_predefined(body.situation_id):
            store.reset(body.situation_id)
            outcome = "reset"
        else:
            store.forget(body.situation_id)
            outcome = "deleted"
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown situation '{body.situation_id}'") from exc
    if body.session_id:
        _sessions.pop(body.session_id, None)
    return {"situation_id": body.situation_id, "outcome": outcome}
