"""The single place that turns keys into credentials.

Each endpoint has its own auth mode. Only simple modes are implemented today.
To add OAuth later, add a branch here (for example, fetch a token and return
{"Authorization": "Bearer ..."}); agent.py and the A2A module do not change.

Key precedence for each outbound endpoint (first non-blank wins):
  * MCP server:  the X-Mcp-Key header of the call, then MCP_API_KEY from the environment,
                 then the caller's own x-api-key (the key it used to reach this agent).
  * Model proxy: the X-Model-Key header of the call, then MODEL_API_KEY from the environment.
                 (Direct Anthropic mode always uses the agent's ANTHROPIC_API_KEY.)
"""
from __future__ import annotations

import hmac
import json
from typing import Any, Sequence

from .config import McpConfig, ModelConfig


class MissingKeyError(ValueError):
    """No key was supplied and no server-side default exists."""


class UnsupportedAuthMode(NotImplementedError):
    """The configured auth mode is not implemented yet."""


def _first_key(label: str, *candidates: str | None, hint: str = "") -> str:
    """The first candidate that is not blank. Raises MissingKeyError when there is none."""
    for key in candidates:
        key = (key or "").strip()
        if key:
            return key
    raise MissingKeyError(f"{label} API key is required.{hint}")


def model_env(cfg: ModelConfig, user_key: str | None) -> dict[str, str]:
    """Environment for the Agent SDK's CLI subprocess, for the model endpoint.

    ANTHROPIC_API_KEY is sent by the SDK as an x-api-key header.
    ANTHROPIC_AUTH_TOKEN is blanked so it can never switch the request to
    an Authorization: Bearer header.

    Direct-to-Anthropic route (ANTHROPIC_API_KEY configured on the agent): the agent's
    Anthropic key and the Anthropic API URL are used and any key sent by a caller is ignored.
    """
    if cfg.direct:
        return {
            "ANTHROPIC_BASE_URL": cfg.base_url,
            "ANTHROPIC_API_KEY": cfg.anthropic_api_key or "",
            "ANTHROPIC_AUTH_TOKEN": "",
        }
    if cfg.auth_mode == "api_key":
        return {
            "ANTHROPIC_BASE_URL": cfg.base_url,
            "ANTHROPIC_API_KEY": _first_key("Model", user_key, cfg.default_api_key),
            "ANTHROPIC_AUTH_TOKEN": "",
        }
    raise UnsupportedAuthMode(f"MODEL_AUTH_MODE '{cfg.auth_mode}' is not implemented yet.")


def _mcp_key(cfg: McpConfig, user_key: str | None, agent_key: str | None) -> tuple[str, str]:
    """The MCP key and where it came from: the caller's X-Mcp-Key, else MCP_API_KEY, else the caller's own x-api-key."""
    for source, key in (
        ("X-Mcp-Key header", user_key),
        ("MCP_API_KEY", cfg.default_api_key),
        ("x-api-key of the call", agent_key),
    ):
        key = (key or "").strip()
        if key:
            return key, source
    raise MissingKeyError(
        "MCP API key is required. Send X-Mcp-Key, set MCP_API_KEY on the agent, or call the agent with an x-api-key."
    )


def mcp_headers(cfg: McpConfig, user_key: str | None, agent_key: str | None = None) -> dict[str, str]:
    """HTTP headers for the MCP server.

    The key is the caller's X-Mcp-Key, else MCP_API_KEY, else the caller's own x-api-key (agent_key).
    That last fallback means the agent key is sent to MCP_URL when nothing else is set, so the MCP
    server (or the gateway in front of it) has to accept it as an MCP key.
    """
    if cfg.auth_mode == "api_key":
        return {cfg.api_key_header: _mcp_key(cfg, user_key, agent_key)[0]}
    raise UnsupportedAuthMode(f"MCP_AUTH_MODE '{cfg.auth_mode}' is not implemented yet.")


def mcp_key_source(cfg: McpConfig, user_key: str | None, agent_key: str | None = None) -> str:
    """Where the MCP key comes from, as a label for logs and the chat. Never the key itself."""
    try:
        return _mcp_key(cfg, user_key, agent_key)[1]
    except MissingKeyError:
        return "none"


# --------------------------------------------------------------------------- #
# Inbound auth: callers of the agent present x-api-key
# --------------------------------------------------------------------------- #
def verify_api_key(provided: str | None, valid: Sequence[str]) -> bool:
    """Constant-time check of a presented key against the configured keys.

    No configured keys means the endpoint is open, so this returns True.
    Every configured key is compared (no early exit) so timing does not reveal which one matched.
    """
    if not valid:
        return True
    presented = (provided or "").encode("utf-8")
    ok = False
    for key in valid:
        ok = hmac.compare_digest(presented, key.encode("utf-8")) | ok
    return ok


class ApiKeyMiddleware:
    """Pure-ASGI middleware: require a valid x-api-key on every HTTP request except discovery and health.

    Open (GET/HEAD only): /ping (AgentCore's health check) and the agent card. Everything else,
    including the JSON-RPC endpoint, needs the key. Only the x-api-key header is read (never a query
    string), and the key is never logged or echoed back. Non-HTTP scopes (lifespan) pass straight through.

    CORS preflights never reach this check: the CORS middleware is added after it, so it wraps this one
    (see a2a_server.main).
    """

    OPEN_PATHS = ("/ping", "/.well-known/agent-card.json", "/.well-known/agent.json")

    def __init__(self, app: Any, keys: Sequence[str], open_paths: Sequence[str] | None = None):
        self.app = app
        self.keys = list(keys)
        self.open_paths = tuple(open_paths or self.OPEN_PATHS)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = (scope.get("path") or "/").rstrip("/") or "/"
        if scope.get("method", "GET") in ("GET", "HEAD") and path in self.open_paths:
            await self.app(scope, receive, send)
            return
        provided = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"x-api-key"), None
        )
        if verify_api_key(provided, self.keys):
            await self.app(scope, receive, send)
            return
        body = json.dumps(
            {"error": "unauthorized", "message": "Missing or invalid x-api-key header."}
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
