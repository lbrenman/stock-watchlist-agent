"""Agent logic. No web-framework imports, so it can be reused or redeployed.

Public API:
    run_agent(settings, messages, model_key, mcp_key, agent_key) -> async stream of event dicts
        model_key  the caller's X-Model-Key (optional)
        mcp_key    the caller's X-Mcp-Key (optional)
        agent_key  the caller's own x-api-key (optional); the last fallback for the MCP key

Event dicts yielded by run_agent:
    {"type": "text", "text": str}
    {"type": "tool_call", "id": str, "name": str, "input": dict}
    {"type": "tool_result", "id": str, "content": str, "is_error": bool}
    {"type": "done", "turns": int, "duration_ms": int}
    {"type": "error", "message": str}
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from .auth import mcp_headers, model_env
from .config import Settings

log = logging.getLogger("agent")

SYSTEM_PROMPT = (
    "You are a stock watchlist assistant. You can only act through the tools provided. "
    "For any question about quotes or the contents of the watchlist, call a tool; never answer "
    "from memory and never invent prices or watchlist contents. The watchlist may have changed "
    "since earlier in the conversation, so re-read it with a tool when it matters. "
    "After you add or remove symbols, confirm what changed. Keep answers short and plain."
)

# Belt and braces: tools=[] already turns off Claude Code's built-in tools.
BUILTIN_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch",
    "Task", "Agent", "TodoWrite", "NotebookEdit",
]

# Keep the CLI subprocess from calling anything except the gateway.
_RUNTIME_ENV = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}


# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #
def _credentials(settings: Settings, model_key: str | None, mcp_key: str | None, agent_key: str | None):
    env = model_env(settings.model, model_key)
    headers = mcp_headers(settings.mcp, mcp_key, agent_key)
    secrets = [env["ANTHROPIC_API_KEY"], *headers.values()]
    return env, headers, secrets


def _build_options(
    settings: Settings,
    env: dict[str, str],
    headers: dict[str, str],
    *,
    max_turns: int,
) -> ClaudeAgentOptions:
    model = settings.model.name
    full_env = {
        **env,
        **_RUNTIME_ENV,
        # Route every model alias the CLI might use (including background work) to our model.
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
    }
    if settings.model.disable_betas and not settings.model.direct:
        full_env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"

    server = settings.mcp.server_name
    mcp_servers: dict[str, Any] = {
        server: {"type": settings.mcp.transport, "url": settings.mcp.url, "headers": headers}
    }

    return ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[],  # no built-in tools: the MCP tools are the whole demo
        disallowed_tools=BUILTIN_TOOLS,
        allowed_tools=[f"mcp__{server}__*"],
        mcp_servers=mcp_servers,
        strict_mcp_config=True,  # ignore any other MCP config on the machine
        setting_sources=[],  # ignore user/project/local Claude Code settings
        permission_mode="dontAsk",  # deny anything not pre-approved instead of prompting
        max_turns=max_turns,
        env=full_env,
        stderr=lambda line: log.debug("cli: %s", line.rstrip()),
    )


def _redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def _describe(exc: BaseException) -> str:
    parts = [f"{type(exc).__name__}: {exc}"]
    for attr in ("errors", "api_error_status", "terminal_reason"):
        val = getattr(exc, attr, None)
        if val:
            parts.append(f"{attr}={val}")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
def _build_prompt(messages: list[dict[str, str]]) -> str:
    """The backend is stateless: the browser sends the whole transcript every turn."""
    *earlier, last = messages
    if not earlier:
        return last["content"]
    transcript = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in earlier
    )
    return f"Earlier conversation:\n{transcript}\n\nCurrent user message:\n{last['content']}"


def _flatten(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return json.dumps(content, indent=2, default=str)


def _events(msg: Any, prefix: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(msg, AssistantMessage):
        if msg.error:
            out.append({"type": "error", "message": f"Model call failed: {msg.error}. Check the model key and gateway."})
        for block in msg.content:
            if isinstance(block, TextBlock) and block.text.strip():
                out.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                out.append({
                    "type": "tool_call",
                    "id": block.id,
                    "name": block.name.removeprefix(prefix),
                    "input": block.input,
                })
    elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                out.append({
                    "type": "tool_result",
                    "id": block.tool_use_id,
                    "content": _flatten(block.content),
                    "is_error": bool(block.is_error),
                })
    elif isinstance(msg, ResultMessage):
        if msg.is_error or msg.subtype != "success":
            parts = [msg.result or "", *(msg.errors or [])]
            if msg.api_error_status:
                parts.append(f"HTTP {msg.api_error_status}")
            if msg.terminal_reason:
                parts.append(f"({msg.terminal_reason})")
            out.append({"type": "error", "message": " ".join(p for p in parts if p) or msg.subtype})
        else:
            out.append({"type": "done", "turns": msg.num_turns, "duration_ms": msg.duration_ms})
    return out


async def run_agent(
    settings: Settings,
    messages: list[dict[str, str]],
    model_key: str | None,
    mcp_key: str | None,
    agent_key: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one agent turn and stream normalized events."""
    env, headers, secrets = _credentials(settings, model_key, mcp_key, agent_key)
    options = _build_options(settings, env, headers, max_turns=settings.max_turns)
    prefix = f"mcp__{settings.mcp.server_name}__"
    finished = False

    try:
        async for msg in query(prompt=_build_prompt(messages), options=options):
            for event in _events(msg, prefix):
                if event["type"] in ("done", "error"):
                    if finished:
                        continue  # one terminal event per turn
                    finished = True
                if "message" in event:
                    event["message"] = _redact(event["message"], secrets)
                yield event
    except Exception as exc:  # noqa: BLE001 - surface anything to the UI
        if not finished:
            finished = True
            yield {"type": "error", "message": _redact(_describe(exc), secrets)}

    if not finished:
        yield {"type": "done", "turns": 0, "duration_ms": 0}
