"""Minimal A2A client: fetch an agent card and stream one chat turn.

Speaks the A2A JSON-RPC wire format directly over httpx (message/stream with SSE),
so it works against any A2A server, including the AgentCore-hosted one. It emits the
same event dicts as agent.run_agent(), which lets the web UI treat both identically.

CLI probe (no web UI):
    A2A_AGENT_URL=http://localhost:9000/ MCP_API_KEY=... python -m app.a2a_client "Show my watchlist"
Keys are read from MCP_API_KEY and (optionally) MODEL_API_KEY in the environment.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, AsyncIterator

import httpx

from .auth import a2a_target_auth
from .config import A2ATarget

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


class A2AError(Exception):
    """The A2A agent could not be reached or returned an error."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def _headers(
    target: A2ATarget,
    model_key: str | None,
    mcp_key: str | None,
    session_id: str | None,
    *,
    sse: bool,
) -> tuple[dict[str, str], Any]:
    extra, auth = a2a_target_auth(target)
    headers = {
        "Accept": "text/event-stream" if sse else "application/json",
        SESSION_HEADER: session_id or str(uuid.uuid4()),  # 36 chars, satisfies AgentCore's length rule
        **extra,
    }
    if sse:
        headers["Content-Type"] = "application/json"
    if mcp_key:
        headers["X-Mcp-Key"] = mcp_key
    if model_key:
        headers["X-Model-Key"] = model_key
    return headers, auth


def card_url(target: A2ATarget) -> str:
    base = target.url if target.url.endswith("/") else target.url + "/"
    return base + ".well-known/agent-card.json"


async def fetch_card(target: A2ATarget, session_id: str | None = None) -> dict[str, Any]:
    headers, auth = _headers(target, None, None, session_id, sse=False)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(card_url(target), headers=headers, auth=auth)
    except httpx.HTTPError as exc:
        raise A2AError(f"Could not reach the agent: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise A2AError(f"Agent card request failed with HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise A2AError("The agent card is not valid JSON.") from exc


def _parts(message: dict[str, Any] | None) -> list[dict[str, Any]]:
    return (message or {}).get("parts") or []


def _text_of(parts: list[dict[str, Any]]) -> str:
    return "\n".join(p.get("text", "") for p in parts if p.get("kind") == "text").strip()


def map_result(result: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Map one A2A streaming result object to UI events. Pure function (unit-testable).

    `state` carries across calls: text_seen, artifact_text, finished.
    """
    out: list[dict[str, Any]] = []
    kind = result.get("kind")

    if kind == "status-update":
        status = result.get("status") or {}
        st = status.get("state")
        parts = _parts(status.get("message"))
        events = [
            p["data"] for p in parts
            if p.get("kind") == "data" and isinstance(p.get("data"), dict)
            and p["data"].get("event") in ("tool_call", "tool_result")
        ]
        for d in events:
            if d["event"] == "tool_call":
                out.append({"type": "tool_call", "id": d.get("id", ""), "name": d.get("name", ""),
                            "input": d.get("input") or {}})
            else:
                out.append({"type": "tool_result", "id": d.get("id", ""), "content": d.get("content", ""),
                            "is_error": bool(d.get("is_error"))})
        text = _text_of(parts)
        if st in ("failed", "rejected", "canceled"):
            out.append({"type": "error", "message": text or f"The agent ended the task as '{st}'."})
            state["finished"] = True
        elif st in ("input-required", "auth-required"):
            out.append({"type": "error", "message": text or f"The agent needs more input ({st})."})
            state["finished"] = True
        else:
            if text and not events:
                out.append({"type": "text", "text": text})
                state["text_seen"] = True
            if st == "completed":
                _finish(out, state)

    elif kind == "artifact-update":
        state["artifact_text"] = (state.get("artifact_text", "") + _text_of(_parts(result.get("artifact")))).strip()

    elif kind == "message":
        text = _text_of(_parts(result))
        if text:
            out.append({"type": "text", "text": text})
            state["text_seen"] = True

    elif kind == "task":
        status = result.get("status") or {}
        st = status.get("state")
        for artifact in result.get("artifacts") or []:
            state["artifact_text"] = (state.get("artifact_text", "") + "\n" + _text_of(_parts(artifact))).strip()
        if st == "failed":
            out.append({"type": "error", "message": _text_of(_parts(status.get("message"))) or "The agent failed."})
            state["finished"] = True
        elif st == "completed":
            _finish(out, state)

    return out


def _finish(out: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if state.get("finished"):
        return
    if not state.get("text_seen") and state.get("artifact_text"):
        out.append({"type": "text", "text": state["artifact_text"]})
        state["text_seen"] = True
    out.append({"type": "done", "turns": 0, "duration_ms": int((time.monotonic() - state["t0"]) * 1000)})
    state["finished"] = True


# --------------------------------------------------------------------------- #
# Streaming chat
# --------------------------------------------------------------------------- #
async def stream_chat(
    target: A2ATarget,
    messages: list[dict[str, str]],
    model_key: str | None,
    mcp_key: str | None,
    session_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    secrets = [k for k in (model_key, mcp_key, target.bearer_token) if k]
    *history, last = messages
    parts: list[dict[str, Any]] = []
    if history:
        parts.append({"kind": "data", "data": {"history": history}})
    parts.append({"kind": "text", "text": last["content"]})
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/stream",
        "params": {"message": {"kind": "message", "role": "user", "messageId": uuid.uuid4().hex, "parts": parts}},
    }

    state: dict[str, Any] = {"t0": time.monotonic()}
    try:
        headers, auth = _headers(target, model_key, mcp_key, session_id, sse=True)
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
            async with client.stream("POST", target.url, json=payload, headers=headers, auth=auth) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:400]
                    yield {"type": "error",
                           "message": _redact(f"Agent returned HTTP {resp.status_code}: {body}", secrets)}
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if obj.get("error"):
                        err = obj["error"]
                        yield {"type": "error", "message": _redact(f"A2A error {err.get('code')}: {err.get('message')}", secrets)}
                        return
                    for event in map_result(obj.get("result") or {}, state):
                        if "message" in event:
                            event["message"] = _redact(event["message"], secrets)
                        yield event
    except Exception as exc:  # noqa: BLE001 - surface anything to the UI
        yield {"type": "error", "message": _redact(f"{type(exc).__name__}: {exc}", secrets)}
        return

    if not state.get("finished"):
        out: list[dict[str, Any]] = []
        _finish(out, state)
        for event in out:
            yield event


# --------------------------------------------------------------------------- #
# CLI probe
# --------------------------------------------------------------------------- #
async def _probe(prompt: str) -> int:
    from .config import load_ui_settings

    # The probe is always an A2A client, whatever AGENT_MODE says in .env, and an empty
    # A2A_AGENT_URL= line in .env counts as unset.
    os.environ["AGENT_MODE"] = "a2a"
    if not os.environ.get("A2A_AGENT_URL"):
        os.environ["A2A_AGENT_URL"] = "http://localhost:9000/"
    target = load_ui_settings().target
    assert target is not None
    mcp_key = os.environ.get("MCP_API_KEY") or None
    model_key = os.environ.get("MODEL_API_KEY") or None

    card = await fetch_card(target)
    print(f"Agent: {card.get('name')} (v{card.get('version')}), skills: "
          f"{', '.join(s.get('name', '') for s in card.get('skills', []))}")
    code = 0
    async for ev in stream_chat(target, [{"role": "user", "content": prompt}], model_key, mcp_key):
        t = ev["type"]
        if t == "tool_call":
            print(f"  > {ev['name']} {json.dumps(ev['input'])}")
        elif t == "tool_result":
            print(f"  < {'ERROR ' if ev['is_error'] else ''}{ev['content'][:300]}")
        elif t == "text":
            print(ev["text"])
        elif t == "error":
            print(f"ERROR: {ev['message']}", file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('usage: python -m app.a2a_client "your message"', file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(_probe(" ".join(sys.argv[1:]))))
