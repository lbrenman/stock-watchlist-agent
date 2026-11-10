"""The single place that turns keys into credentials.

Each endpoint has its own auth mode. Only simple modes are implemented today.
To add OAuth later, add a branch here (for example, fetch a token and return
{"Authorization": "Bearer ..."}); agent.py, server.py and the A2A modules do not change.

Key precedence for each endpoint: key typed in the UI (or sent as a request
header), then the optional server-side default from the environment.
"""
from __future__ import annotations

from typing import Any

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

    Direct mode (ANTHROPIC_API_KEY configured on the server): the server's
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
