# Stock Watchlist Agent

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/lbrenman/stock-watchlist-agent)

Demo agent on Axway AI Gateway. The agent is one A2A server process that calls a model and the Fusion Stock Watchlist
MCP server. A small chat UI is a client for it and shows every tool call (name, input, result, timing) as it happens.

```
Browser  ->  chat UI server (:8000)  ->  A2A agent (:9000)  ->  model  +  MCP tools
                 just a client            does all the work     (Anthropic directly, or an
                                                                 Anthropic-compatible proxy)
```

The agent is always its own process (`python -m app.a2a_server`), whether it runs on a laptop, in a Codespace, or in AWS
Bedrock AgentCore Runtime. The chat UI points at it with `A2A_AGENT_URL`, which defaults to the agent on the same machine.
Other A2A clients can discover and call the agent through its card.

## Model route: two options

Set on the agent:

- **An Anthropic-compatible proxy (default), such as Fusion AI Gateway:** leave `ANTHROPIC_API_KEY` unset. Set
  `MODEL_BASE_URL` (root, no `/v1/messages`) and `MODEL_NAME`. The proxy key comes from the caller (`X-Model-Key`, which
  the UI sends from its Model API key field) or from `MODEL_API_KEY`.
- **Direct to Anthropic:** set `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` (an Anthropic model ID, for example
  `claude-haiku-5-5` for a low-cost option). The agent calls Anthropic directly and **bypasses the gateway**. Keys sent
  by callers are ignored. Use it as a dev fallback; leave it unset for gateway demos and AgentCore.

`ANTHROPIC_API_KEY` is a common variable name. Check your shell does not already set it, or the agent will go direct
without you asking. The agent logs its route at startup and states it in its card description, which the UI shows.

## Securing the agent: x-api-key

Set `AGENT_API_KEYS` on the agent and every call except the agent card and `/ping` (AgentCore's health check) must send
`x-api-key: <key>`. A missing or wrong key gets HTTP 401.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"      # generate a key
```

| Variable | Meaning |
|---|---|
| `AGENT_API_KEYS` | One key, or several separated by commas (add the new key, move callers over, then remove the old one) |
| `AGENT_REQUIRE_API_KEY=1` | The agent refuses to start without a key. The Codespaces config sets this. Unset: no keys means open access, with a warning in the log |
| `A2A_API_KEY` | The key the chat UI and the CLI probe send by default. The UI's Agent API key field overrides it |

```bash
curl -i http://localhost:9000/ -H "Content-Type: application/json" -d '{}'                  # 401 without a key
curl -s http://localhost:9000/.well-known/agent-card.json | head -c 200                      # the card is open
A2A_API_KEY=<key> python -m app.a2a_client "Show my watchlist"                                # with the key
```

The key is compared in constant time, read only from the `x-api-key` header, and never logged. This is a shared-secret
gate for a demo, not an identity system: no per-key permissions and no rate limiting. Keys live in environment variables,
so on AgentCore anyone who can read the runtime configuration can read them (use Secrets Manager beyond a demo).
The chat UI server does not check keys itself; it forwards them to the agent. It can hold saved credentials
(`A2A_API_KEY`, or a bearer token for AgentCore), so keep its port private.

## Quick start (laptop)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in section 1: the model route, MCP_URL, and AGENT_API_KEYS
bash scripts/start.sh       # starts the agent on :9000 and the chat UI on :8000
```

Open http://localhost:8000, enter the Agent API key (your `AGENT_API_KEYS` value), optionally an MCP key and a model key
(blank uses the agent's own defaults), and click Connect.

## Run in a GitHub Codespace

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/lbrenman/stock-watchlist-agent)

The button creates a Codespace from this repo. Your Codespaces secrets are private to your own account, so anyone else
who clicks it gets their own Codespace without your keys and must supply their own configuration.

### One-time setup

1. **Push the project to a GitHub repo.** Check with `git status` that `.env` is not listed (it is gitignored).
2. **Decide where configuration lives.** Either works:
   - *Codespaces secrets* (set once, every new Codespace gets them): GitHub account **Settings > Codespaces >
     Secrets**, create `MODEL_BASE_URL`, `MODEL_NAME`, `MCP_URL` (proxy route), **`AGENT_API_KEYS`**, and optionally
     `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL` (direct route). Give each access to this repo.
   - *A `.env` file inside the Codespace* (per Codespace, gone when it is deleted): `cp .env.example .env` and edit.
     If the same variable is set both ways, the secret wins.
3. **`AGENT_API_KEYS` is required in Codespaces.** The dev container sets `AGENT_REQUIRE_API_KEY=1`, so the agent refuses
   to start until a key exists. Generate one with the command in "Securing the agent".

### Start it

4. **Create the Codespace:** repo page > **Code > Codespaces > Create codespace on main**. Wait for the build.
5. **Both processes start by themselves** (the agent on 9000, the chat UI on 8000) through `scripts/start.sh`, which runs
   when the Codespace starts and again each time you open it. It only starts what is not already running, so if one
   crashed, reopening the Codespace brings it back. It prints whether each came up, and the log tail if not:
   ```bash
   bash scripts/start.sh               # start whatever is not running, and report
   pgrep -af "uvicorn|a2a_server"      # list the processes
   tail -n 20 /tmp/a2a.log /tmp/uvicorn.log
   ```
   `/tmp/a2a.log` should show the model route and that `x-api-key` is required.
6. **Make the ports private** before anything else: bottom panel **Ports** tab > right-click 8000 and 9000 >
   **Port Visibility > Private**. (Private is the default; a globe icon means public.) A public agent port lets anyone who
   knows the URL reach the agent, and a public UI port exposes whatever credentials the UI server has saved.

### Use it

7. **Open the chat UI:** Ports tab > port 8000 > globe icon (Open in Browser). Sign in to GitHub if asked.
8. **Connect.** Enter the **Agent API key** (your `AGENT_API_KEYS` value). Leave the MCP key blank to use the agent's saved
   default, or type another to demo another account. Connect checks the agent key without calling the model. The left
   panel then shows the agent (name, version, description including its model route), its card URL with Copy and Open
   buttons, and its skills.
9. **Chat.** Click a suggested prompt (blue = reads data, amber = changes it) or type your own.

### Check the agent directly

10. **Agent card** (open, no key needed):
    ```bash
    curl -s http://localhost:9000/.well-known/agent-card.json | grep -o '"url":"[^"]*"'
    ```
    This should print your forwarded `-9000` address. If it prints `http://localhost:9000/`, set `A2A_PUBLIC_URL`
    (see "Fix the card address").
11. **Send a message** with the CLI probe (it targets `http://localhost:9000/` unless you set `A2A_AGENT_URL`):
    ```bash
    A2A_API_KEY=<your-agent-key> python -m app.a2a_client "Show my watchlist"
    ```
    You should see `>` lines for tool calls, `<` lines for results, then the answer.

### Fix the card address

Codespaces fills in the card address from `CODESPACE_NAME`. If the card still says `localhost`, set it yourself in `.env`
(your Codespace name is the first part of the URL in your browser; note `-9000`, the agent's port):

```
A2A_PUBLIC_URL=https://<codespace-name>-9000.app.github.dev/
```

then `bash scripts/start.sh --restart`. The address changes if you create a new Codespace. The UI's agent panel warns
when the card still advertises `localhost`.

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
| Neither process starts after updating the repo | `AGENT_API_KEYS` is not set but the Codespaces config requires it. Add the secret or `.env` line, then `bash scripts/start.sh --restart`. |
| Ports tab has no 8000 or 9000 | The process did not start. `bash scripts/start.sh` prints the reason, or `tail /tmp/a2a.log /tmp/uvicorn.log`. |
| Connect says it could not reach the agent | The agent is not running, or `A2A_AGENT_URL` points somewhere else. Run `bash scripts/start.sh`. |
| Connect says the agent rejected the Agent API key | Enter the value of `AGENT_API_KEYS` (any one of the comma-separated keys), or set `A2A_API_KEY`. |
| The first message fails with an MCP or model error | Wrong or missing MCP or model key, or the wrong `MCP_TRANSPORT` (try `sse`). The UI cannot check these at Connect. |
| Card advertises `localhost` | Set `A2A_PUBLIC_URL` as above and restart. |
| An old `.env` has `AGENT_MODE` | It is ignored now; delete the line. The agent always runs as its own process. |

## The A2A agent

```bash
python -m app.a2a_server
curl http://localhost:9000/.well-known/agent-card.json
A2A_API_KEY=<key> python -m app.a2a_client "Show my watchlist"          # CLI probe
```

- **Agent card** at `/.well-known/agent-card.json`: three skills, a description that states the model route, and the
  `x-api-key` scheme (listed as required when `AGENT_API_KEYS` is set) plus the optional `X-Mcp-Key` input.
- **Keys per request:** `x-api-key` (access), `X-Mcp-Key` (whose watchlist) and `X-Model-Key` (proxy key). The last two are
  optional, with the env fallbacks above. The probe reads `MCP_API_KEY` and `MODEL_API_KEY` from its environment.
- **Stateless:** each message stands alone. A caller can add prior turns as a data part
  `{"history": [{"role": "user"|"assistant", "content": "..."}]}` beside the text part; the chat UI does this.
- **Streaming:** `message/stream` sends status updates for each tool call and result (a text part plus a data part
  `{"event": "tool_call"|"tool_result", ...}`), then the final answer as an artifact.
- Uses a2a-sdk 0.3.x and the AgentCore SDK's `build_a2a_app`, wrapped with the key check and run by uvicorn.
  `/ping` and the card come from the SDK.

## The chat UI

A client for the agent: it fetches the card, forwards your keys as headers, streams the agent's tool calls and answer,
and renders answers as markdown (tables with aligned numbers and green and red changes). To reach a remote agent, set
`A2A_AGENT_URL`, and `A2A_AUTH_MODE` (`bearer` + `A2A_BEARER_TOKEN`, or `sigv4` + `A2A_REGION` with AWS credentials in
the environment) to get past the host's own authentication.

Because it only talks to the agent, it cannot see the MCP server: the sidebar lists the agent card's skills, not the MCP
tools, and a wrong MCP or model key shows up on the first message rather than at Connect (the agent key is checked at
Connect).

## Docker

```bash
docker build -f Dockerfile.agentcore -t stock-watchlist-agent .          # the agent (ARM64-ready, port 9000)
docker run --rm -p 9000:9000 --env-file .env stock-watchlist-agent
docker build -t stock-watchlist-ui .                                     # the chat UI (light image, port 8000)
docker run --rm -p 8000:8000 -e A2A_AGENT_URL=http://host.docker.internal:9000/ stock-watchlist-ui
```

`requirements.txt` is for the agent (and laptop or Codespace use of both); `requirements-ui.txt` is the UI-only subset.

## Deploy the agent to AWS Bedrock AgentCore Runtime

AgentCore needs an **ARM64** container serving A2A on **port 9000**; `Dockerfile.agentcore` is built for that.

```bash
export AWS_REGION=us-east-1
bash deploy/build_and_push.sh                   # builds linux/arm64, pushes to ECR, prints the image URI

# set the agent's configuration in your shell (same names as .env.example section 1), then:
export MODEL_BASE_URL=... MODEL_NAME=... MODEL_API_KEY=... MCP_URL=... AGENT_API_KEYS=...   # or ANTHROPIC_API_KEY + ANTHROPIC_MODEL
python deploy/agentcore_deploy.py --image <image-uri> --role-arn <execution-role-arn> --region $AWS_REGION
```

- The script creates the runtime with protocol A2A, public network mode, IAM (SigV4) auth for callers, and the request
  header allowlist `X-Mcp-Key`, `X-Model-Key` and `X-Api-Key`, so each caller's keys reach the agent.
  Add `--jwt-discovery-url` and `--jwt-allowed-client` for a JWT authorizer instead (it also allowlists `Authorization`).
  Use `--update <runtime-id>` to redeploy.
- AgentCore's own IAM or JWT auth gates who can invoke the runtime; `AGENT_API_KEYS` is a second layer inside it.
  If the runtime does not forward `X-Api-Key` to the container, every call gets a 401 (see "Not yet verified").
- You create the IAM execution role yourself (see the AgentCore docs, "Runtime permissions").
- Values you export become the runtime's environment variables, readable by anyone who can read the runtime
  configuration. Fine for a demo with throwaway keys; move them to Secrets Manager later.
- The script prints the settings for the chat UI. For example:
  `A2A_AGENT_URL=<url> A2A_AUTH_MODE=sigv4 A2A_REGION=$AWS_REGION A2A_API_KEY=<key> uvicorn app.server:app --port 8000`.

## Layout

| File | Role |
|---|---|
| `app/a2a_server.py` | The agent: card, executor, and entrypoint (`python -m app.a2a_server`) |
| `app/agent.py` | Runs one agent turn against the model and MCP server (no web imports) |
| `app/auth.py` | The single place for credentials: outbound keys and, inbound, the `x-api-key` check and middleware |
| `app/config.py` | Env vars only, one config object per endpoint |
| `app/server.py` | The chat UI's thin FastAPI layer (an A2A client) |
| `app/a2a_client.py` | Minimal A2A client used by the UI, plus the CLI probe |
| `app/static/index.html` | Single-file chat UI |
| `scripts/start.sh` | Starts the agent and the UI, skipping what is already running; `--restart` restarts both |
| `Dockerfile.agentcore`, `Dockerfile` | The ARM64 agent image for AgentCore, and the light UI image |
| `requirements.txt`, `requirements-ui.txt` | Agent (and all-in-one) dependencies, and UI-only dependencies |
| `deploy/` | ECR build/push script and the AgentCore create/update script |
| `.devcontainer/` | Codespaces setup |

## Verification status

Tested by hand in a GitHub Codespace on an earlier version, direct Anthropic route: the agent card is served; the CLI probe
reads and writes the watchlist over A2A with streamed tool calls; the chat UI worked.

Tested in a sandbox without the real SDKs: the key verification and middleware (valid, wrong, missing and multiple keys;
`/ping` and the card staying open; no key echoed back), the config rules, the client's key handling, the web server's
Connect and chat handlers, and `scripts/start.sh` (start, no-op rerun, partial recovery, restart, crash reporting,
overlapping starts).

Not yet verified:

- **This simplified version end to end.** The agent now starts through `build_a2a_app` + the key middleware + uvicorn, and the
  UI no longer has a direct mode. Check on first run that `/ping`, the card and JSON-RPC all answer, the card shows the right
  address, and the UI connects, chats, and shows tool-call cards.
- **Per-request key headers (`X-Mcp-Key`, `X-Model-Key`):** that they override the agent's defaults. Test with a wrong
  `MCP_API_KEY` on the probe: it should fail. If it prints a watchlist, the header is being ignored.
- **`X-Api-Key` on AgentCore:** that the runtime forwards it to the container once allowlisted. The middleware reads the raw
  request header, so it works anywhere the header arrives.
- **Proxy model route:** needs `/v1/messages`, streaming, tool use, `x-api-key`, and your `MODEL_NAME`.
  If it rejects `anthropic-beta` headers, keep `MODEL_DISABLE_BETAS=1`.
- **AgentCore:** that the SDK's bundled CLI starts in an ARM64 AgentCore container, that the runtime can reach your
  gateway (and Anthropic on the direct route), and `sigv4`/`bearer` calls from the UI.
- **MCP transport `sse`:** only `http` has been exercised.
- **Read/write labels** in the UI are a name heuristic (`Get*` = read).
- **Gateway for A2A:** no Axway documentation found on fronting an A2A agent with AI Gateway, so A2A calls reach the agent
  directly. The agent's outbound calls (model, MCP) still go through the gateway.
