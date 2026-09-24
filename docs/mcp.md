# Local MCP server

Expose Jev Mobile to an AI agent as three small tools. The agent supplies a goal; the
server runs observations, Jev decisions, target validation, and Maestro actions internally.
The caller gets one compact outcome. This reduces the caller's screen data and tool-call
traffic. It does not eliminate the tokens used by Jev or establish measured cost savings.

```text
AI agent → run_goal → local Jev Mobile MCP → Jev API + Maestro MCP → simulator
         ← short outcome                 ← internal observe/decide/act loop
```

## Connect an agent

Copy `.env.example` to `.env` and set `TYPESAFE_API_KEY`. The server uses TypeSafe's
hosted Jev API; the MCP server itself runs locally. No model weights are served locally.
Without a key, device discovery and saved reports work; goals return `unavailable`
before any device action. Restart the server after changing credentials.

Use [examples/mcp.json](../examples/mcp.json) in a client that supports stdio MCP. Replace
both repository paths, the simulator ID, and the app ID. Use an absolute `uv` executable
path if your desktop agent cannot find it. The client starts and stops the server.

Equivalent launch command, from this repository:

```sh
uv run --env-file .env jev-mobile serve \
  --device YOUR_SIMULATOR_UDID --app-id com.example.myapp
```

The default device and app can also come from `JEV_DEVICE_ID` and `JEV_APP_ID`. They let
agents omit repeated identifiers. Register this facade alone when you do not need the
raw Maestro tools in the agent: otherwise both sets of tool schemas consume context.
Do not run another controller against the same simulator during a goal.

## Tools

| Tool | Use |
| --- | --- |
| `devices()` | Connected devices only; omit when a default is configured. |
| `run_goal(goal, expect_text?, device_id?, app_id?, values?, max_steps?)` | Execute the bounded loop; return status, run ID, step count, duration, and reported Jev usage. |
| `run_report(run_id, last_steps=3)` | Retrieve a compact summary, up to ten recent decisions, and the local exported flow path. |

Example agent call when defaults are configured:

```json
{"goal":"Open General, then About","expect_text":["About","iOS Version"]}
```

Illustrative response (not a measured live Jev run):

```json
{"status":"verified","run_id":"93ae46c3ed3b43cb9f48d3ebc482b162","steps":4,"ms":12345,"jev_tokens":{"input":1200,"output":80}}
```

`verified` means the specified Maestro text assertions passed after Jev chose DONE.
Without assertions, DONE returns `done_unverified`. Other outcomes include `blocked`,
`step_limit`, `error`, `timeout`, `busy`, `invalid`, and `unavailable`. Agents must read
`status`; receiving a tool response alone does not mean the goal succeeded.

Field input uses `values`, a map of exact accessibility/resource IDs to literal text.
No text-generation model is required. In this initial version, the agent or project
configuration must already know those field IDs.

## Output and execution limits

Normal tool output contains no hierarchy, screenshots, generated YAML, or full step log.
Each result has one text block, without a duplicate `structuredContent` payload. Details
live under `runs/mcp/<run_id>/`: `steps.jsonl`, `result.json`, and `flow.yaml`. Ask for
`run_report` only when the extra context is useful. Local files can contain entered values.

One persistent Maestro connection and one HTTP client are reused. Runs are serialized;
concurrent goals get `busy`. Each goal resumes its app without clearing state or
implicitly stopping it. The default limit is 30 decisions (maximum 100) and 180 seconds
including launch and verification. Change the server deadline with `--timeout` (up to
600 seconds), and set the agent client's tool timeout slightly higher. No polling is
needed: one tool call waits for the outcome.

Cancellation stops the local loop and saves a cancelled report. A command already sent
to the device may have taken effect; cancellation is not rollback. Retries are new runs,
so inspect failures before repeating goals that change data.

`jev_tokens` totals usage reported on completed decisions; failed or interrupted provider
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
