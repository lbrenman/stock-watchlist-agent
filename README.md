# Stock Watchlist Agent

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/lbrenman/stock-watchlist-agent)

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
| Wrong keys | Caught when you click Connect | MCP and model keys only when you send a message; the Agent API key at Connect |
| Agent API key | Checked by the UI server | Forwarded to the A2A agent, which checks it |
| Model key field | Greyed out in direct Anthropic mode | Optional (blank = the agent's own setting) |
| Footer under an answer | Model turns and seconds | Seconds only |
| Needs | Model and MCP settings in the UI server's env | `A2A_AGENT_URL` (the agent holds the model and MCP settings) |

**Use direct mode** for everyday demos: one process, and Connect checks every key and lists the tools.
**Use A2A mode** to show A2A itself, or when the agent is deployed elsewhere (AgentCore is only reachable over A2A).
Other A2A clients can discover and call the agent through its card; direct mode keeps the agent hidden inside the web app.

## Securing the agent: x-api-key

Set `AGENT_API_KEYS` and callers must send `x-api-key: <key>`:

- **A2A agent (port 9000):** every call needs the key except the agent card and `/ping` (AgentCore's health check).
  Missing or wrong key gives HTTP 401.
- **Chat UI server in direct mode (port 8000):** `/api/connect` and `/api/chat` need it. The connect screen
  shows an **Agent API key** field when the server requires one.
- **Chat UI in A2A mode:** the UI does not check the key itself. It forwards the field's value (or `A2A_API_KEY`)
  to the remote agent, and Connect verifies it without calling the model.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"      # generate a key
```

| Variable | Meaning |
|---|---|
| `AGENT_API_KEYS` | One key, or several separated by commas (add the new key, move callers over, then remove the old one) |
| `AGENT_REQUIRE_API_KEY=1` | Refuse to start without a key. The Codespaces config sets this. Unset: no keys means open access, with a warning in the log |
| `A2A_API_KEY` | The key the UI (in A2A mode) and the CLI probe send to the agent |

```bash
curl -i http://localhost:9000/ -H "Content-Type: application/json" -d '{}'                  # 401 without a key
curl -s http://localhost:9000/.well-known/agent-card.json | head -c 200                      # card is open
A2A_API_KEY=<key> AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/ python -m app.a2a_client "Show my watchlist"
```

The key is compared in constant time, read only from the `x-api-key` header, and never logged. This is a shared-secret
gate for a demo, not a full identity system: no per-key permissions, no rate limiting. Keys live in environment variables,
so on AgentCore anyone who can read the runtime configuration can read them (use Secrets Manager beyond a demo).

## Quick start (laptop)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in the gateway route (or ANTHROPIC_API_KEY), MCP_URL, and AGENT_API_KEYS
bash scripts/start.sh       # starts the chat UI on :8000 and the A2A agent on :9000
```

(Or run them yourself: `uvicorn app.server:app --port 8000` and `python -m app.a2a_server`.)
Open http://localhost:8000, enter the keys the form asks for (leave a field blank to use a server-side default),
and click Connect.

## Run in a GitHub Codespace

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/lbrenman/stock-watchlist-agent)

The button creates a Codespace from this repo. Your Codespaces secrets are private to your own account, so anyone else
who clicks it gets their own Codespace without your keys and must supply their own configuration.

### One-time setup

1. **Push the project to a GitHub repo.** Check with `git status` that `.env` is not listed (it is gitignored).
2. **Decide where configuration lives.** Either works:
   - *Codespaces secrets* (set once, every new Codespace gets them): GitHub account **Settings > Codespaces >
     Secrets**, create `MODEL_BASE_URL`, `MODEL_NAME`, `MCP_URL` (gateway route), **`AGENT_API_KEYS`**, and optionally
     `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL` (direct route). Give each access to this repo.
   - *A `.env` file inside the Codespace* (per Codespace, gone when it is deleted): `cp .env.example .env` and edit.
     If the same variable is set both ways, the secret wins.
3. **`AGENT_API_KEYS` is required in Codespaces.** The dev container sets `AGENT_REQUIRE_API_KEY=1`, so both processes
   refuse to start until a key exists. Generate one with the command in "Securing the agent".

### Start it

4. **Create the Codespace:** repo page > **Code > Codespaces > Create codespace on main**. Wait for the build.
5. **Two processes start by themselves** (the chat UI on 8000, the A2A agent on 9000) through `scripts/start.sh`,
   which runs when the Codespace starts and again each time you open it. It only starts what is not already running,
   so if one crashed, reopening the Codespace brings it back. It prints whether each came up, and the log tail if not:
   ```bash
   bash scripts/start.sh               # start whatever is not running, and report
   pgrep -af "uvicorn|a2a_server"      # list the processes
   tail -n 20 /tmp/uvicorn.log /tmp/a2a.log
   ```
   `/tmp/uvicorn.log` should say `Mode: direct`, the model route, and that `x-api-key` is required.
6. **Make the ports private** before anything else, because your saved keys act as defaults for anyone who can open
   a port: bottom panel **Ports** tab > right-click 8000 and 9000 > **Port Visibility > Private**.
   (Private is the default; a globe icon means public.)

### Use it

7. **Open the chat UI:** Ports tab > port 8000 > globe icon (Open in Browser). Sign in to GitHub if asked.
8. **Connect.** Enter the **Agent API key** (your `AGENT_API_KEYS` value). Direct Anthropic mode greys out the model key
   field. Leave the MCP key blank to use the saved default, or type another to demo another account. After Connect you
   see the six MCP tools, and the **Agent card** panel on the left shows the A2A agent's card URL with Copy and Open
   buttons and a live status dot.
9. **Chat.** Click a suggested prompt (blue = reads data, amber = changes it) or type your own.

### Check the A2A agent

10. **Agent card** (open, no key needed):
    ```bash
    curl -s http://localhost:9000/.well-known/agent-card.json | grep -o '"url":"[^"]*"'
    ```
    This should print your forwarded `-9000` address. If it prints `http://localhost:9000/`, set `A2A_PUBLIC_URL`
    (see "Fix the card address").
11. **Send a message over A2A.** The probe needs the mode and URL on the command line because `.env` says
    `AGENT_MODE=direct`, plus the agent key:
    ```bash
    A2A_API_KEY=<your-agent-key> AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/ python -m app.a2a_client "Show my watchlist"
    ```
    You should see `>` lines for tool calls, `<` lines for results, then the answer.

### Fix the card address

Codespaces fills in the card address from `CODESPACE_NAME`. If the card still says `localhost`, set it yourself in `.env`
(your Codespace name is the first part of the URL in your browser; note `-9000`, the A2A port):

```
A2A_PUBLIC_URL=https://<codespace-name>-9000.app.github.dev/
```

then `bash scripts/start.sh --restart`. The address changes if you create a new Codespace. The UI's Agent card panel
warns when the card still advertises `localhost`.

### Run the UI as an A2A client

```bash
pkill -f "uvicorn app.server"
AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/ nohup uvicorn app.server:app --host 0.0.0.0 --port 8000 > /tmp/uvicorn.log 2>&1 &
```

Reload the page and Connect (enter the Agent API key, or set `A2A_API_KEY`): the sidebar now lists skills and the header
says `... via A2A`. To go back to direct mode, run `bash scripts/start.sh --restart`. Do not `export` `AGENT_MODE`:
an exported `AGENT_MODE=a2a` makes the next UI start come up in A2A mode.

### Restart (after editing `.env` or a secret)

Secrets are read when the Codespace starts, and `.env` when each process starts:

```bash
bash scripts/start.sh --restart       # stop both, start both, report
```

Stopping a Codespace pauses it and keeps its files; deleting it removes `.env` and logs. Both processes start again
automatically when it resumes.

### Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Neither app starts after updating the repo | `AGENT_API_KEYS` is not set but the Codespaces config requires it. Add the secret or `.env` line, then `bash scripts/start.sh --restart`. |
| Ports tab has no 8000 or 9000 | The process did not start. `bash scripts/start.sh` prints the reason, or `tail /tmp/uvicorn.log /tmp/a2a.log`. |
| Connect says the Agent API key is missing or wrong | Enter the value of `AGENT_API_KEYS` (any one of the comma-separated keys). |
| A2A calls return HTTP 401 | Missing or wrong `x-api-key`. Pass `A2A_API_KEY` to the probe, or the UI's Agent API key field. |
| Connect shows an MCP error | Wrong or missing MCP key, or the wrong `MCP_TRANSPORT` (try `sse`). |
| Connect shows a model error | Bad model key, or an unknown model name for the route in use. |
| Agent card panel says "A2A server not running" | `bash scripts/start.sh`. |
| Card advertises `localhost` | Set `A2A_PUBLIC_URL` as above and restart. |
| Probe fails with `Missing required environment variable: A2A_AGENT_URL` | Pass `AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/` on the command line. |
| UI starts in the wrong mode | An exported `AGENT_MODE` is set. `unset AGENT_MODE A2A_AGENT_URL`, then `bash scripts/start.sh --restart`. |

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
A2A_API_KEY=<key> AGENT_MODE=a2a A2A_AGENT_URL=http://localhost:9000/ python -m app.a2a_client "Show my watchlist"   # CLI probe
```

- **Agent card** at `/.well-known/agent-card.json`, with three skills; it declares the `x-api-key` scheme (and lists it
  as required when `AGENT_API_KEYS` is set) plus the optional `X-Mcp-Key` input.
- **Keys per request:** `x-api-key` (access), `X-Mcp-Key` (whose watchlist) and `X-Model-Key` (gateway key). The last two
  are optional, with the same env fallbacks as the UI. The probe reads `MCP_API_KEY` and `MODEL_API_KEY` from the environment.
- **Stateless:** each message stands alone. A caller can add prior turns as a data part
  `{"history": [{"role": "user"|"assistant", "content": "..."}]}` beside the text part; the web UI does this.
- **Streaming:** `message/stream` sends status updates for each tool call and result (a text part plus a data part
  `{"event": "tool_call"|"tool_result", ...}`), then the final answer as an artifact.
- Uses a2a-sdk 0.3.x and the AgentCore SDK's `build_a2a_app`, wrapped with the key check and run by uvicorn.
  `/ping` and the card come from the SDK.

For a remote agent, the UI authenticates to it with `A2A_AUTH_MODE`: `bearer` (+ `A2A_BEARER_TOKEN`) or `sigv4`
(+ `A2A_REGION`, AWS credentials in your shell), on top of the `x-api-key`.

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
bash deploy/build_and_push.sh                   # builds linux/arm64, pushes to ECR, prints the image URI

# set the runtime's configuration in your shell (same names as .env.example), then:
export MODEL_BASE_URL=... MODEL_NAME=... MODEL_API_KEY=... MCP_URL=... AGENT_API_KEYS=...   # or ANTHROPIC_API_KEY + ANTHROPIC_MODEL
python deploy/agentcore_deploy.py --image <image-uri> --role-arn <execution-role-arn> --region $AWS_REGION
```

- The script creates the runtime with protocol A2A, public network mode, IAM (SigV4) auth for callers, and the
  request header allowlist `X-Mcp-Key`, `X-Model-Key` and `X-Api-Key`, so each caller's keys reach the agent.
  Add `--jwt-discovery-url` and `--jwt-allowed-client` for a JWT authorizer instead (it also allowlists `Authorization`).
  Use `--update <runtime-id>` to redeploy.
- AgentCore's own IAM or JWT auth gates who can invoke the runtime; `AGENT_API_KEYS` is a second layer inside it.
  If the runtime does not forward `X-Api-Key` to the container, every call gets a 401 (see "Not yet verified").
- You create the IAM execution role yourself (see the AgentCore docs, "Runtime permissions").
- Values you export become the runtime's environment variables, which are readable by anyone who can
  read the runtime configuration. Fine for a demo with throwaway keys; move them to Secrets Manager later.
- The script prints the `A2A_AGENT_URL` to use. Then point the chat UI at it:
  `AGENT_MODE=a2a A2A_AGENT_URL=<url> A2A_AUTH_MODE=sigv4 A2A_REGION=$AWS_REGION A2A_API_KEY=<key> uvicorn app.server:app --port 8000`.

## Layout

| File | Role |
|---|---|
| `app/config.py` | Env vars only, one config object per endpoint |
| `app/auth.py` | The single place for credentials: outbound keys and, inbound, the `x-api-key` check and middleware |
| `app/agent.py` | Agent logic, no web imports: `connect()` and `run_agent()` |
| `app/a2a_server.py` | A2A agent card, executor, and entrypoint |
| `app/a2a_client.py` | Minimal A2A client used by the UI, plus a CLI probe |
| `app/server.py` | Thin FastAPI layer for the chat UI |
| `app/static/index.html` | Single-file chat UI (markdown tables, tool-call timeline, agent card panel) |
| `scripts/start.sh` | Starts the UI and the A2A agent, skipping what is already running; `--restart` restarts both |
| `Dockerfile`, `Dockerfile.agentcore` | UI image, and the ARM64 A2A image for AgentCore |
| `deploy/` | ECR build/push script and the AgentCore create/update script |
| `.devcontainer/` | Codespaces setup |

## Verification status

Tested by hand in a GitHub Codespace (before the key check and start script were added), direct Anthropic route: the agent card
is served; the CLI probe reads and writes the watchlist over A2A with streamed tool calls; the chat UI works in direct and
A2A modes.

Tested in a sandbox without the real SDKs: the key verification and middleware (valid, wrong, missing and multiple keys;
`/ping` and the card staying open; no key echoed back), the config rules, the client's key handling, and `scripts/start.sh`
(start, no-op rerun, partial recovery, restart, crash reporting, overlapping starts).

Not yet verified:

- **The new A2A startup path:** the server now builds the app with `build_a2a_app`, adds the key middleware, and runs uvicorn
  itself instead of calling `serve_a2a`. I could not read the SDK source, so check on first run that `/ping`, the card, and
  JSON-RPC all answer, and that the card shows the right address (`A2A_PUBLIC_URL`).
- **Per-request key headers (`X-Mcp-Key`, `X-Model-Key`):** that they override the server defaults. Test with a wrong
  `MCP_API_KEY` on the probe: it should fail. If it prints a watchlist, the header is being ignored.
- **`X-Api-Key` on AgentCore:** that the runtime forwards it to the container once allowlisted. The middleware reads the
  raw request header, so it works anywhere the header arrives.
- **Gateway model route:** needs `/v1/messages`, streaming, tool use, `x-api-key`, and your `MODEL_NAME`.
  If it rejects `anthropic-beta` headers, keep `MODEL_DISABLE_BETAS=1`.
- **AgentCore:** that the SDK's bundled CLI starts in an ARM64 AgentCore container, that the runtime can reach your
  gateway (and Anthropic in direct mode), and `sigv4`/`bearer` calls from the UI.
- **MCP transport `sse`:** only `http` has been exercised.
- **Read/write labels** in the UI are a name heuristic (`Get*` = read). Tool input schemas are not shown in the sidebar.
- **Gateway for A2A:** no Axway documentation found on fronting an A2A agent with AI Gateway, so inbound A2A calls
  reach the agent directly. The agent's outbound calls (model, MCP) still go through the gateway.
