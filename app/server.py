"""Thin web layer for the chat UI. All agent logic lives in agent.py.

AGENT_MODE=direct (default): this server runs the agent itself.
AGENT_MODE=a2a:              this server is an A2A client for A2A_AGENT_URL (local, Codespace
                             or AgentCore) and forwards the user's keys as request headers.

Keys arrive per request in headers (X-Model-Key, X-Mcp-Key). A blank or missing
header falls back to the optional server-side default from the environment.
Nothing is stored between requests, and keys are never logged.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Header
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import a2a_client
from .auth import MissingKeyError, UnsupportedAuthMode, a2a_target_auth, mcp_headers, model_env
from .config import load_ui_settings, local_card_url

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("server")

ui = load_ui_settings()  # fails fast with a clear message if env vars are missing
settings = ui.agent  # direct mode only
target = ui.target  # a2a mode only

if ui.mode == "direct":
    log.info("Mode: direct. Model route: %s (%s)", settings.model.route, settings.model.name)  # type: ignore[union-attr]
else:
    log.info("Mode: a2a. Agent endpoint: %s", urlsplit(target.url).netloc)  # type: ignore[union-attr]

# agent.py imports the Agent SDK, which a2a-mode deployments of the UI do not need to load.
if ui.mode == "direct":
    from .agent import ConnectFailed, connect, run_agent

app = FastAPI(title="Stock Watchlist Agent")
INDEX = Path(__file__).parent / "static" / "index.html"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


def _check_keys(x_model_key: str | None, x_mcp_key: str | None) -> JSONResponse | None:
    """Direct mode: return an error response if a key is missing or an auth mode is unsupported.

    In a2a mode the remote agent decides which keys it needs, so nothing is checked here.
    """
    if ui.mode != "direct":
        return None
    try:
        model_env(settings.model, x_model_key)  # type: ignore[union-attr]
        mcp_headers(settings.mcp, x_mcp_key)  # type: ignore[union-attr]
    except MissingKeyError as exc:
        return JSONResponse({"errors": {"keys": str(exc)}}, status_code=400)
    except UnsupportedAuthMode as exc:
        return JSONResponse({"errors": {"config": str(exc)}}, status_code=500)
    return None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(INDEX)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
async def get_config() -> dict:
    """Non-secret settings the UI needs.

    model_key / mcp_key tell the UI how to present each field:
      "required" | "optional" (blank uses a server or agent default) | "unused"
    """
    if ui.mode == "direct":
        m = settings.model  # type: ignore[union-attr]
        if m.direct:
            model_key = "unused"
        else:
            model_key = "optional" if m.default_api_key else "required"
        mcp_key = "optional" if settings.mcp.default_api_key else "required"  # type: ignore[union-attr]
    else:
        model_key = mcp_key = "optional"
    # The agent card this UI is showing: our own A2A server's card (direct mode), or the
    # card of the remote agent the UI is a client of (a2a mode).
    card_url = a2a_client.card_url(target) if ui.mode == "a2a" else local_card_url()  # type: ignore[arg-type]
    return {"mode": ui.mode, "model_key": model_key, "mcp_key": mcp_key, "agent_card_url": card_url}


@app.get("/api/agent-card")
async def agent_card_status() -> dict:
    """Direct mode: is the local A2A server running, and what address does its card advertise?"""
    if ui.mode == "a2a":
        return {"reachable": None}
    port = os.environ.get("A2A_PORT", "").strip() or "9000"
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json")
        if resp.status_code == 200:
            card = resp.json()
            return {"reachable": True, "name": card.get("name"), "advertised_url": card.get("url")}
    except Exception:  # noqa: BLE001 - any failure just means "not reachable"
        pass
    return {"reachable": False}


@app.post("/api/connect")
async def api_connect(
    x_model_key: str | None = Header(default=None),
    x_mcp_key: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
):
    if ui.mode == "a2a":
        try:
            card = await a2a_client.fetch_card(target, x_session_id)  # type: ignore[arg-type]
        except (a2a_client.A2AError, MissingKeyError, UnsupportedAuthMode) as exc:
            return JSONResponse({"errors": {"connect": str(exc)}}, status_code=502)
        skills = [{"name": s.get("name", ""), "description": s.get("description", "")}
                  for s in card.get("skills") or []]
        return {
            "kind": "skills",
            "label": f"{card.get('name', 'A2A agent')} via A2A",
            "tools": skills,
        }

    if (bad := _check_keys(x_model_key, x_mcp_key)) is not None:
        return bad
    try:
        tools = await connect(settings, x_model_key, x_mcp_key)  # type: ignore[arg-type]
    except ConnectFailed as exc:
        return JSONResponse({"errors": exc.errors}, status_code=401)
    m = settings.model  # type: ignore[union-attr]
    route = "direct to Anthropic" if m.direct else "via gateway"
    return {"kind": "tools", "label": f"{m.name} {route}", "tools": tools}


@app.post("/api/chat")
async def api_chat(
    req: ChatRequest,
    x_model_key: str | None = Header(default=None),
    x_mcp_key: str | None = Header(default=None),
    x_session_id: str | None = Header(default=None),
):
    if (bad := _check_keys(x_model_key, x_mcp_key)) is not None:
        return bad
    if req.messages[-1].role != "user":
        return JSONResponse({"errors": {"messages": "The last message must be from the user."}}, status_code=400)

    messages = [m.model_dump() for m in req.messages]

    if ui.mode == "a2a":
        try:
            a2a_target_auth(target)  # type: ignore[arg-type]  # fail early on a missing token or region
        except (MissingKeyError, UnsupportedAuthMode) as exc:
            return JSONResponse({"errors": {"config": str(exc)}}, status_code=500)
        events = a2a_client.stream_chat(target, messages, x_model_key, x_mcp_key, x_session_id)  # type: ignore[arg-type]
    else:
        events = run_agent(settings, messages, x_model_key, x_mcp_key)  # type: ignore[arg-type]

    async def stream():
        async for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
