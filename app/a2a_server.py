"""A2A front door for the agent: A2A protocol 1.0 only (a2a-sdk 1.x, app built with the AgentCore SDK's build_a2a_app).

Run:  python -m app.a2a_server
Serves (port 9000 by default, which is also what AgentCore requires):
    GET  /.well-known/agent-card.json   agent card
    POST /                              JSON-RPC 2.0 (SendMessage, SendStreamingMessage, GetTask, ...)
    GET  /ping                          health check

The same process works on a laptop, in a Codespace, and in AgentCore Runtime.

Per request, the caller may send these headers (the keys are optional, env fallbacks apply):
    X-Mcp-Key     the MCP API key (decides whose watchlist this is). When absent or blank the agent uses
                  MCP_API_KEY from its environment, and when that is not set either, the caller's own
                  x-api-key (so the key used to reach the agent doubles as the MCP key).
    X-Model-Key   the model/gateway key (ignored when the server is in direct Anthropic mode)

Protocol: A2A 1.0 only. Callers send the header "A2A-Version: 1.0" on every JSON-RPC call (the A2A spec tells a server to
assume 0.3 for a call without it). A 0.3 client no longer works against this agent.

Access control: when AGENT_API_KEYS is set, every request except /ping and the agent card must
carry a valid x-api-key header (HTTP 401 otherwise). With AGENT_REQUIRE_API_KEY=1 the server
refuses to start without a key.

Browsers: the web page in web/ calls this server directly, so the app answers CORS preflights and sends
CORS headers (any origin: access is by header key, not cookies). See main() for why the order matters.

Conversation history: the server keeps no state. A caller may include prior turns as
a data part {"data": {"history": [{"role": "user"|"assistant", "content": "..."}]}} next to the
text part {"text": "..."}; plain A2A clients just send text and get single-turn behavior.

Streaming: a task stream, as A2A 1.0 requires: the Task first, then status updates and one artifact, never a
bare Message. Tool calls and results are status updates (state TASK_STATE_WORKING) whose message holds a
text part (human readable) and a data part {"event": "tool_call" | "tool_result", ...}.
Once at the start of a run, a data part {"event": "mcp_status", "servers": [...], "tools": [...]}
says whether the MCP server connected and which tools loaded.
The final answer is sent as an artifact, then the task ends in TASK_STATE_COMPLETED (or TASK_STATE_FAILED).
"""
from __future__ import annotations

import inspect
import logging
import os
from typing import Any

from a2a.helpers import new_message, new_task_from_user_message, new_text_artifact_update_event
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCard, Part, TaskState, TaskStatus, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value
from starlette.middleware.cors import CORSMiddleware

from .agent import run_agent
from .auth import ApiKeyMiddleware, MissingKeyError, UnsupportedAuthMode
from .config import Settings, load_inbound_auth, load_settings

log = logging.getLogger("a2a")

MODEL_KEY_HEADER = "x-model-key"
MCP_KEY_HEADER = "x-mcp-key"
AGENT_KEY_HEADER = "x-api-key"
SESSION_HEADER = "x-amzn-bedrock-agentcore-runtime-session-id"
VERSION_HEADER = "a2a-version"   # A2A 1.0: sent by the caller on every JSON-RPC call ("A2A-Version: 1.0")

# Request headers a browser page may send to the agent (lower case; the CORS preflight is checked against these).
CORS_ALLOWED_HEADERS = [
    "content-type", "accept", AGENT_KEY_HEADER, MCP_KEY_HEADER, MODEL_KEY_HEADER, SESSION_HEADER, VERSION_HEADER,
]


# --------------------------------------------------------------------------- #
# Agent card
# --------------------------------------------------------------------------- #
def public_url() -> str:
    """URL advertised in the card: A2A_PUBLIC_URL, else this Render service's own address, else this Codespace's
    forwarded address, else localhost. AgentCore overrides it with AGENTCORE_RUNTIME_URL."""
    explicit = os.environ.get("A2A_PUBLIC_URL", "").strip()
    if explicit:
        return explicit
    render = os.environ.get("RENDER_EXTERNAL_URL", "").strip()  # set by Render for a web service, e.g. https://myapp.onrender.com
    if render:
        return render.rstrip("/") + "/"
    name = os.environ.get("CODESPACE_NAME")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    port = os.environ.get("A2A_PORT", "").strip() or "9000"  # blank counts as unset
    if name and domain:
        return f"https://{name}-{port}.{domain}/"
    return f"http://localhost:{port}/"


def build_card(auth_required: bool = False, model_note: str = "") -> AgentCard:
    # A2A 1.0 card: the address lives in supportedInterfaces (there is no top-level url any more), and security uses
    # securitySchemes plus securityRequirements. It is built from the spec's JSON field names with ParseDict, which
    # rejects a field name the installed SDK does not know, so a mismatch fails at startup and names the field.
    card = {
        "name": "Stock Watchlist Agent",
        "description": (
            "Looks up stock quotes and manages a per-account watchlist (view, add, remove, clear) "
            "through tools hosted behind Axway AI Gateway."
            + (f" Model: {model_note}." if model_note else "")
        ),
        "supportedInterfaces": [
            {"url": public_url(), "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
        ],
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
            "agentKey": {
                "apiKeySecurityScheme": {
                    "location": "header",
                    "name": "x-api-key",
                    "description": "Agent API key. Required on every call except the agent card and /ping.",
                },
            },
            "mcpKey": {
                "apiKeySecurityScheme": {
                    "location": "header",
                    "name": "X-Mcp-Key",
                    "description": (
                        "Optional MCP API key. Each key has its own watchlist. When absent, the agent uses its "
                        "MCP_API_KEY, and when that is not set, the x-api-key of the call."
                    ),
                },
            },
        },
    }
    if auth_required:
        card["securityRequirements"] = [{"schemes": {"agentKey": {}}}]
    return ParseDict(card, AgentCard())


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
    """Turn an A2A 1.0 message into the [{role, content}] list that run_agent expects.

    A 1.0 Part is one flat message: it has a text, or a data value (a protobuf Value), and no "kind".
    """
    history: list[dict[str, str]] = []
    texts: list[str] = []
    for part in getattr(message, "parts", None) or []:
        if part.HasField("data"):
            data = MessageToDict(part.data)
            if isinstance(data, dict) and isinstance(data.get("history"), list):
                for entry in data["history"]:
                    if (
                        isinstance(entry, dict)
                        and entry.get("role") in ("user", "assistant")
                        and isinstance(entry.get("content"), str)
                    ):
                        history.append({"role": entry["role"], "content": entry["content"]})
        elif part.HasField("text"):
            texts.append(part.text)
    last = "\n".join(texts).strip() or "Hello"
    return [*history, {"role": "user", "content": last}]


def _text_part(text: str) -> Part:
    return Part(text=text)


def _data_part(data: dict[str, Any]) -> Part:
    return Part(data=ParseDict(data, Value()))   # a 1.0 data part holds a protobuf Value, not a dict


def _status_event(task: Any, state: Any, parts: list[Part] | None = None) -> TaskStatusUpdateEvent:
    """A status update for the task, with an agent message made of `parts` when there are any."""
    status: dict[str, Any] = {"state": state}
    if parts:
        status["message"] = new_message(parts=parts, context_id=task.context_id, task_id=task.id)
    return TaskStatusUpdateEvent(task_id=task.id, context_id=task.context_id, status=TaskStatus(**status))


class WatchlistExecutor(AgentExecutor):
    def __init__(self, settings: Settings):
        self.settings = settings

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # A2A 1.0 task stream: the Task is the first event, then only status and artifact updates (the SDK rejects a
        # stream that mixes in a Message or sends an update before the Task).
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)

        headers = _request_headers(context)
        if not headers:
            log.warning("No request headers reached the executor: X-Mcp-Key, X-Model-Key and the x-api-key fallback "
                        "for the MCP key cannot be used for this run.")
        messages = _messages_from(context.message)
        names: dict[str, str] = {}
        answer: list[str] = []

        async def send(state: Any, parts: list[Part] | None = None) -> None:
            await event_queue.enqueue_event(_status_event(task, state, parts))

        async def say(parts: list[Part]) -> None:
            await send(TaskState.TASK_STATE_WORKING, parts)

        try:
            async for ev in run_agent(
                self.settings,
                messages,
                headers.get(MODEL_KEY_HEADER),
                headers.get(MCP_KEY_HEADER),
                headers.get(AGENT_KEY_HEADER),
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
                elif kind == "mcp_status":
                    summary = ", ".join(f"{x['name']} {x['status']}" for x in ev["servers"]) or "no MCP servers reported"
                    await say([
                        _text_part(f"MCP: {summary}; {len(ev['tools'])} tool(s)"),
                        _data_part({
                            "event": "mcp_status",
                            "servers": ev["servers"],
                            "tools": ev["tools"],
                        }),
                    ])
                elif kind == "error":
                    await send(TaskState.TASK_STATE_FAILED, [_text_part(ev["message"])])
                    return
        except (MissingKeyError, UnsupportedAuthMode) as exc:
            await send(TaskState.TASK_STATE_FAILED, [_text_part(str(exc))])
            return

        if answer:
            await event_queue.enqueue_event(new_text_artifact_update_event(
                task_id=task.id, context_id=task.context_id, name="answer", text="\n\n".join(answer), last_chunk=True,
            ))
        await send(TaskState.TASK_STATE_COMPLETED)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task:
            await event_queue.enqueue_event(_status_event(task, TaskState.TASK_STATE_CANCELED))


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def _sdk_attr(name: str):
    """Import a name from the AgentCore SDK, tolerating the module it lives in changing."""
    for module in ("bedrock_agentcore.runtime.a2a", "bedrock_agentcore.runtime"):
        try:
            return getattr(__import__(module, fromlist=[name]), name)
        except (ImportError, AttributeError):
            continue
    return None


def _context_builder():
    """Make sure request headers reach the executor (state['headers']).

    The AgentCore SDK's default builder forwards allowlisted headers into its own
    context. This wrapper additionally keeps the raw headers on the A2A call context.
    Returns None if the AgentCore builder can't be imported, in which case the
    a2a-sdk default (which also records headers) is used.
    """
    base = _sdk_attr("BedrockCallContextBuilder")
    if base is None:
        log.warning("BedrockCallContextBuilder not importable; using the default context builder.")
        return None

    def attach_headers(ctx, request):
        try:
            ctx.state.setdefault("headers", {k.lower(): v for k, v in request.headers.items()})
        except Exception:  # noqa: BLE001
            log.debug("could not attach headers to the call context", exc_info=True)
        return ctx

    class HeaderCapturingBuilder(base):  # type: ignore[misc, valid-type]
        def build(self, request):  # type: ignore[override]
            ctx = super().build(request)
            if inspect.isawaitable(ctx):   # the 1.x builder interface may be async; keep whichever shape the base has
                async def attach_when_ready():
                    return attach_headers(await ctx, request)
                return attach_when_ready()
            return attach_headers(ctx, request)

    return HeaderCapturingBuilder()


def _bind_host() -> str:
    """A2A_HOST, else 0.0.0.0 inside a container and 127.0.0.1 elsewhere (the same rule serve_a2a uses)."""
    host = os.environ.get("A2A_HOST", "").strip()
    if host:
        return host
    in_container = os.path.exists("/.dockerenv") or bool(os.environ.get("DOCKER_CONTAINER"))
    return "0.0.0.0" if in_container else "127.0.0.1"


class _QuietPing(logging.Filter):
    """Leave successful GET /ping health checks out of uvicorn's access log: Render and AgentCore each send one every few
    seconds. A failing check, and every other request, is still logged. A record that is not shaped like uvicorn's access
    line ('%s - "%s %s HTTP/%s" %d': client, method, path, version, status) is kept."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            _client, method, path, _version, status = record.args
        except (TypeError, ValueError):
            return True
        return not (method == "GET" and str(path).split("?", 1)[0] == "/ping" and str(status) == "200")


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    access_log = logging.getLogger("uvicorn.access")   # uvicorn keeps a filter added to this logger when it sets up logging
    if not any(isinstance(f, _QuietPing) for f in access_log.filters):
        access_log.addFilter(_QuietPing())
    settings = load_settings()
    inbound = load_inbound_auth()  # raises if AGENT_REQUIRE_API_KEY is set without keys
    log.info("Model route: %s (%s)", settings.model.route, settings.model.name)

    # The AgentCore SDK rewrites the card's address with AGENTCORE_RUNTIME_URL when it is set, and
    # otherwise with a local address. Where we know a public address (A2A_PUBLIC_URL, the Render
    # service address, or the Codespaces forwarded address), publish it that way. On AgentCore the variable is already set.
    # (The card also carries that address itself, in supportedInterfaces: see build_card.)
    url = public_url()
    if not url.startswith("http://localhost"):
        os.environ.setdefault("AGENTCORE_RUNTIME_URL", url)

    build_a2a_app = _sdk_attr("build_a2a_app")
    if build_a2a_app is None:
        raise RuntimeError("bedrock-agentcore[a2a-v1] is not installed or does not provide build_a2a_app.")
    kwargs: dict[str, Any] = {}
    builder = _context_builder()
    if builder is not None:
        kwargs["context_builder"] = builder

    model_note = f"{settings.model.name}, " + ("direct to Anthropic" if settings.model.direct else "through the gateway")
    card = build_card(auth_required=bool(inbound.keys), model_note=model_note)
    app = build_a2a_app(WatchlistExecutor(settings), card, **kwargs)

    if inbound.keys:
        app.add_middleware(ApiKeyMiddleware, keys=list(inbound.keys))
        log.info("x-api-key required on every call except /ping and the agent card (%d key%s).",
                 len(inbound.keys), "" if len(inbound.keys) == 1 else "s")
        if any(len(k) < 16 for k in inbound.keys):
            log.warning("An AGENT_API_KEYS entry is shorter than 16 characters; use a longer random key.")
    else:
        log.warning("AGENT_API_KEYS is not set: this A2A agent is open to anyone who can reach it.")

    # The web page calls this agent straight from the browser, so answer CORS. Starlette treats the middleware added
    # last as the outermost layer, so this wraps the key check: a preflight (which carries no key) is answered before
    # the check, and the check's 401 still carries CORS headers, so the page can read it and say the key was wrong.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=CORS_ALLOWED_HEADERS,
        expose_headers=["*"],   # so the page's Events screen can show the response headers
        max_age=600,
    )

    port = int(os.environ.get("A2A_PORT", "").strip() or 9000)
    if port != 9000:
        log.warning("Serving on port %d. AgentCore only reaches A2A servers on port 9000.", port)
    log.info("Serving A2A 1.0 only (JSON-RPC at /). Callers must send the header A2A-Version: 1.0.")
    uvicorn.run(app, host=_bind_host(), port=port)


if __name__ == "__main__":
    main()
