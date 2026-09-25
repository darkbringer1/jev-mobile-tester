# Local MCP server

Quick install: `uv tool install --editable .` from this checkout, then run
`jev-mobile setup --app-id YOUR_BUNDLE_ID` in your app repository. See the README.

Expose Jev Mobile to an AI agent as six small tools: three direct Maestro tools and three goal tools. The agent supplies a goal; the
server runs observations, model decisions, target validation, and Maestro actions internally.
The caller gets one compact outcome. This reduces the caller's screen data and tool-call
traffic. It does not establish measured total token or cost savings.

```text
AI agent → run_goal → local Jev Mobile MCP → local Laya OR hosted Jev
                                         → Maestro MCP → simulator
         ← short outcome                 ← internal observe/decide/act loop
```

## Connect an agent

### Local Laya

Check `curl --fail http://127.0.0.1:8081/health` first. The development Mac already has
a background Laya service; other Macs need the [local server setup](laya.md).

Use [examples/mcp-laya.json](../examples/mcp-laya.json). Replace the repository path,
`JEV_DEVICE_ID`, and `JEV_APP_ID` with your own values. No `.env` or TypeSafe key is needed.
The MCP client starts the controller; it does not start the Laya inference server.

```sh
uv run --extra laya jev-mobile serve --backend laya \
  --device YOUR_SIMULATOR_UDID --app-id com.example.myapp
```

If your agent is opened in your app's repository, keep `--directory` pointing to the
separate `jev-mobile` checkout. Use [the app guide](your-app.md) to find the bundle ID
and supply text-field values. The server has no deadline by default, because flows can wait
on slow app network calls; long runs return a `run_id` instead of outliving the client's
tool timeout. Do not register a separate Maestro MCP server next to this one.

### Hosted Jev

Copy `.env.example` to `.env` and set `TYPESAFE_API_KEY`. The server uses TypeSafe's
hosted Jev API in this mode; the MCP server itself runs locally.
Without a key, device discovery and saved reports work; goals return `unavailable`
before any device action. Restart the server after changing credentials.

Use [examples/mcp.json](../examples/mcp.json) in a client that supports stdio MCP. Replace
both repository paths, the simulator ID, and the app ID. Use an absolute `uv` executable
path if your desktop agent cannot find it. The client starts and stops the server.

Equivalent launch command, from this repository:

```sh
uv run --env-file .env jev-mobile serve \
  --backend jev --device YOUR_SIMULATOR_UDID --app-id com.example.myapp
```

The default device and app can also come from `JEV_DEVICE_ID` and `JEV_APP_ID`. They let
agents omit repeated identifiers. Register this facade alone when you do not need the
raw Maestro tools in the agent: otherwise both sets of tool schemas consume context.
Do not run another controller against the same simulator during a goal.

## Tools

| Tool | Use |
| --- | --- |
| `devices()` | Connected devices only; omit when a default is configured. |
| `screen(device_id?, raw?)` | Compact visible elements as `{text, id, value}`. `raw` returns Maestro's hierarchy, only to debug a missing element. |
| `screenshot(device_id?)` | Image of the current screen. Large; prefer `screen`. |
| `run_flow(commands? \| files?, device_id?, app_id?, env?, wait=45)` | Run a YAML list of Maestro steps (the server adds the `appId` header) or existing flow files. Returns `passed` or the failing flow's reason, a `run_id`, and the resulting `screen`. Batch predictable steps into one call. |
| `run_goal(goal, expect_text?, device_id?, app_id?, values?, max_steps?, wait=45)` | Execute the bounded loop; return status, run ID, step count, duration, and reported model usage. |
| `run_report(run_id, last_steps=3, wait=0)` | Retrieve a compact summary, up to ten recent decisions, the full error text, and the local exported flow path. `wait` blocks for a running run (values above 110 s are capped). |
| `run_cancel(run_id)` | Stop a running flow or goal, restart Maestro so its driver stops, and free the device. |

Example agent call when defaults are configured:

```json
{"goal":"Open General, then About","expect_text":["About","iOS Version"]}
```

Illustrative response (not a measured live Jev run):

```json
{"status":"verified","run_id":"93ae46c3ed3b43cb9f48d3ebc482b162","steps":4,"ms":12345,"jev_tokens":{"input":1200,"output":80}}
```

`verified` means the specified Maestro text assertions passed after the model chose DONE.
Without assertions, DONE returns `done_unverified`. Other outcomes include `blocked`,
`step_limit`, `error`, `timeout`, `running`, `cancelled`, `busy`, `invalid`, and
`unavailable`. Agents must read
`status`; receiving a tool response alone does not mean the goal succeeded.

Field input uses `values`, a map of exact accessibility/resource IDs to literal text.
No text-generation model is required. In this initial version, the agent or project
configuration must already know those field IDs.

## Output and execution limits

Without `device_id` or a configured default, tools use the single connected device and
return an error when several are connected. Direct tools share the run lock, so they
return `busy` (with the active `run_id`) while a flow or goal runs.

Only one Maestro-driven iOS simulator can run at a time on a Mac. Maestro's MCP server
(2.10.0) always serves its iOS driver on `127.0.0.1:22087`, and simulators share the Mac's
loopback, so a second driver on another simulator gets commands meant for the first. Every
iOS device action therefore first checks for a Maestro driver that another process started
(a separately registered Maestro MCP server, `maestro test`, another agent session) and
returns `busy` naming its pid. Run comparisons between Maestro-based agents one at a time.

Normal tool output contains no hierarchy, screenshots, generated YAML, or full step log.
Each result has one text block, without a duplicate `structuredContent` payload. Details
live under `~/Library/Application Support/jev-mobile/runs/<run_id>/` (or `--output`/`JEV_RUNS`): `steps.jsonl`, `result.json`, and `flow.yaml`. Ask for
`run_report` only when the extra context is useful. Local files can contain entered values.

One persistent Maestro connection and one HTTP client are reused. Runs are serialized;
concurrent runs get `busy`. Each goal resumes its app without clearing state or
implicitly stopping it. The default limit is 30 decisions (maximum 100) with no time
deadline; set one with `--timeout SECONDS` if you want runs bounded.

`run_flow` and `run_goal` run in the background. A call waits up to `wait` seconds
(default 45, below common client tool timeouts) and returns the outcome, or
`{"status":"running","run_id":...}`. Then call `run_report(run_id, wait=60)` until the
status changes. An abandoned tool call does not stop the run; `run_cancel` does.
While waiting, the server sends MCP progress notifications for clients that use them.

Cancellation and `--timeout` restart the Maestro process, because Maestro keeps executing
a flow after its caller gives up; the restart also ends its xcodebuild driver. The next
call starts a fresh driver. Cancellation saves a cancelled report. A command already sent
to the device may have taken effect; cancellation is not rollback. Retries are new runs,
so inspect failures before repeating goals that change data.

The legacy field name `jev_tokens` also carries Laya's reported input/output token counts;
local Laya inference does not incur TypeSafe API charges. It totals usage on completed decisions;
failed or interrupted provider
calls may not return usage and are not counted. It is not the calling agent's token count.
The offline protocol test keeps its sample goal response below 256 bytes. Actual token
counts depend on the client model's tokenizer and any host-added protocol formatting.

## Verify without a TypeSafe key

```sh
uv run pytest
uv run python examples/check_mcp.py
```

The second command starts the real stdio server and Maestro, lists connected devices,
and verifies missing-key behavior. It explicitly removes the API key from its child
process. It neither sends a paid model request nor performs a device action.

To test real local inference separately:

```sh
uv run --extra laya python examples/laya_smoke.py
```

That command does not connect to Maestro or act on a device unless `--device` is supplied. Classification
passed in local validation; Settings navigation did not. See [the test results](laya.md).
