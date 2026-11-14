# Stock Watchlist Agent

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/lbrenman/stock-watchlist-agent)

Demo agent on Axway AI Gateway. The agent is one A2A server process that calls a model and the Fusion Stock Watchlist
MCP server. A small web app is a client for it and shows every tool call (name, input, result, timing) as it happens.

```
Browser (web app, static files)  ------>  A2A agent (:9000)  ------>  model  +  MCP tools
 keeps your clients (URL + keys)          does all the work           (Anthropic directly, or an
 and calls the agent directly                                          Anthropic-compatible proxy)
```

The agent is always its own process (`python -m app.a2a_server`), whether it runs on a laptop, in a Codespace, on Render, or in AWS
Bedrock AgentCore Runtime. The web app is only files (`web/index.html`): it has no server, no settings and no connection
to the agent except the address and keys you give it, so the same page can talk to any agent you can reach. Because the
browser calls the agent directly, the agent answers CORS requests from any origin (access is controlled by the
`x-api-key` header, not by cookies). Other A2A clients can discover and call the agent through its card. The agent speaks
**A2A 1.0 only**: a 0.3 client no longer works against it, and this version of the web app no longer works against a 0.3 agent.

## Model route: two options

Set on the agent:

- **An Anthropic-compatible proxy (default), such as Fusion AI Gateway:** leave `ANTHROPIC_API_KEY` unset. Set
  `MODEL_BASE_URL` (root, no `/v1/messages`), `MODEL_NAME` and `MODEL_API_KEY` (the proxy's key). The web app has no model
  key field, so the key has to be on the agent. A direct caller can still override it per request with an `X-Model-Key`
  header.
- **Direct to Anthropic:** set `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` (an Anthropic model ID, for example
  `claude-haiku-5-5` for a low-cost option). The agent calls Anthropic directly and **bypasses the gateway**. Keys sent
  by callers are ignored. Use it as a dev fallback; leave it unset for gateway demos and AgentCore.

`ANTHROPIC_API_KEY` is a common variable name. Check your shell does not already set it, or the agent will go direct
without you asking. The agent logs its route at startup and states it in its card description, which the web app shows.

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

```bash
curl -i -X POST http://localhost:9000/ -H "Content-Type: application/json" -d '{}'          # 401 without a key
curl -s http://localhost:9000/.well-known/agent-card.json | head -c 200                      # the card is open
```

The key is compared in constant time, read only from the `x-api-key` header, and never logged. This is a shared-secret
gate for a demo, not an identity system: no per-key permissions and no rate limiting. Keys live in environment variables,
so on AgentCore anyone who can read the runtime configuration can read them (use Secrets Manager beyond a demo).
The web app has no server: the keys you type are kept in your browser (see "The web app") and sent straight to the agent.

CORS allows any origin, so the key is the only protection. Anyone who knows an agent's public URL can reach it, and they
can use it only with a valid key. Use long random keys.

## Which MCP key the agent uses

Each MCP API key has its own watchlist. For every call the agent picks the key for the MCP server like this, first
non-blank wins:

1. the `X-Mcp-Key` header of the call;
2. `MCP_API_KEY` from the agent's environment;
3. the caller's own `x-api-key` (the key it used to reach the agent).

The key is sent to `MCP_URL` in the header named by `MCP_API_KEY_HEADER` (default `x-api-key`). The model key has the same
shape without the third step: `X-Model-Key`, then `MODEL_API_KEY`.

What that means in practice:

- With `MCP_API_KEY` empty and no `X-Mcp-Key`, **the agent key is sent to the MCP server**. The MCP server (or the gateway
  in front of it) must accept it as an MCP key, or callers must always send `X-Mcp-Key`.
- Behind an AI Gateway proxy that adds the agent's `x-api-key` to every call, callers who send no `X-Mcp-Key` all get the
  watchlist of that key.
- If the agent is open (`AGENT_API_KEYS` unset) and the caller sends no `x-api-key`, no `X-Mcp-Key` and `MCP_API_KEY` is empty,
  the run fails with "MCP API key is required."

Each run's log line names which one it used (`mcp_key_from=X-Mcp-Key header`, `MCP_API_KEY` or `x-api-key of the call`),
never the key itself. The web app is not told: it cannot see the agent's environment, and the label is not sent in the
stream. Keys are never logged and are redacted from errors.

## Quick start (laptop)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in: the model route, MCP_URL, and AGENT_API_KEYS
bash scripts/start.sh       # starts the agent on :9000 and serves the web app on :8000
```

Open http://localhost:8000. The first time, the page asks for a client: the agent's URL (`http://localhost:9000/` is
filled in), the Agent API key (one of your `AGENT_API_KEYS`) and, if you want, an MCP API key. After **Save and connect**
it goes straight to the chat, and next time it connects by itself. To see another account's watchlist, open **Clients**
(top right), add a client with that account's MCP API key, and switch between them.

## Run in a GitHub Codespace

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/lbrenman/stock-watchlist-agent)

The button creates a Codespace from this repo. Your Codespaces secrets are private to your own account, so anyone else
who clicks it gets their own Codespace without your keys and must supply their own configuration.

### One-time setup

1. **Push the project to a GitHub repo.** Check with `git status` that `.env` is not listed (it is gitignored).
2. **Decide where configuration lives.** Either works:
   - *Codespaces secrets* (set once, every new Codespace gets them): GitHub account **Settings > Codespaces >
     Secrets**, create `MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY`, `MCP_URL` (proxy route), **`AGENT_API_KEYS`**, and optionally
     `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL` (direct route). Give each access to this repo.
   - *A `.env` file inside the Codespace* (per Codespace, gone when it is deleted): `cp .env.example .env` and edit.
     If the same variable is set both ways, the secret wins.
3. **`AGENT_API_KEYS` is required in Codespaces.** The dev container sets `AGENT_REQUIRE_API_KEY=1`, so the agent refuses
   to start until a key exists. Generate one with the command in "Securing the agent".

### Start it

4. **Create the Codespace:** repo page > **Code > Codespaces > Create codespace on main**. Wait for the build.
5. **Both start by themselves** (the agent on 9000, the web app on 8000) through `scripts/start.sh`, which runs
   when the Codespace starts and again each time you open it. It only starts what is not already running, so if one
   crashed, reopening the Codespace brings it back. It prints whether each came up, and the log tail if not:
   ```bash
   bash scripts/start.sh               # start whatever is not running, and report
   pgrep -af "http.server|a2a_server"  # list the processes
   tail -n 20 /tmp/a2a.log /tmp/web.log
   ```
   `/tmp/a2a.log` should show the model route and that `x-api-key` is required.
6. **Make the agent's port public.** The page runs in your browser and calls the agent at its own address, a different
   origin from the page. Reports from other Codespaces users say that a Private port wants a GitHub sign-in that such a call
   (and its CORS preflight) does not carry, so the agent port has to be Public. Bottom panel **Ports** tab > right-click
   9000 > **Port Visibility > Public**. Port 8000 only serves the page and can stay Private. A public agent port lets anyone who
   knows the URL reach the agent, and only the `x-api-key` check stands between them and it, so use a long random key.

### Use it

7. **Open the web app:** Ports tab > port 8000 > globe icon (Open in Browser). Sign in to GitHub if asked.
8. **Add a client the first time.** The URL is filled in with this Codespace's port 9000 address. Enter the Agent API key
   (one of your `AGENT_API_KEYS`) and, if you want, an MCP API key (leave it blank to use the agent's default, see "Which MCP
   key the agent uses"). Choose **Save and connect**. The client is saved in this browser, and from then on the page
   connects by itself and opens straight to the chat. You only see the home screen if that connection fails, or after you choose Disconnect (the message
   says why).
9. **Chat.** Click a suggested prompt (blue = reads data, amber = changes it) or type your own. After the first message the
   prompts stay available as chips above the message box. Each tool call is one collapsed line; click it for the input and
   result. The agent panel (top left button) shows the client, the agent, its card URL, and its skills, and opens only when
   you ask. **Clients** (top right) switches between saved clients or edits them.

### Check the agent directly

10. **Agent card** (open, no key needed):
    ```bash
    curl -s http://localhost:9000/.well-known/agent-card.json | grep -o '"url": *"[^"]*"'
    ```
    This should print your forwarded `-9000` address (in the card it sits under `supportedInterfaces`). If it prints `http://localhost:9000/`, set `A2A_PUBLIC_URL`
    (see "Fix the card address").
11. **Send a message** with the curl command in "Call the agent directly (curl)" below. You should see `data:` lines
    for tool calls and results, then the answer.

### Fix the card address

Codespaces fills in the card address from `CODESPACE_NAME`. If the card still says `localhost`, set it yourself in `.env`
(your Codespace name is the first part of the URL in your browser; note `-9000`, the agent's port):

```
A2A_PUBLIC_URL=https://<codespace-name>-9000.app.github.dev/
```

then `bash scripts/start.sh --restart`. The address changes if you create a new Codespace. The web app does not use the
address in the card (it calls the URL you gave it), but other A2A clients do, such as the A2A Inspector, whose sends fail
with a 404 if the card's address (`supportedInterfaces[0].url`) has no port. The agent panel warns when a remote agent's card still advertises `localhost`.

### Restart (after editing `.env` or a secret)

Secrets are read when the Codespace starts, and `.env` when each process starts:

```bash
bash scripts/start.sh --restart       # stop both, start both, report
```

Type the flag exactly: the script refuses an option it does not know and exits with an error, instead of quietly
reporting that everything is already running on the old settings.

Stopping a Codespace pauses it and keeps its files; deleting it removes `.env` and logs. Both start again
automatically when it resumes. Saved clients live in your browser, not in the Codespace.

### Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Neither process starts after updating the repo | `AGENT_API_KEYS` is not set but the Codespaces config requires it. Add the secret or `.env` line, then `bash scripts/start.sh --restart`. |
| Ports tab has no 8000 or 9000 | The process did not start. `bash scripts/start.sh` prints the reason, or `tail /tmp/a2a.log /tmp/web.log`. |
| "Could not reach ... CORS, or the port is not public" | The page could not get an answer from the agent URL. Check the URL (a Codespace agent is `https://<codespace-name>-9000.app.github.dev/`), that the agent is running, and that port 9000 is **Public**. A gateway in front of the agent must also answer CORS requests (see "Point the web app at an agent somewhere else"). |
| "The agent rejected the Agent API key" | The key is not one of the agent's `AGENT_API_KEYS`. Open **Clients**, choose **Edit** on the client, and fix the key. |
| The home screen appears instead of the chat | The page could not connect to the client it used last. The message under the form says why. |
| An old key keeps coming back | Keys are saved in this browser with each client. **Clients** > **Edit** or **Delete**, or **Delete all clients**. |
| The first message fails with an MCP or model error | Wrong or missing MCP key or proxy key, the MCP server does not accept the agent key (when no MCP key is set, see "Which MCP key the agent uses"), or the wrong `MCP_TRANSPORT` (try `sse`). The page cannot check these in advance. For the proxy route, set `MODEL_API_KEY` on the agent. |
| I edited `.env` or a secret and nothing changed | The agent reads it only when it starts. `bash scripts/start.sh --restart`, then check the first line of `tail /tmp/a2a.log`: it names the model route and `x-api-key` status. |
| The model says it has no quote tool, or no tools | The agent could not use the MCP server. Open the "MCP connection" line at the top of the answer (it opens itself when a server failed) and the Events screen, and read `/tmp/a2a.log`: `MCP servers: stockwatchlist=failed` and `mcp_key_from=` say what happened. Usual causes: the MCP key was rejected (see "Which MCP key the agent uses"), a wrong `MCP_URL`, or the wrong `MCP_TRANSPORT` (try `sse`). |
| Card advertises `localhost` | Set `A2A_PUBLIC_URL` as above and restart. |
| "A2A error -32601: Method not found. The agent may speak an older A2A version" | The agent is not on A2A 1.0 (an older deployment). Redeploy it from this version, or use the web app that matches it. The page and the agent must speak the same version. |
| An old `.env` has `AGENT_MODE`, `A2A_AGENT_URL`, `A2A_API_KEY`, `A2A_AUTH_MODE`, `A2A_BEARER_TOKEN` or `A2A_REGION` | They are ignored now; delete the lines. The agent is always its own process, and the web app takes the URL and keys in the browser. |

## The A2A agent

```bash
python -m app.a2a_server
curl http://localhost:9000/.well-known/agent-card.json
```

- **Agent card** at `/.well-known/agent-card.json`: three skills, a description that states the model route, and the
  `x-api-key` scheme (listed as required when `AGENT_API_KEYS` is set) plus the optional `X-Mcp-Key` input.
- **Keys per request:** `x-api-key` (access), `X-Mcp-Key` (whose watchlist) and `X-Model-Key` (proxy key). The last two are
  optional, with the fallbacks described in "Which MCP key the agent uses".
- **Protocol: A2A 1.0 only.** JSON-RPC methods are the 1.0 names (`SendMessage`, `SendStreamingMessage`, `GetTask`, ...) and
  every call must carry the header `A2A-Version: 1.0` (the A2A spec tells a server to assume 0.3 for a call without it).
  Parts are flat (`{"text": ...}`, `{"data": ...}`, no `kind`), roles are `ROLE_USER` and `ROLE_AGENT`, task states are
  `TASK_STATE_*`, and the card's address is in `supportedInterfaces`.
- **Browsers:** the agent answers CORS preflights and sends CORS headers for any origin. It allows the headers `Content-Type`,
  `Accept`, `x-api-key`, `X-Mcp-Key`, `X-Model-Key`, `A2A-Version` and the AgentCore session header, and exposes all of its
  response headers to the page (so the Events screen can show them).
- **Stateless:** each message stands alone. A caller can add prior turns as a data part
  `{"data": {"history": [{"role": "user"|"assistant", "content": "..."}]}}` beside the text part; the web app does this.
- **Streaming:** `SendStreamingMessage` is a task stream, as 1.0 requires: the Task first, then status updates (state
  `TASK_STATE_WORKING`) for each tool call and result (a text part plus a data part
  `{"event": "tool_call"|"tool_result", ...}`), then the final answer as an artifact, then `TASK_STATE_COMPLETED` (or
  `TASK_STATE_FAILED` with the reason). The stream ends at that state; 1.0 has no `final` flag. Once at the start of a run it also sends
  `{"event": "mcp_status", "servers": [...], "tools": [...]}`: whether the MCP server connected
  (`connected`, `failed`, `needs-auth`, `pending` or `disabled`, as the Agent SDK reports it at the start of a run) and which
  tools loaded. `pending` is not a failure by itself.
- **Logs:** to the process's output (`/tmp/a2a.log` with `scripts/start.sh`, so `tail -f /tmp/a2a.log` while you chat). At
  startup the model route and whether `x-api-key` is required; for each run one `run start` line (route, model, MCP host and
  transport, where the MCP key came from), the MCP servers and tools, each tool call and result, and `run done`, or the
  error. Failed tool results and errors are logged with keys redacted. Keys themselves are never logged. uvicorn's own
  access lines (`POST / ... 200`) go to the same file, except successful `GET /ping` health checks: Render and AgentCore send
  one every few seconds, so those lines are left out (a failing check still shows).
  ```
  INFO:agent:run start: route=gateway model=<name> mcp=<gateway-host> (http) mcp_key_from=x-api-key of the call
  INFO:agent:MCP servers: stockwatchlist=connected; 6 tool(s): AddSymbolToStockWatchList, GetStockQuote, ...
  INFO:agent:tool call: GetStockWatchList
  INFO:agent:tool result: ok (120 chars)
  INFO:agent:run done: 2 turn(s), 5400 ms
  ```
- Uses a2a-sdk 1.x and the AgentCore SDK's `build_a2a_app` (installed with its `a2a-v1` extra, which AgentCore documents as the
  opt-in for A2A 1.x and which excludes the `a2a` extra), wrapped with the CORS and key-check middleware and run by
  uvicorn. `/ping` and the JSON-RPC route come from the SDK; the card is built in `app/a2a_server.py` from its JSON form.

### Call the agent directly (curl)

The JSON-RPC endpoint is `POST /` on the agent (or on its AI Gateway proxy URL). This streams the answer as SSE,
including each tool call and result as it happens:

```bash
curl -N http://localhost:9000/ \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -H "A2A-Version: 1.0" \
  -H "x-api-key: <AGENT_API_KEY>" \
  -H "X-Mcp-Key: <MCP_API_KEY>" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "SendStreamingMessage",
    "params": {
      "message": {
        "role": "ROLE_USER",
        "messageId": "msg-1",
        "parts": [{"text": "Show my watchlist"}]
      }
    }
  }'
```

For one JSON response instead of a stream, use `"method": "SendMessage"` and drop `-N` and the `Accept` header.
For a Codespace or a gateway proxy, replace `http://localhost:9000/` with that URL.

| Header | Required? | Purpose |
|---|---|---|
| `Content-Type: application/json` | Yes | JSON-RPC body |
| `A2A-Version: 1.0` | Yes, on every JSON-RPC call | The protocol version. Without it the agent is told to assume 0.3, which it does not speak |
| `x-api-key` | When `AGENT_API_KEYS` is set on the agent (always, with `AGENT_REQUIRE_API_KEY=1`) | The agent's own access key. Missing or wrong: HTTP 401. With no `AGENT_API_KEYS` the agent is open. It is also the last-resort MCP key |
| `X-Mcp-Key` | Optional | The MCP API key, which decides whose watchlist is used. Blank or absent falls back to `MCP_API_KEY` on the agent, then to the `x-api-key` of the call |
| `X-Model-Key` | Optional | Overrides the model or gateway key, falling back to `MODEL_API_KEY`. Ignored in direct Anthropic mode |
| `Accept: text/event-stream` | For `SendStreamingMessage` | What the web app sends |
| `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` | Only when calling through AgentCore | The agent code never reads it. AgentCore requires it (33 or more characters) |

Header names are not case sensitive. The agent card and `GET /ping` need no key.

## The web app

`web/index.html` is one file of HTML, CSS and JavaScript with no build step and no server logic. It keeps a list of
**clients** and calls the selected client's agent straight from the browser: it fetches the card, checks the key, sends
each chat turn as `SendStreamingMessage` (A2A 1.0, with the `A2A-Version: 1.0` header), reads the streamed tool calls and answer, and renders answers as markdown (tables with
aligned numbers and green and red changes). `scripts/start.sh` serves the folder with `python -m http.server` on port 8000;
any static file host would do. If you host `web/index.html` somewhere else (for example on a gateway), it is a copy: put the new
file there whenever it changes, and reload without the cache. To see which version a host serves, run
`curl -s <page URL> | grep -c SendStreamingMessage` (a number above 0 for this version, 0 for the previous A2A 0.3 version). The page is not tied to the agent's settings: it names none of them (no environment variable,
no `.env`) and is told nothing about the agent's environment. All it learns from an agent is its card and the answers to
its own calls.

- **Clients.** A client is a name, the agent's URL, the Agent API key and an optional MCP API key. The **Clients** button
  (top right, a people icon; on a desktop it also shows the name of the connected client, on a phone just the icon) opens the
  list: click a client to switch to it (a different client starts a fresh chat), **Edit**, **Delete**, **Add client**, or
  **Delete all clients**. The connected client's row also has **Disconnect**. All client actions are in this one place. Save and connect checks the card and the agent key before it saves anything, so a client that
  does not connect is not stored, and what you typed stays in the form.
- **Where they are kept.** In this browser's local storage, in plain text, keys included. Use it on private devices only.
  Nothing is sent anywhere except to the agent URL of the client you are using.
- **Connects on load** to the client used last, and shows the home screen when that fails, when there is no client yet, or
  when no client is marked as used last (after **Disconnect**, or after you delete the one used last). The first time, the
  URL is filled in for you: the agent on this machine, or this Codespace's port 9000.
- **Disconnect** (in the Clients sheet, on the connected client's row, next to Edit and Delete) goes back to the home screen with your saved clients listed. It
  clears the chat, cancels a reply that is still streaming, and keeps your clients and the Events list. No client is marked as
  used last, so a reload stays on the home screen until you pick one. On a phone it also closes the agent drawer. Switching to
  another client while a reply is streaming cancels that reply the same way.
- **Agent panel:** the button at the top left shows or hides it (open by default on a desktop, closed on a phone, remembered
  per device). It shows the client, the agent, its card URL (Copy and Open) and its skills, and never opens by itself.
- **MCP connection.** The first line of an answer says whether the MCP server connected and which tools loaded (collapsed;
  it opens itself when a server failed, with its error).
- **Tool calls** appear as one collapsed line each (name, a short input summary, status, time). Open one to see the full input
  and result; a failed call opens itself.
- **Events.** The **Events** button (top bar, and on the home screen, so it works when connecting fails) opens a panel with
  every request the page makes to an agent, newest first: the card fetch, the key check and each chat turn. A row shows the
  time, what it was, the status and the duration; open it for the request (URL, every header the page set, the JSON-RPC body)
  and the response (status, headers, and the whole body; for a chat turn that is the raw event stream, filling in as it
  arrives). A request that failed opens itself, and one that never completed says so. Keys (`x-api-key`, `X-Mcp-Key`,
  `X-Model-Key`, `Authorization`) are masked unless you switch on **Show keys**, and **Copy this event** and **Copy all**
  always mask them, so what you copy is safe to paste. Events are kept in memory only (the newest 200) and **Clear**
  empties them. The page cannot see headers the browser adds itself (Origin, User-Agent, Content-Length), and it sees a
  cross-origin response's headers only when the server exposes them; the agent does, a gateway may not. For a request
  that failed before any response, the browser's Network tab gives the reason.
- **Prompts** stay pinned as chips above the message box once a chat has started.
- **Phones:** the layout uses the full screen height without hiding behind Safari's toolbar or the notch, inputs are sized so
  iOS does not zoom, tables scroll sideways, and on a touch screen Return adds a line (the arrow button sends).

Because it only talks to the agent, it cannot see the MCP server: the panel lists the agent card's skills, not the MCP tools,
and a wrong MCP key shows up on the first message that needs it, not when connecting (the agent key is checked on connect).
There is no model key field; the proxy key lives on the agent (`MODEL_API_KEY`).

### Point the web app at an agent somewhere else

Add a client with that agent's URL. No code change and no restart. The page uses the URL two ways: it fetches the card from
`<URL>/.well-known/agent-card.json`, and it POSTs JSON-RPC to the URL itself, expecting an SSE stream. It ignores the `url`
inside the card. So the URL must be an address that serves both. Browsers add two limits:

- **CORS.** The agent (or whatever sits in front of it) must answer the browser's preflight request without credentials and
  allow the request headers `content-type`, `accept`, `x-api-key`, `x-mcp-key` and `a2a-version`. The agent itself does.
- **https pages.** A page served over https cannot call an `http://` address, except `localhost`. The page says so.

**The agent is on this machine.** `http://localhost:9000/`.

**The agent is in a Codespace.** `https://<codespace-name>-9000.app.github.dev/` with the agent's port set to Public (see
"Run in a GitHub Codespace"), and one of the agent's `AGENT_API_KEYS` as the Agent API key. The page can be served from the
same Codespace, from your laptop, or from any other host.

**The agent is proxied in AI Gateway.** Use the proxy's URL. The proxy must:
- serve the card at `<proxy URL>/.well-known/agent-card.json` and accept the JSON-RPC POST at the proxy URL itself;
- not buffer the response, or tool calls appear only when the answer finishes;
- pass `A2A-Version`, `X-Mcp-Key` and `X-Model-Key` through to the agent. If it drops `A2A-Version`, the agent is told to
  assume 0.3 and every call fails; if it drops `X-Mcp-Key`, every user silently gets the agent's default watchlist;
- speak A2A 1.0 itself: a gateway that only knows 0.3 cannot carry these calls;
- **answer the CORS preflight itself.** A preflight carries no credentials, so a proxy that rejects calls without credentials
  will reject it unless it is set up to answer `OPTIONS` first. This has not been tried; see "Verification status".

The proxy can hold the agent's `x-api-key` and add it to each call. In that case the Agent API key you enter is whatever the
gateway wants from its callers. Have the proxy authenticate its own callers, because one that accepts anonymous calls lets
anyone with its URL use the agent. If you rotate the agent's key, update it in the gateway too. With no `X-Mcp-Key` and no
`MCP_API_KEY`, the injected key also becomes the MCP key (see "Which MCP key the agent uses"). The card still advertises the
agent's own address. To advertise the proxy instead, set `A2A_PUBLIC_URL` on the agent to the proxy URL.

**The agent is on AgentCore Runtime.** Not reachable from this page. A runtime accepts either SigV4 (IAM) or a JWT, and a
browser cannot sign SigV4 requests; this page also has no bearer-token field. Call it with curl or another client that can
sign or present a token, or put it behind the gateway.

**Check that it worked.**
- The agent panel shows the card URL the page is using.
- Ask for your watchlist, then switch to a client with a different MCP key and ask again. Each account should show its own list.
- A wrong Agent API key fails on connect, with the message under the key field. A wrong MCP key fails on the first message.
- If connecting says "Could not reach ...", open the Events screen (it shows the request and that nothing came back) and the browser's developer console, which names a CORS problem.

## Docker

```bash
docker build -f Dockerfile.agentcore -t stock-watchlist-agent .          # the agent (ARM64-ready, port 9000)
docker run --rm -p 9000:9000 --env-file .env stock-watchlist-agent
```

The web app needs no image: serve `web/` from any static host, or run `python -m http.server 8000 --directory web`.

## Deploy the agent to AWS Bedrock AgentCore Runtime

AgentCore needs an **ARM64** container serving A2A on **port 9000**; `Dockerfile.agentcore` is built for that.

```bash
export AWS_REGION=us-east-1
bash deploy/build_and_push.sh                   # builds linux/arm64, pushes to ECR, prints the image URI

# set the agent's configuration in your shell (same names as .env.example), then:
export MODEL_BASE_URL=... MODEL_NAME=... MODEL_API_KEY=... MCP_URL=... AGENT_API_KEYS=...   # or ANTHROPIC_API_KEY + ANTHROPIC_MODEL
python deploy/agentcore_deploy.py --image <image-uri> --role-arn <execution-role-arn> --region $AWS_REGION
```

- The script creates the runtime with protocol A2A, public network mode, IAM (SigV4) auth for callers, and the request
  header allowlist `X-Mcp-Key`, `X-Model-Key`, `X-Api-Key` and `A2A-Version`, so each caller's keys and protocol version reach the agent.
  Add `--jwt-discovery-url` and `--jwt-allowed-client` for a JWT authorizer instead (it also allowlists `Authorization`).
  Use `--update <runtime-id>` to redeploy.
- AgentCore's own IAM or JWT auth gates who can invoke the runtime; `AGENT_API_KEYS` is a second layer inside it.
  If the runtime does not forward `X-Api-Key` to the container, every call gets a 401, and if it does not forward `A2A-Version`
  every call fails as a 0.3 call (see "Not yet verified").
- You create the IAM execution role yourself (see the AgentCore docs, "Runtime permissions").
- Values you export become the runtime's environment variables, readable by anyone who can read the runtime
  configuration. Fine for a demo with throwaway keys; move them to Secrets Manager later.
- The script prints the runtime's A2A address at the end. The web app cannot call an AgentCore runtime directly (see above).

## Run the agent on Render (free tier)

`render.yaml` runs the agent, and only the agent, as a Docker web service on Render's free plan. The web app is not part of
it: use any copy of the web app, or curl, with the service's URL.

1. Push the repo to GitHub. In the Render dashboard choose **New > Blueprint** and connect the repo; Render reads
   `render.yaml` from the repo root.
2. Render asks for the values marked `sync: false` (so none of your hostnames or keys is committed). Fill in the ones for your
   setup and leave the others blank (blank means unset):

   | Variable | What to enter |
   |---|---|
   | `MCP_URL` | The MCP server's URL |
   | `MCP_API_KEY` | An optional default MCP key. Blank: the caller's own `x-api-key` is used (see "Which MCP key the agent uses") |
   | `MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY` | The model route through a gateway proxy |
   | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Or the direct route (leave the three above blank) |

   If Render will not accept an empty value, a single space should also work, because the agent treats a whitespace-only value
   as unset (not tried on Render). To use another MCP transport, add `MCP_TRANSPORT=sse` under the service's **Environment**.
3. Render builds the image and starts the agent. `AGENT_API_KEYS` is generated for you: open the service, then
   **Environment**, and copy it. `AGENT_REQUIRE_API_KEY=1` is already set, so the agent refuses to start without a key.
4. The agent's address is `https://<service-name>.onrender.com/`, shown at the top of the service page. The card advertises it
   without any setting (the agent reads `RENDER_EXTERNAL_URL`, which Render sets). Check it:
   ```bash
   curl -s https://<service-name>.onrender.com/.well-known/agent-card.json | grep -o '"url": *"[^"]*"'
   ```
5. In the web app add a client with that URL and the generated key (and an MCP key if you use one), or call it with the curl
   command in "Call the agent directly". Render serves https, so a page served over https can call it, and the agent port
   does not have to be made public by hand as in a Codespace.

The settings are fixed in `render.yaml`: the port is `A2A_PORT=10000` to match Render's default `PORT`, the image is
`Dockerfile.agentcore` (the same one AgentCore uses), and the health check is `/ping`, which needs no key.

**The free plan's limits, for this agent:**
- **It sleeps.** A free service spins down after 15 minutes without traffic, and the next request takes about a minute
  while Render shows a loading page. The web app may report "Could not reach ... (CORS)" for that first call, because
  Render's loading page does not carry the agent's CORS headers (not verified). Wait a minute and try again.
- **512 MB of memory and 0.1 CPU.** Each run starts the Agent SDK's bundled command-line process next to the server. It may
  not fit in memory, or may run slowly. Watch the service's **Metrics** during your first runs; if the service is restarted for
  running out of memory or is too slow, switch it to a paid instance type.
- **The URL is public.** Anyone who knows it can reach the agent; only `x-api-key` stops them, so keep the generated key
  secret. To rotate it, edit `AGENT_API_KEYS` (several keys can be comma-separated).
- The service's **Logs** tab shows the agent's output, including the `run start` line and the MCP status lines. Render's health
  check calls `/ping` every few seconds; the agent leaves the successful ones out of its log.

## Layout

| File | Role |
|---|---|
| `app/a2a_server.py` | The agent: card, executor, CORS, and entrypoint (`python -m app.a2a_server`) |
| `app/agent.py` | Runs one agent turn against the model and MCP server (no web imports) |
| `app/auth.py` | The single place for credentials: outbound keys (MCP key order, model key) and, inbound, the `x-api-key` check and middleware |
| `app/config.py` | Env vars only, one config object per endpoint |
| `web/index.html` | The web app: one static file, with the A2A client and the clients list in its script |
| `scripts/start.sh` | Starts the agent and serves the web app, skipping what is already running; `--restart` restarts both |
| `Dockerfile.agentcore` | The ARM64 agent image for AgentCore (also fine for any container host) |
| `render.yaml` | Render Blueprint: runs the agent on Render's free plan (see "Run the agent on Render") |
| `requirements.txt` | The agent's dependencies |
| `deploy/` | ECR build/push script and the AgentCore create/update script |
| `.devcontainer/` | Codespaces setup |

## Verification status

**A2A 1.0 migration:** everything under "Tested by hand" and "Seen working on Render" below was done on the previous version,
which spoke A2A 0.3. None of it has been repeated on 1.0 yet; the 1.0 items under "Not yet verified" come first for that
reason.

Tested by hand in a GitHub Codespace on the earlier version of this project (A2A 0.3), when the page still went through a small
server of its own: the agent card is served with the right public URL; the startup path (`build_a2a_app`, key middleware,
uvicorn); the A2A Inspector and the page both read and write with streamed tool calls; a wrong Agent API key and a wrong MCP
key each fail; switching the MCP API key returned each account's own watchlist (the `X-Mcp-Key` override works); an AI Gateway
proxy of the agent worked with the old page, passed `X-Mcp-Key` through, and rejected calls that carry no credentials.

Seen working on Render (A2A 0.3), from the service's logs: the Blueprint built and started the agent; Render detected it on port 10000
and its health checks on `/ping` return 200; the card is served; a call without a valid key gets 401; and runs complete with
the bundled CLI, the MCP server connected (6 tools, reached from Render on the gateway's port), tool calls and results, and
`mcp_key_from=MCP_API_KEY`. Three runs went by without a restart. In those logs each run spent roughly 5 to 7 seconds
starting up before the model's work began, which the duration the page shows (the Agent SDK's own figure) does not include.

Tested in a sandbox without the real SDKs or a browser, on the current code:
- *A2A 1.0, the agent, with stand-ins:* Part, Message and the card are real protobuf messages built from the documented 1.0
  field names (so `HasField`, oneofs, `Value`, `ParseDict` and `MessageToDict` behave as in the SDK); the SDK, Starlette, AgentCore
  and uvicorn are stand-ins. A run's stream is the Task first and then only status and artifact updates (never a bare Message),
  in the order working x N, artifact (marked last chunk), completed; an agent error or a missing key ends it as failed with the
  reason and nothing after; the ids on every event; the data parts (mcp_status, tool_call, tool_result) read back as the
  original dicts; history and text parts read from 1.0 wire JSON, including dropped bad entries; the three headers still reach
  the agent run, and a warning is logged when no headers reach the executor; the card has no top-level `url`, has
  `supportedInterfaces` with `JSONRPC` and `1.0`, requires `agentKey` only when keys are set and follows `A2A_PUBLIC_URL`;
  CORS allows `a2a-version` plus the earlier headers; the context builder keeps the headers whether the base `build` is sync
  or async; `main()` passes the card and executor to `build_a2a_app` and adds the CORS middleware after the key middleware.
  Each check was shown to fail on a deliberately broken copy (Task not first, history ignored, `a2a-version` missing from CORS,
  a 0.3 `url` in the card, a sync-only context builder, an error sent as a bare Message, the artifact not marked last).
- *A2A 1.0, the page's real script against a fake DOM, a fake `fetch` and a fake 1.0 agent:* the card fetch carries no
  `A2A-Version` and the JSON-RPC calls do; the key check is `GetTask`; a chat turn is `SendStreamingMessage` with role
  `ROLE_USER`, flat parts and no `kind` anywhere in the body; history goes as a flat data part; a full run becomes the tool
  steps, the MCP status, the answer and done, the same however the stream is cut (CRLF, every chunk size from 1 to 40); failed
  and input-required tasks show the agent's message; states are read as `TASK_STATE_*` and also as lowercase; a JSON-RPC
  error in the stream, and a plain JSON error body instead of a stream, both show the code, message and an older-version hint
  for -32601; keys never appear in an error; the Events screen records the method and the version header; the page still
  names no agent setting. The previous (0.3) page fails 11 of these 15 checks, and eight deliberate breakages of the new page
  (no version header, results read as 0.3 objects, states not normalised, role `user`, plain errors not looked for, the old
  method name, the 0.3 `url` field, the header also on the card fetch) are each caught.
- *Disconnect and the top bar, in the same fake browser:* Disconnect is only on the connected client's row in the Clients
  sheet (not on the other rows, not in the agent panel, not on the home screen) and closes the sheet; the top bar has one
  Clients button with a people icon and the client's name (no pill, no sun-like icon); it shows the home screen with the clients listed and
  none connected, clears the chat, keeps the clients and the events, and marks no client as used last; a reload then stays on
  the home screen and makes no request (also after deleting the client used last); picking a client connects with a clean
  chat; a reply that is still streaming is cancelled (by Disconnect, and by switching clients), the page is not left busy, the
  Events screen records the turn as cancelled, and nothing from it reaches the next chat; on a phone the drawer closes.
- *The Events screen, in the same fake browser:* the request and response of the card fetch, key check and a chat turn
  (URL, headers, body, status, duration), masking and Show keys, a key echoed in an error body, Copy this event and Copy all
  always masked, Clear, the 200-event cap, a failed request that never got a response, a 401, a stream cut off half way,
  opening from the home screen, a row staying open across redraws; the MCP connection line (connected, and failed
  opening itself); and that nothing in the page, its form hints or its warnings names an agent setting or says where an MCP
  key came from (the check fails on the previous version).
- *The agent, with stand-ins for the SDKs:* the init message becoming the `mcp_status` event (prefix stripped, only MCP
  tools, `pending` and `failed`), that event reaching the stream, the log lines for a run, and that no key appears in the
  log or the status (including a key echoed in an MCP error); the MCP key order (`X-Mcp-Key`, then `MCP_API_KEY`, then the caller's `x-api-key`,
  including blanks and whitespace, a missing key, and the model key staying unchanged); that the executor hands the three
  headers to the agent run; that the agent key is in the redaction list; that `main()` adds the CORS middleware after the
  key middleware; that the key middleware on its own would reject a preflight (which is why the order matters).
- *The page's real script against a fake DOM, a fake `fetch` and a fake agent:* first run and the prefilled URL (laptop and
  Codespace); adding, editing, switching and deleting clients and what is stored; connect on load and the failure screen;
  the card and key check requests and their headers; a chat turn streamed with CRLF line endings cut at arbitrary points;
  tool steps, failed steps, failed tasks, HTTP and JSON-RPC errors with keys redacted; history sent as a data part; wrong
  key, unreachable agent, https page with an http address, bad URL; double-click on Save.
- *Render, without Render, and the /ping log filter:* the card's address comes from `RENDER_EXTERNAL_URL` (trailing slash added, an explicit
  `A2A_PUBLIC_URL` wins, blank counts as unset, Codespaces unchanged) and `main()` publishes it to the AgentCore SDK; and
  `render.yaml` parses and passes structural checks (one free Docker web service; the Dockerfile exists; every variable it
  sets is one the agent reads and `.env.example` documents; nothing environment-specific is committed; the key is generated;
  the health path is open; the port matches Render's default; the image binds 0.0.0.0), shown to fail on broken copies. The filter that leaves successful `GET /ping` lines out of the access log drops only
  those (a failing `/ping`, the 401s and every chat turn stay), is attached once, and survives a logging config being applied.
- *`scripts/start.sh`:* the web half (serves `web/`, reports "already running", `--restart`). Its agent half needs the real SDKs.

**Not yet verified, and needs you on a real machine:**

- **A2A 1.0, first start with the real packages.** Everything here was built from the SDK's migration guide and API pages, not run
  against the SDK. In order: (1) `pip install -r requirements.txt` resolves. The `a2a-v1` extra of `bedrock-agentcore` is
  documented, but I did not find which release added it (the pin `>=1.24` assumes this one has it), and `a2a-sdk[all]` assumes
  1.x still has an `all` extra (pip only warns if not). (2) The agent starts: `ParseDict` accepts the card (a wrong field name
  fails at startup and names the field), and `build_a2a_app` in v1 mode accepts the card, the executor and the context
  builder. (3) `curl <agent>/.well-known/agent-card.json` shows `supportedInterfaces` with the right address. (4) The curl
  command in "Call the agent directly" streams a task: the SDK enforces the 1.0 stream rules and raises an error for a stream
  that does not start with the Task. (5) The web app connects, and a chat turn shows the tool steps. (6) **`X-Mcp-Key` reaches the
  agent:** switch the client's MCP key and check that the `run start` log line says `mcp_key_from=X-Mcp-Key header`. The
  context builder was changed to cope with an async base class, and I could not see AgentCore's 1.x one. If the log says
  "No request headers reached the executor", that is the cause.
- **A2A 1.0 wire details I took from documentation, not from this SDK:** the stream result members (`task`, `message`,
  `statusUpdate`, `artifactUpdate`), the strings `ROLE_USER` and `TASK_STATE_*`, the key check `GetTask` with `params.id`, and
  that the SDK accepts `ROLE_USER` in a request. A wrong guess shows up as a JSON-RPC error in the page, which now names it.
- **A2A Inspector on 1.0:** that it connects and sends (it did on 0.3).
- **AI Gateway proxy with 1.0:** that it carries 1.0 calls, passes `A2A-Version` through, and that its CORS policy lists
  `a2a-version` (the policy needs that header added) on both the agent route and the card route.
- **AgentCore Runtime with 1.0:** that the runtime accepts a 1.0 A2A agent, and forwards `A2A-Version` now that it is allowlisted.
- **Integer tool inputs:** a 1.0 data part holds a protobuf Value, so a whole number in a tool call's input (none of the six
  tools take one today) would show as `5.0` in the page.

- **CORS end to end, in a real browser.** That the agent answers the preflight and the page can read its answers and its 401.
  The check: with a wrong Agent API key the page must say "The agent rejected the Agent API key", not "Could not reach ...
  CORS". That depends on the CORS middleware being the outermost layer (added last), which relies on how Starlette orders
  middleware and was not run. Try, in order: the agent on localhost; the Codespace agent with port 9000 Public; the proxy.
- **The `x-api-key` fallback for the MCP key.** That the agent sees the caller's `x-api-key` the same way it sees `X-Mcp-Key`
  (both are read from the request headers the context builder keeps; only `X-Mcp-Key` has been proven), and that your MCP
  gateway accepts the agent key as an MCP key. Test with `MCP_API_KEY` empty and no `X-Mcp-Key`.
- **The MCP connection line and the agent's log, with the real SDK.** The init message and its fields follow the Agent SDK's
  documentation, but the stand-ins I used are mine. Check on a real run that the line shows `connected` and the tool names,
  and that a wrong MCP key shows `failed`, `needs-auth` or no tools. A server that has a cached tool list can read `pending`
  at the start and connect on first use.
- **Response headers on the Events screen,** through the agent (`expose_headers`) and through a gateway proxy, which may not
  expose them.
- **Codespaces:** that a page opened from the Private port 8000 can call the Public port 9000.
- **AI Gateway proxy:** that it answers the CORS preflight; whether it streams tool calls live rather than buffering; and
  `X-Model-Key` through it.
- **The UI on a desktop browser:** colors, spacing, the new client list and form, the Events panel (long bodies, many rows), the agent panel open and closed (closed, the chat must use the full width; it once collapsed to nothing there), the top bar's Clients button with its people icon and the connected client's name (long names cut with an ellipsis), tables, light and dark mode.
- **The UI on an iPhone (Safari):** (the first-launch home screen, with the dark brand block whole above the form, has been seen working on an iPhone in Chrome; a tall form once squeezed that block to nothing, and the grid now keeps its row at full height); the page fits without scrolling sideways; the clients sheet slides up from the bottom, its
  list and form are usable, and focusing a field does not zoom; after the first message the send button, the message box's
  right edge and the tool steps stay inside the screen (the prompt chips once widened the chat past a phone; the CSS now
  lets that column shrink, checked by reading the CSS, not by rendering it) and the message box hint fits on one line; the
  Events panel fills the screen and its rows and buttons are easy to tap; the top bar and message box clear the notch and the home
  indicator; the agent panel slides in and a tap outside closes it; the connected client's row in the Clients sheet (name, Edit, Delete, Disconnect) wraps cleanly on a narrow screen; prompt chips and tables scroll sideways.
- **Proxy model route:** needs `/v1/messages`, streaming, tool use, `x-api-key`, and your `MODEL_NAME`.
  If it rejects `anthropic-beta` headers, keep `MODEL_DISABLE_BETAS=1`.
- **Render:** whether it accepts an empty value for a prompted variable (a single space is the fallback); whether 512 MB is
  enough over time (the logs show no restart, but not memory: watch the service's Metrics); that the event stream arrives live
  through Render's proxy; and what the web app shows for the first request after a sleep.
- **The /ping log filter in a real run:** that uvicorn keeps the filter when it sets up its logging (checked here with the
  standard library's logging config, not uvicorn itself).
- **AgentCore:** that the SDK's bundled CLI starts in an ARM64 AgentCore container, that the runtime can reach your
  gateway (and Anthropic on the direct route), and that the runtime forwards `X-Api-Key` once allowlisted.
- **MCP transport `sse`:** only `http` has been exercised.
- **Read/write labels** in the UI are a name heuristic (`Get*` = read).
