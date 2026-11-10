"""A2A front door for the agent (a2a-sdk 0.3.x, served with the AgentCore SDK helper).

Run:  python -m app.a2a_server
Serves (port 9000 by default, which is also what AgentCore requires):
    GET  /.well-known/agent-card.json   agent card
    POST /                              JSON-RPC 2.0 (message/send, message/stream, ...)
    GET  /ping                          health check

The same process works on a laptop, in a Codespace, and in AgentCore Runtime.

Per request, the caller may send these headers (both optional, env fallbacks apply):
    X-Mcp-Key     the MCP API key (decides whose watchlist this is)
    X-Model-Key   the model/gateway key (ignored when the server is in direct Anthropic mode)

Conversation history: the server keeps no state. A caller may include prior turns as
a data part {"history": [{"role": "user"|"assistant", "content": "..."}]} next to the
text part; plain A2A clients just send text and get single-turn behavior.

Streaming: tool calls and results are sent as status updates whose message holds a
text part (human readable) and a data part {"event": "tool_call" | "tool_result", ...}.
The final answer is also sent as an artifact.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCard, DataPart, Part, TaskState, TextPart
from a2a.utils import new_task

from .agent import run_agent
from .auth import MissingKeyError, UnsupportedAuthMode
from .config import Settings, load_settings

log = logging.getLogger("a2a")

MODEL_KEY_HEADER = "x-model-key"
MCP_KEY_HEADER = "x-mcp-key"


# --------------------------------------------------------------------------- #
# Agent card
# --------------------------------------------------------------------------- #
def public_url() -> str:
    """URL advertised in the card. AgentCore overrides it with AGENTCORE_RUNTIME_URL."""
    explicit = os.environ.get("A2A_PUBLIC_URL", "").strip()
    if explicit:
        return explicit
    name = os.environ.get("CODESPACE_NAME")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    port = os.environ.get("A2A_PORT", "9000")
    if name and domain:
        return f"https://{name}-{port}.{domain}/"
    return f"http://localhost:{port}/"


def build_card() -> AgentCard:
    # Built from the spec's JSON field names so it validates the same way a client would read it.
    return AgentCard.model_validate({
        "name": "Stock Watchlist Agent",
        "description": (
            "Looks up stock quotes and manages a per-account watchlist (view, add, remove, clear) "
            "through tools hosted behind Axway AI Gateway."
        ),
        "url": public_url(),
        "version": "0.1.0",
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "stock-quotes",
                "name": "Stock quotes",
                "description": "Get the current quote for a stock symbol.",
                "tags": ["stocks", "quotes"],
                "examples": ["What is the quote for AAPL?"],
            },
            {
                "id": "view-watchlist",
                "name": "View watchlist",
                "description": "Show the symbols on the account's watchlist, with quotes if asked.",
                "tags": ["stocks", "watchlist"],
                "examples": ["Show my watchlist", "Show quotes for everything on my watchlist"],
            },
            {
                "id": "manage-watchlist",
                "name": "Manage watchlist",
                "description": "Add or remove symbols, or clear the whole watchlist.",
                "tags": ["stocks", "watchlist"],
                "examples": ["Add NVDA and TSLA to my watchlist", "Remove TSLA", "Clear my watchlist"],
            },
        ],
        "securitySchemes": {
            "mcpKey": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Mcp-Key",
                "description": "MCP API key. Each key has its own watchlist.",
            },
        },
        "security": [{"mcpKey": []}],
    })


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #
def _request_headers(context: RequestContext) -> dict[str, str]:
    """Lower-cased request headers, if the context builder captured them."""
    call_context = getattr(context, "call_context", None)
    state = getattr(call_context, "state", None) or {}
    headers = state.get("headers") or {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _messages_from(message: Any) -> list[dict[str, str]]:
    """Turn an A2A message into the [{role, content}] list that run_agent expects.

    Duck-typed on purpose: a text part has .text, a data part has .data.
    """
    history: list[dict[str, str]] = []
    texts: list[str] = []
    for part in getattr(message, "parts", None) or []:
        root = getattr(part, "root", part)
        data = getattr(root, "data", None)
        text = getattr(root, "text", None)
        if isinstance(data, dict) and isinstance(data.get("history"), list):
            for entry in data["history"]:
                if (
                    isinstance(entry, dict)
                    and entry.get("role") in ("user", "assistant")
                    and isinstance(entry.get("content"), str)
                ):
                    history.append({"role": entry["role"], "content": entry["content"]})
        elif isinstance(text, str):
            texts.append(text)
    last = "\n".join(texts).strip() or "Hello"
    return [*history, {"role": "user", "content": last}]


def _text_part(text: str) -> Part:
    return Part(root=TextPart(text=text))


def _data_part(data: dict[str, Any]) -> Part:
    return Part(root=DataPart(data=data))


class WatchlistExecutor(AgentExecutor):
    def __init__(self, settings: Settings):
        self.settings = settings

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task(context.message)
        if not context.current_task:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        headers = _request_headers(context)
        messages = _messages_from(context.message)
        names: dict[str, str] = {}
        answer: list[str] = []

        async def say(parts: list[Part]) -> None:
            await updater.update_status(TaskState.working, message=updater.new_agent_message(parts))

        try:
            async for ev in run_agent(
                self.settings, messages, headers.get(MODEL_KEY_HEADER), headers.get(MCP_KEY_HEADER)
            ):
                kind = ev["type"]
                if kind == "text":
                    answer.append(ev["text"])
                    await say([_text_part(ev["text"])])
                elif kind == "tool_call":
                    names[ev["id"]] = ev["name"]
                    await say([
                        _text_part(f"Calling {ev['name']}"),
                        _data_part({"event": "tool_call", "id": ev["id"], "name": ev["name"], "input": ev["input"]}),
                    ])
                elif kind == "tool_result":
                    name = names.get(ev["id"], "tool")
                    await say([
                        _text_part(f"{name} returned" + (" an error" if ev["is_error"] else "")),
                        _data_part({
                            "event": "tool_result",
                            "id": ev["id"],
                            "name": name,
                            "content": ev["content"],
                            "is_error": ev["is_error"],
                        }),
                    ])
                elif kind == "error":
                    await updater.failed(message=updater.new_agent_message([_text_part(ev["message"])]))
                    return
        except (MissingKeyError, UnsupportedAuthMode) as exc:
            await updater.failed(message=updater.new_agent_message([_text_part(str(exc))]))
            return

        if answer:
            await updater.add_artifact([_text_part("\n\n".join(answer))], name="answer")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def _context_builder():
    """Make sure request headers reach the executor (state['headers']).

    The AgentCore SDK's default builder forwards allowlisted headers into its own
    context. This wrapper additionally keeps the raw headers on the A2A call context.
    Returns None if the AgentCore builder can't be imported, in which case the
    a2a-sdk default (which also records headers) is used.
    """
    base = None
    for module in ("bedrock_agentcore.runtime.a2a", "bedrock_agentcore.runtime"):
        try:
            base = getattr(__import__(module, fromlist=["BedrockCallContextBuilder"]), "BedrockCallContextBuilder")
            break
        except (ImportError, AttributeError):
            continue
    if base is None:
        log.warning("BedrockCallContextBuilder not importable; using the default context builder.")
        return None

    class HeaderCapturingBuilder(base):  # type: ignore[misc, valid-type]
        def build(self, request):  # type: ignore[override]
            ctx = super().build(request)
            try:
                ctx.state.setdefault("headers", {k.lower(): v for k, v in request.headers.items()})
            except Exception:  # noqa: BLE001
                log.debug("could not attach headers to the call context", exc_info=True)
            return ctx

    return HeaderCapturingBuilder()


def main() -> None:
    from bedrock_agentcore.runtime import serve_a2a

    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    log.info("Model route: %s (%s)", settings.model.route, settings.model.name)

    kwargs: dict[str, Any] = {}
    builder = _context_builder()
    if builder is not None:
        kwargs["context_builder"] = builder
    host = os.environ.get("A2A_HOST", "").strip()
    if host:
        kwargs["host"] = host

    serve_a2a(WatchlistExecutor(settings), build_card(), **kwargs)


if __name__ == "__main__":
    main()
