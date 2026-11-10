"""The single place that turns keys into credentials.

Each endpoint has its own auth mode. Only simple modes are implemented today.
To add OAuth later, add a branch here (for example, fetch a token and return
{"Authorization": "Bearer ..."}); agent.py, server.py and the A2A modules do not change.

Key precedence for each endpoint: key typed in the UI (or sent as a request
header), then the optional server-side default from the environment.
"""
from __future__ import annotations

import hmac
import json
from typing import Any, Sequence

from .config import A2ATarget, McpConfig, ModelConfig


class MissingKeyError(ValueError):
    """No key was supplied and no server-side default exists."""


class UnsupportedAuthMode(NotImplementedError):
    """The configured auth mode is not implemented yet."""


def _resolve(user_key: str | None, default: str | None, label: str) -> str:
    key = (user_key or "").strip() or default
    if not key:
        raise MissingKeyError(f"{label} API key is required.")
    return key


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
            "ANTHROPIC_API_KEY": _resolve(user_key, cfg.default_api_key, "Model"),
            "ANTHROPIC_AUTH_TOKEN": "",
        }
    raise UnsupportedAuthMode(f"MODEL_AUTH_MODE '{cfg.auth_mode}' is not implemented yet.")


def mcp_headers(cfg: McpConfig, user_key: str | None) -> dict[str, str]:
    """HTTP headers for the MCP server."""
    if cfg.auth_mode == "api_key":
        return {cfg.api_key_header: _resolve(user_key, cfg.default_api_key, "MCP")}
    raise UnsupportedAuthMode(f"MCP_AUTH_MODE '{cfg.auth_mode}' is not implemented yet.")


# --------------------------------------------------------------------------- #
# Outbound auth to an A2A agent (the web UI as an A2A client)
# --------------------------------------------------------------------------- #
def a2a_target_auth(cfg: A2ATarget) -> tuple[dict[str, str], Any]:
    """Return (extra headers, httpx.Auth or None) for calls to the A2A agent."""
    if cfg.auth_mode == "none":
        return {}, None
    if cfg.auth_mode == "bearer":
        if not cfg.bearer_token:
            raise MissingKeyError("A2A_AUTH_MODE is 'bearer' but A2A_BEARER_TOKEN is not set.")
        return {"Authorization": f"Bearer {cfg.bearer_token}"}, None
    if cfg.auth_mode == "sigv4":
        if not cfg.region:
            raise MissingKeyError("A2A_AUTH_MODE is 'sigv4' but no region is set (A2A_REGION or AWS_REGION).")
        return {}, _sigv4_auth(cfg.region)
    raise UnsupportedAuthMode(f"A2A_AUTH_MODE '{cfg.auth_mode}' is not implemented.")


def _sigv4_auth(region: str, service: str = "bedrock-agentcore") -> Any:
    """httpx.Auth that signs each request with the ambient AWS credentials."""
    import httpx

    class SigV4(httpx.Auth):
        requires_request_body = True

        def auth_flow(self, request):  # type: ignore[override]
            import boto3
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest

            creds = boto3.Session().get_credentials()
            if creds is None:
                raise MissingKeyError("No AWS credentials found for SigV4 signing.")
            aws_req = AWSRequest(
                method=request.method,
                url=str(request.url),
                data=request.content,
                headers={"Content-Type": request.headers.get("content-type", "application/json")},
            )
            SigV4Auth(creds.get_frozen_credentials(), service, region).add_auth(aws_req)
            for key, value in aws_req.headers.items():
                request.headers[key] = value
            yield request

    return SigV4()


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
