# Stock Watchlist Agent

Demo agent on Axway AI Gateway. It talks to two gateway endpoints only (an Anthropic-compatible
model endpoint and the Fusion Stock Watchlist MCP server), with one optional exception: direct
Anthropic mode, below. It runs three ways, and the same agent code is behind all of them:

| Run as | What it is | Port |
|---|---|---|
| Chat UI, `AGENT_MODE=direct` | Web UI whose server runs the agent itself | 8000 |
| A2A agent | JSON-RPC A2A server with an agent card (`python -m app.a2a_server`) | 9000 |
| Chat UI, `AGENT_MODE=a2a` | Same UI, but its server is an A2A client for any A2A agent URL | 8000 |

The A2A agent runs on your laptop, in a Codespace, and in AWS Bedrock AgentCore Runtime.
The UI shows every tool call (name, input, result) as it happens in all modes.

## Quick start (laptop)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in the gateway route (or ANTHROPIC_API_KEY), and MCP_URL
uvicorn app.server:app --port 8000            # chat UI, runs the agent itself
python -m app.a2a_server                      # optional, in another terminal: the A2A agent on :9000
```

Open http://localhost:8000, paste the **Model API key** and the **MCP API key**, and click Connect.

## Model route: gateway or direct Anthropic

- **Gateway (default):** leave `ANTHROPIC_API_KEY` unset. Set `MODEL_BASE_URL` (root, no `/v1/messages`)
  and `MODEL_NAME`. The gateway key comes from the UI, an `X-Model-Key` header, or `MODEL_API_KEY`.
- **Direct to Anthropic:** set `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` (an Anthropic model ID). The agent
  calls Anthropic directly and **bypasses the gateway**. Keys sent by callers are ignored and the UI disables
  the model key field. Use this as a dev fallback; leave it unset for gateway demos and AgentCore.
- `ANTHROPIC_API_KEY` is a common variable name. Check your shell does not already set it, or the
  agent will go direct without you asking. The UI header and the startup log both show the active route.

## The A2A agent

```bash
python -m app.a2a_server
curl http://localhost:9000/.well-known/agent-card.json
MCP_API_KEY=<key> MODEL_API_KEY=<key> python -m app.a2a_client "Show my watchlist"   # CLI probe
```

- **Agent card** at `/.well-known/agent-card.json`, with three skills and the `X-Mcp-Key` header declared.
- **Keys per request:** `X-Mcp-Key` (whose watchlist) and `X-Model-Key` (gateway key). Both optional, with the
  same env fallbacks as the UI.
- **Stateless:** each message stands alone. A caller can add prior turns as a data part
  `{"history": [{"role": "user"|"assistant", "content": "..."}]}` beside the text part; the web UI does this.
- **Streaming:** `message/stream` sends status updates for each tool call and result (a text part plus a data part
  `{"event": "tool_call"|"tool_result", ...}`), then the final answer as an artifact.
- Uses a2a-sdk 0.3.x and the AgentCore SDK's `serve_a2a`, which serves `/ping` and the card, and reads
  `AGENTCORE_RUNTIME_URL` when deployed.

## Chat UI as an A2A client

```bash
AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/ uvicorn app.server:app --port 8000
```

Connect fetches the agent card and lists its skills (the UI server can't see the MCP tools in this mode).
Your keys are forwarded to the agent as headers; a wrong key shows up as an error on the first message.
For a remote agent set `A2A_AUTH_MODE` to `bearer` (+ `A2A_BEARER_TOKEN`) or `sigv4` (+ `A2A_REGION`, AWS credentials in your shell).

## Docker

```bash
docker build -t stock-watchlist-agent .                                  # chat UI
docker run --rm -p 8000:8000 --env-file .env stock-watchlist-agent
docker build -f Dockerfile.agentcore -t stock-watchlist-a2a .            # A2A agent
docker run --rm -p 9000:9000 --env-file .env stock-watchlist-a2a
```

## Run in a GitHub Codespace

The repo is public, so gateway details stay out of it: they come from Codespaces secrets, and API keys are typed in the UI.

1. Push the project to a GitHub repo.
2. In your GitHub account go to **Settings > Codespaces > Secrets** and create secrets, each with access to this repo:
   `MODEL_BASE_URL`, `MODEL_NAME`, `MCP_URL` (gateway route), and optionally `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL`
   (direct route).
3. **Code > Codespaces > Create codespace on main.** The chat UI (8000) and the A2A agent (9000) both start.
4. Enter your keys in the UI and click Connect. To exercise the A2A path in the UI, set `AGENT_MODE=a2a` and
   `A2A_AGENT_URL=http://localhost:9000/` in the terminal, then restart the UI (`pkill -f "uvicorn app.server"`).

Keep the forwarded ports private (the default): anyone who can open them can use the app. Logs are in
`/tmp/uvicorn.log` and `/tmp/a2a.log`; the usual cause of a missing app is a missing secret. Secrets are read at
Codespace start, so restart the Codespace after changing one.

## Deploy to AWS Bedrock AgentCore Runtime

AgentCore needs an **ARM64** container serving A2A on **port 9000**; `Dockerfile.agentcore` is built for that.

```bash
export AWS_REGION=us-east-1
./deploy/build_and_push.sh                      # builds linux/arm64, pushes to ECR, prints the image URI

# set the runtime's configuration in your shell (same names as .env.example), then:
export MODEL_BASE_URL=... MODEL_NAME=... MODEL_API_KEY=... MCP_URL=...     # or ANTHROPIC_API_KEY + ANTHROPIC_MODEL
python deploy/agentcore_deploy.py --image <image-uri> --role-arn <execution-role-arn> --region $AWS_REGION
```

- The script creates the runtime with protocol A2A, public network mode, IAM (SigV4) auth for callers, and the
  request header allowlist `X-Mcp-Key` + `X-Model-Key`, so each caller's MCP key reaches the agent.
  Add `--jwt-discovery-url` and `--jwt-allowed-client` for a JWT authorizer instead (it also allowlists `Authorization`).
  Use `--update <runtime-id>` to redeploy.
- You create the IAM execution role yourself (see the AgentCore docs, "Runtime permissions").
- Values you export become the runtime's environment variables, which are readable by anyone who can
  read the runtime configuration. Fine for a demo with throwaway keys; move them to Secrets Manager later.
- The script prints the `A2A_AGENT_URL` to use. Then point the chat UI at it:
  `AGENT_MODE=a2a A2A_AGENT_URL=<url> A2A_AUTH_MODE=sigv4 A2A_REGION=$AWS_REGION uvicorn app.server:app --port 8000`.

## Layout

| File | Role |
|---|---|
| `app/config.py` | Env vars only, one config object per endpoint |
| `app/auth.py` | The single place that builds credentials (OAuth would be added here) |
| `app/agent.py` | Agent logic, no web imports: `connect()` and `run_agent()` |
| `app/a2a_server.py` | A2A agent card, executor, and entrypoint |
| `app/a2a_client.py` | Minimal A2A client used by the UI, plus a CLI probe |
| `app/server.py` | Thin FastAPI layer for the chat UI |
| `app/static/index.html` | Single-file chat UI |
| `Dockerfile`, `Dockerfile.agentcore` | UI image, and the ARM64 A2A image for AgentCore |
| `deploy/` | ECR build/push script and the AgentCore create/update script |
| `.devcontainer/` | Codespaces setup |

## Not yet verified (I could not run any of this against your gateway or AWS)

- **MCP transport:** `MCP_TRANSPORT` defaults to `http`; if Connect fails with a correct key, try `sse`.
- **Gateway model endpoint:** needs `/v1/messages`, streaming, tool use, `x-api-key`, and your `MODEL_NAME`.
  If it rejects `anthropic-beta` headers, keep `MODEL_DISABLE_BETAS=1`.
- **A2A SDK calls:** the executor uses `TaskUpdater.update_status`, `new_agent_message`, `failed`, `cancel`,
  and the `a2a-sdk` 0.3.x types. If the server errors at startup or on the first message, start there.
- **Header forwarding:** the A2A server reads `X-Mcp-Key` / `X-Model-Key` from the request headers. Locally
  this should just work. On AgentCore it depends on the runtime allowlist (set by the deploy script) and an
  `bedrock-agentcore` version that forwards custom headers to A2A containers.
- **AgentCore container:** that the Python SDK's bundled CLI starts inside an ARM64 AgentCore container, and that
  the runtime can reach your gateway and (in direct mode) Anthropic over the public internet.
- **SigV4 to AgentCore:** `A2A_AUTH_MODE=sigv4` signs with service name `bedrock-agentcore`. If AgentCore returns 403,
  test with the AWS CLI/console first, or use a JWT authorizer with `bearer`.
- **Read/write labels** in the UI are a name heuristic (`Get*` = read); **tool schemas** are not shown in the sidebar.
- **Gateway for A2A:** I found no Axway documentation on fronting an A2A agent with AI Gateway, so inbound A2A calls
  reach the agent directly. The agent's outbound calls (model, MCP) still go through the gateway.
