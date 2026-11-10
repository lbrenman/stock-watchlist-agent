"""Configuration: environment variables only.

Each endpoint (model, MCP server, A2A target) has its own config object with its
own auth mode, so adding OAuth later means touching auth.py, not the agent logic.

Model route:
  * ANTHROPIC_API_KEY set   -> call Anthropic directly (dev fallback, bypasses the gateway)
  * ANTHROPIC_API_KEY unset -> call the gateway's Anthropic-compatible endpoint
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
    base_url: str  # gateway root (no /v1/messages suffix), or the Anthropic API URL in direct mode
    name: str  # model name sent to whichever endpoint is active
    auth_mode: str  # gateway mode: "api_key" now; "oauth" reserved for later
    default_api_key: str | None  # optional gateway key fallback; the UI can override it
    disable_betas: bool  # gateway mode only: strip anthropic-beta headers some gateways reject
    anthropic_api_key: str | None = None  # set => direct mode

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
    """An A2A agent the web UI talks to (local, Codespace, or AgentCore)."""

    url: str  # the agent's JSON-RPC endpoint, exactly as its card advertises it
    auth_mode: str  # "none" | "bearer" | "sigv4"
    bearer_token: str | None
    region: str | None  # for sigv4


@dataclass(frozen=True)
class UiSettings:
    mode: str  # "direct": UI server runs the agent itself; "a2a": UI server is an A2A client
    agent: Settings | None  # present in direct mode
    target: A2ATarget | None  # present in a2a mode


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
    """Settings for the web UI server. In a2a mode it needs no model or MCP config."""
    mode = _optional("AGENT_MODE", "direct").lower()
    if mode == "direct":
        return UiSettings(mode=mode, agent=load_settings(), target=None)
    if mode == "a2a":
        auth_mode = _optional("A2A_AUTH_MODE", "none").lower()
        if auth_mode not in ("none", "bearer", "sigv4"):
            raise RuntimeError("A2A_AUTH_MODE must be 'none', 'bearer' or 'sigv4'")
        return UiSettings(
            mode=mode,
            agent=None,
            target=A2ATarget(
                url=_required("A2A_AGENT_URL"),
                auth_mode=auth_mode,
                bearer_token=_optional("A2A_BEARER_TOKEN") or None,
                region=_optional("A2A_REGION") or _optional("AWS_REGION") or None,
            ),
        )
    raise RuntimeError("AGENT_MODE must be 'direct' or 'a2a'")
