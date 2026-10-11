"""Puts the app together: the MCP server at /mcp, the web demo at /, and the original JSON API."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.mcp.server import mcp_server
from app.paths import STATIC_DIR
from app.web.api import router as demo_router
from app.web.situations_api import router as situations_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(title="Situation Guard", lifespan=lifespan)

# MCP over Streamable HTTP at /mcp (one URL; all tools are called through it).
app.router.routes.extend(mcp_server.streamable_http_app(streamable_http_path="/mcp").routes)

# Simulated Alexa+ web demo: page at /, its files at /static, its API under /api.
app.include_router(demo_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Original JSON endpoints over the predefined situations.
app.include_router(situations_router)
