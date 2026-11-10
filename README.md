# Stock Watchlist Agent

Demo agent on Axway AI Gateway. It talks to two gateway endpoints only (an Anthropic-compatible
model endpoint and the Fusion Stock Watchlist MCP server), with one optional exception: direct
Anthropic mode, below. The same agent code runs three ways:

| Run as | What it is | Port |
|---|---|---|
| Chat UI, `AGENT_MODE=direct` (default) | Web UI whose server runs the agent itself | 8000 |
| A2A agent | JSON-RPC A2A server with an agent card (`python -m app.a2a_server`) | 9000 |
| Chat UI, `AGENT_MODE=a2a` | Same UI, but its server is an A2A client for any A2A agent URL | 8000 |

It runs on your laptop, in a GitHub Codespace, and (the A2A agent) in AWS Bedrock AgentCore Runtime.
The UI shows every tool call (name, input, result, timing) as it happens.

## Direct mode vs A2A mode

The two UI modes look almost the same in the browser. The difference is where the agent runs and how the UI reaches it.

```
Direct mode:   Browser -> UI server (runs the agent inside it) -> model + MCP tools

A2A mode:      Browser -> UI server -> A2A agent (separate process) -> model + MCP tools
                         (just a client)
```

| | Direct mode | A2A mode |
|---|---|---|
| Where the agent runs | Inside the UI server, one process | In a separate A2A agent (port 9000, or AgentCore) |
| What the UI server does | Calls the model and MCP tools itself | Sends your message and keys to the A2A agent and shows what comes back |
| Sidebar | The MCP server's tools | The skills from the agent card |
| Wrong keys | Caught when you click Connect | Only found when you send a message |
| Model key field | Greyed out in direct Anthropic mode | Optional (blank = the agent's own setting) |
| Footer under an answer | Model turns and seconds | Seconds only |
| Needs | Model and MCP settings in the UI server's env | `A2A_AGENT_URL` (the agent holds the model and MCP settings) |

**Use direct mode** for everyday demos: one process, and Connect checks both keys and lists the tools.
**Use A2A mode** to show A2A itself, or when the agent is deployed elsewhere (AgentCore is only reachable over A2A).
Other A2A clients can discover and call the agent through its card; direct mode keeps the agent hidden inside the web app.

## Quick start (laptop)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in the gateway route (or ANTHROPIC_API_KEY), and MCP_URL
uvicorn app.server:app --port 8000            # chat UI, direct mode
python -m app.a2a_server                      # optional, in a second terminal: the A2A agent on :9000
```

Open http://localhost:8000, enter the **Model API key** and **MCP API key** (or leave a field blank to use a
server-side default), and click Connect.

## Run in a GitHub Codespace

### One-time setup

1. **Push the project to a GitHub repo.** Check with `git status` that `.env` is not listed (it is gitignored).
2. **Decide where configuration lives.** Either works:
   - *Codespaces secrets* (set once, every new Codespace gets them): GitHub account **Settings > Codespaces >
     Secrets**, create `MODEL_BASE_URL`, `MODEL_NAME`, `MCP_URL` (gateway route), and optionally
     `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL` (direct route). Give each access to this repo.
   - *A `.env` file inside the Codespace* (per Codespace, gone when it is deleted): `cp .env.example .env` and edit.
     If the same variable is set both ways, the secret wins.

### Start it

3. **Create the Codespace:** repo page > **Code > Codespaces > Create codespace on main**. Wait for the build.
4. **Two processes start by themselves**: the chat UI on port 8000 and the A2A agent on port 9000. Confirm:
   ```bash
   pgrep -af "uvicorn|a2a_server"      # should list both
   tail -n 5 /tmp/uvicorn.log /tmp/a2a.log
   ```
   `/tmp/uvicorn.log` should say `Mode: direct` and the model route. If a process is missing, the log says why;
   the usual cause is a missing setting (the apps refuse to start without a model route and `MCP_URL`).
   If you set up `.env` after the Codespace started, restart them (see "Restart" below).
5. **Make the ports private** before anything else, because your saved keys act as defaults for anyone who can open
   a port: bottom panel **Ports** tab > right-click 8000 and 9000 > **Port Visibility > Private**.
   (Private is the default; if a port shows a globe icon it is public.)

### Use it

6. **Open the chat UI:** Ports tab > port 8000 > globe icon (Open in Browser). Sign in to GitHub if asked.
7. **Connect.** Direct Anthropic mode greys out the model key field. Leave the MCP key blank to use the saved default,
   or type another key to demo another account. After Connect you see the six MCP tools, and the
   **Agent card** panel on the left shows the A2A agent's card URL with Copy and Open buttons and a live status dot.
8. **Chat.** Click a suggested prompt (blue = reads data, amber = changes it) or type your own.

### Check the A2A agent

9. **Agent card:**
   ```bash
   curl -s http://localhost:9000/.well-known/agent-card.json | grep -o '"url":"[^"]*"'
   ```
   If this prints `http://localhost:9000/`, the SDK overwrote the card address (see "Fix the card address").
10. **Send a message over A2A** (the probe needs these two variables because `.env` says `AGENT_MODE=direct`):
    ```bash
    AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/ python -m app.a2a_client "Show my watchlist"
    ```
    You should see `>` lines for tool calls, `<` lines for results, then the answer.

### Fix the card address

The AgentCore SDK rewrites the card's `url` to its own local address unless `AGENTCORE_RUNTIME_URL` is set.
In `.env` add (note `-9000`, the A2A port; your Codespace name is the first part of the URL in your browser):

```
AGENTCORE_RUNTIME_URL=https://<codespace-name>-9000.app.github.dev/
```

then restart the A2A server. The address changes if you create a new Codespace. The UI's Agent card panel warns
when the card still advertises `localhost`.

### Run the UI as an A2A client

```bash
pkill -f "uvicorn app.server"
AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/ nohup uvicorn app.server:app --host 0.0.0.0 --port 8000 > /tmp/uvicorn.log 2>&1 &
```

Reload the page and Connect: the sidebar now lists skills and the header says `... via A2A`. To go back to direct mode,
repeat without the two variables. Do not `export` them: an exported `AGENT_MODE=a2a` makes the next UI restart start in A2A mode.

### Restart (after editing `.env` or a secret)

Secrets are read when the Codespace starts, and `.env` when each process starts. Restart both processes:

```bash
pkill -f "uvicorn app.server"; pkill -f "app.a2a_server"
nohup uvicorn app.server:app --host 0.0.0.0 --port 8000 > /tmp/uvicorn.log 2>&1 &
nohup python -m app.a2a_server > /tmp/a2a.log 2>&1 &
```

Or restart the Codespace. Stopping a Codespace (Code > Codespaces menu) pauses it and keeps its files; deleting it removes
`.env` and logs. Both processes start again automatically when it resumes.

### Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Ports tab has no 8000 or 9000 | The process did not start. `tail /tmp/uvicorn.log /tmp/a2a.log` for the missing setting. |
| Page loads but Connect shows an MCP error | Wrong or missing MCP key, or the wrong `MCP_TRANSPORT` (try `sse`). |
| Connect shows a model error | Bad model key, or an unknown model name for the route in use. |
| Agent card panel says "A2A server not running" | Start it: `nohup python -m app.a2a_server > /tmp/a2a.log 2>&1 &`. |
| Card advertises `localhost` | Set `AGENTCORE_RUNTIME_URL` as above and restart the A2A server. |
| Probe fails with `Missing required environment variable: A2A_AGENT_URL` | Pass `AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/` on the command line. |
| UI starts in the wrong mode | An exported `AGENT_MODE` is set. `unset AGENT_MODE A2A_AGENT_URL`, then restart the UI. |

## Model route: gateway or direct Anthropic

- **Gateway (default):** leave `ANTHROPIC_API_KEY` unset. Set `MODEL_BASE_URL` (root, no `/v1/messages`) and
  `MODEL_NAME`. The gateway key comes from the UI, an `X-Model-Key` header, or `MODEL_API_KEY`.
- **Direct to Anthropic:** set `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` (an Anthropic model ID, for example
  `claude-haiku-5-5` for a low-cost option). The agent calls Anthropic directly and **bypasses the gateway**. Keys sent
  by callers are ignored and the UI disables the model key field. Use it as a dev fallback; leave it unset for
  gateway demos and AgentCore.
- `ANTHROPIC_API_KEY` is a common variable name. Check your shell does not already set it, or the agent will go
  direct without you asking. The UI header and the startup log both show the active route.

## The A2A agent

```bash
python -m app.a2a_server
curl http://localhost:9000/.well-known/agent-card.json
AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/ python -m app.a2a_client "Show my watchlist"   # CLI probe
```

- **Agent card** at `/.well-known/agent-card.json`, with three skills and the `X-Mcp-Key` header declared.
- **Keys per request:** `X-Mcp-Key` (whose watchlist) and `X-Model-Key` (gateway key). Both optional, with the
  same env fallbacks as the UI. The probe reads `MCP_API_KEY` and `MODEL_API_KEY` from the environment.
- **Stateless:** each message stands alone. A caller can add prior turns as a data part
  `{"history": [{"role": "user"|"assistant", "content": "..."}]}` beside the text part; the web UI does this.
- **Streaming:** `message/stream` sends status updates for each tool call and result (a text part plus a data part
  `{"event": "tool_call"|"tool_result", ...}`), then the final answer as an artifact.
- Uses a2a-sdk 0.3.x and the AgentCore SDK's `serve_a2a`, which serves `/ping` and the card.

For a remote agent, the UI uses `A2A_AUTH_MODE`: `bearer` (+ `A2A_BEARER_TOKEN`) or `sigv4` (+ `A2A_REGION`, AWS credentials in your shell).

## Docker

```bash
docker build -t stock-watchlist-agent .                                  # chat UI
docker run --rm -p 8000:8000 --env-file .env stock-watchlist-agent
docker build -f Dockerfile.agentcore -t stock-watchlist-a2a .            # A2A agent
docker run --rm -p 9000:9000 --env-file .env stock-watchlist-a2a
```

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
| `app/static/index.html` | Single-file chat UI (renders markdown tables, tool-call timeline, agent card panel) |
| `Dockerfile`, `Dockerfile.agentcore` | UI image, and the ARM64 A2A image for AgentCore |
| `deploy/` | ECR build/push script and the AgentCore create/update script |
| `.devcontainer/` | Codespaces setup |

## Verification status

Tested by hand in a GitHub Codespace, direct Anthropic route: the agent card is served (and its address
is fixed by `AGENTCORE_RUNTIME_URL`); the CLI probe reads and writes the watchlist over A2A with streamed tool calls;
the chat UI works in both direct and A2A modes.

Not yet verified:

- **Per-request key header:** that `X-Mcp-Key` overrides the server default. Test with
  `AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/ MCP_API_KEY=wrong-key python -m app.a2a_client "Show my watchlist"`:
  it should fail. If it prints a watchlist, the header is being ignored. This matters for AgentCore.
- **Gateway model route:** needs `/v1/messages`, streaming, tool use, `x-api-key`, and your `MODEL_NAME`.
  If it rejects `anthropic-beta` headers, keep `MODEL_DISABLE_BETAS=1`.
- **AgentCore:** that the SDK's bundled CLI starts in an ARM64 AgentCore container, that the runtime can reach your
  gateway (and Anthropic in direct mode), header forwarding with the allowlist, and `sigv4`/`bearer` calls from the UI.
- **MCP transport `sse`:** only `http` has been exercised.
- **Read/write labels** in the UI are a name heuristic (`Get*` = read). Tool input schemas are not shown in the sidebar.
- **Gateway for A2A:** no Axway documentation found on fronting an A2A agent with AI Gateway, so inbound A2A calls
  reach the agent directly. The agent's outbound calls (model, MCP) still go through the gateway.
