"""Thin web layer for the chat UI. The UI is always an A2A client.

It never calls the model or the MCP server itself. It fetches the agent card from A2A_AGENT_URL
(default: the agent on this machine, http://localhost:9000/), forwards each chat turn to that agent,
and relays the streamed tool calls and answer to the browser.

Keys arrive per request in headers and are forwarded to the agent; nothing is stored between
requests and keys are never logged:
    X-Api-Key     the agent's access key (the agent checks it; Connect verifies it).
                  Blank means the key from this server's environment (A2A_API_KEY, else the first AGENT_API_KEYS).
    X-Mcp-Key     decides whose watchlist this is; blank means the agent's default
The model key is never set from the UI: it belongs to the agent's own configuration.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import a2a_client
from .auth import MissingKeyError, UnsupportedAuthMode, a2a_target_auth
from .config import load_ui_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("server")

target = load_ui_settings().target
log.info("Chat UI talking to the A2A agent at %s (host auth: %s)", urlsplit(target.url).netloc, target.auth_mode)

app = FastAPI(title="Stock Watchlist Agent")
INDEX = Path(__file__).parent / "static" / "index.html"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(INDEX)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
async def get_config() -> dict:
    """Non-secret settings the UI needs. The key itself is never sent to the browser, only whether one exists."""
    return {"agent_card_url": a2a_client.card_url(target), "key_from_env": bool(target.api_key)}


@app.post("/api/connect")
async def api_connect(
    x_session_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    """Fetch the agent card, then check the access key against the agent without calling the model."""
    try:
        card = await a2a_client.fetch_card(target, x_session_id, x_api_key)
    except (a2a_client.A2AError, MissingKeyError, UnsupportedAuthMode) as exc:
        return JSONResponse({"errors": {"connect": str(exc)}}, status_code=502)
    try:
        await a2a_client.check_agent_key(target, x_api_key, x_session_id)
    except a2a_client.A2AError as exc:
        return JSONResponse({"errors": {"agent": str(exc)}}, status_code=401)
    return {
        "name": card.get("name", "A2A agent"),
        "description": card.get("description", ""),
        "version": card.get("version", ""),
        "advertised_url": card.get("url", ""),
        "skills": [
            {"name": s.get("name", ""), "description": s.get("description", "")}
            for s in card.get("skills") or []
        ],
    }


@app.post("/api/chat")
async def api_chat(
    req: ChatRequest,
    x_mcp_key: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
):
    if req.messages[-1].role != "user":
        return JSONResponse({"errors": {"messages": "The last message must be from the user."}}, status_code=400)
    try:
        a2a_target_auth(target)  # fail early on a missing token or region
    except (MissingKeyError, UnsupportedAuthMode) as exc:
        return JSONResponse({"errors": {"config": str(exc)}}, status_code=500)

    messages = [m.model_dump() for m in req.messages]
    events = a2a_client.stream_chat(target, messages, None, x_mcp_key, x_session_id, x_api_key)

    async def stream():
        async for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
