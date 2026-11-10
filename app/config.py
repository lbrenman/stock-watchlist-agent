"""Configuration: environment variables only.

Two programs read it:
  * the A2A agent (python -m app.a2a_server) needs the model route, the MCP server and, optionally, inbound keys;
  * the chat UI server (uvicorn app.server:app) only needs to know where the agent is (A2A_AGENT_URL).

Each endpoint (model, MCP server, A2A agent) has its own config object with its own auth mode, so adding
OAuth later means touching auth.py, not the agent logic.

Model route (set on the agent):
  * ANTHROPIC_API_KEY set   -> call Anthropic directly (dev fallback, bypasses the gateway)
  * ANTHROPIC_API_KEY unset -> call an Anthropic-compatible proxy such as Fusion AI Gateway
                               (MODEL_BASE_URL + MODEL_NAME + a gateway key)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

try:  # optional convenience for local runs; containers pass real env vars
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ANTHROPIC_API_URL = "https://api.anthropic.com"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: str) -> bool:
    return _optional(name, default).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class ModelConfig:
    base_url: str  # gateway root (no /v1/messages suffix), or the Anthropic API URL when calling Anthropic directly
    name: str  # model name sent to whichever endpoint is active
    auth_mode: str  # proxy route: "api_key" now; "oauth" reserved for later
    default_api_key: str | None  # optional gateway key fallback; the UI can override it
    disable_betas: bool  # proxy route only: strip anthropic-beta headers some gateways reject
    anthropic_api_key: str | None = None  # set => call Anthropic directly

    @property
    def direct(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def route(self) -> str:
        return "direct" if self.direct else "gateway"


@dataclass(frozen=True)
class McpConfig:
    server_name: str  # key used when registering the server; also the tool-name prefix
    url: str
    transport: str  # "http" (streamable HTTP) or "sse"
    auth_mode: str  # "api_key" now; "oauth" reserved for later
    api_key_header: str  # header the gateway reads the key from
    default_api_key: str | None


@dataclass(frozen=True)
class Settings:
    """Everything the agent itself needs."""

    model: ModelConfig
    mcp: McpConfig
    max_turns: int


@dataclass(frozen=True)
class A2ATarget:
    """The A2A agent the chat UI talks to (local, Codespace, or AgentCore)."""

    url: str  # the agent's JSON-RPC endpoint, exactly as its card advertises it
    auth_mode: str  # "none" | "bearer" | "sigv4": how to authenticate to the agent's host
    bearer_token: str | None
    region: str | None  # for sigv4
    api_key: str | None = None  # default x-api-key to send (the UI field overrides it)


@dataclass(frozen=True)
class UiSettings:
    target: A2ATarget


def load_settings() -> Settings:
    server_name = _optional("MCP_SERVER_NAME", "stockwatchlist")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", server_name):
        raise RuntimeError("MCP_SERVER_NAME may contain only letters, digits, '_' and '-'")

    transport = _optional("MCP_TRANSPORT", "http").lower()
    if transport not in ("http", "sse"):
        raise RuntimeError("MCP_TRANSPORT must be 'http' or 'sse'")

    anthropic_key = _optional("ANTHROPIC_API_KEY") or None
    if anthropic_key:
        name = _optional("ANTHROPIC_MODEL") or _optional("MODEL_NAME")
        if not name:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is set, so also set ANTHROPIC_MODEL (an Anthropic model ID)."
            )
        base_url = ANTHROPIC_API_URL
    else:
        name = _required("MODEL_NAME")
        base_url = _required("MODEL_BASE_URL").rstrip("/")

    return Settings(
        model=ModelConfig(
            base_url=base_url,
            name=name,
            auth_mode=_optional("MODEL_AUTH_MODE", "api_key").lower(),
            default_api_key=_optional("MODEL_API_KEY") or None,
            disable_betas=_flag("MODEL_DISABLE_BETAS", "1"),
            anthropic_api_key=anthropic_key,
        ),
        mcp=McpConfig(
            server_name=server_name,
            url=_required("MCP_URL"),
            transport=transport,
            auth_mode=_optional("MCP_AUTH_MODE", "api_key").lower(),
            api_key_header=_optional("MCP_API_KEY_HEADER", "x-api-key"),
            default_api_key=_optional("MCP_API_KEY") or None,
        ),
        max_turns=int(_optional("AGENT_MAX_TURNS", "15")),
    )


def load_ui_settings() -> UiSettings:
    """Settings for the chat UI server. A2A_AGENT_URL defaults to the agent on this machine (port 9000)."""
    auth_mode = _optional("A2A_AUTH_MODE", "none").lower()
    if auth_mode not in ("none", "bearer", "sigv4"):
        raise RuntimeError("A2A_AUTH_MODE must be 'none', 'bearer' or 'sigv4'")
    default_url = f"http://localhost:{_optional('A2A_PORT', '9000')}/"
    return UiSettings(
        target=A2ATarget(
            url=_optional("A2A_AGENT_URL") or default_url,
            auth_mode=auth_mode,
            bearer_token=_optional("A2A_BEARER_TOKEN") or None,
            region=_optional("A2A_REGION") or _optional("AWS_REGION") or None,
            api_key=_optional("A2A_API_KEY") or None,
        )
    )


@dataclass(frozen=True)
class InboundAuth:
    """Keys that callers must present (as x-api-key) to reach the agent."""

    keys: tuple[str, ...]
    require: bool


def load_inbound_auth() -> InboundAuth:
    """AGENT_API_KEYS: one key, or several separated by commas (so keys can be rotated with overlap).

    Protects the A2A agent: every call except /ping and the agent card must carry a valid x-api-key.
    With AGENT_REQUIRE_API_KEY=1 the agent refuses to start without a key; otherwise no keys means
    open access (a warning is logged).
    """
    keys = tuple(k.strip() for k in _optional("AGENT_API_KEYS").split(",") if k.strip())
    require = _flag("AGENT_REQUIRE_API_KEY", "0")
    if require and not keys:
        raise RuntimeError(
            "AGENT_REQUIRE_API_KEY is set but AGENT_API_KEYS is empty. Generate a key with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    return InboundAuth(keys=keys, require=require)
