# Use Jev Mobile with your existing app

Run these commands from the **jev-mobile checkout**. Keep building and installing your
app with its current Xcode, Tuist, Flutter, React Native, or other project workflow.
Jev Mobile controls an installed app through Maestro; Laya supplies local action decisions.
No SDK or Laya model needs to be embedded in your app.

This guide describes configuration. The user's own app has not been selected or tested
with this integration yet. Start with a small navigation task in a test app session.

## 1. Check the local services

```sh
uv sync --locked --extra laya
curl --fail http://127.0.0.1:8081/health
uv run --extra laya python examples/laya_smoke.py
uv run --extra laya jev-mobile doctor
uv run --extra laya jev-mobile devices
```

The health response should say `ready`, the classification smoke should say `passed: true`,
and `doctor` checks the Maestro MCP tool contract. These do not prove that a mobile goal
will succeed. If Laya is unavailable, use the [service instructions](laya.md).

## 2. Choose the simulator and installed app

Boot your simulator and build/run your app in it using your normal development workflow.
Copy the simulator ID returned by `jev-mobile devices`, or list booted simulators directly:

```sh
xcrun simctl list devices booted
```

Use the app target's **bundle identifier**, not its display name, scheme, or directory name.
For example, replace `com.example.yourapp` below with your actual bundle ID. You can inspect
the installed apps on the selected simulator with:

```sh
xcrun simctl listapps YOUR_SIMULATOR_UDID
```

The previous Settings test used simulator `0768F6BA-68FE-498C-8940-73D0A81976A2`
(iPhone 17 Pro, iOS 26.5). Rediscover available devices rather than assuming this ID is current.

Open your app to a known starting screen, then inspect what Maestro can observe:

```sh
uv run --extra laya jev-mobile inspect --device YOUR_SIMULATOR_UDID
```

`inspect` reads the foreground app; it does not launch the bundle ID for you.
For controls without usable labels, add stable accessibility identifiers in the app
using its existing conventions. This controller acts on observed labels and IDs.

## 3. Try one bounded goal

```sh
uv run --extra laya jev-mobile run --backend laya \
  --device YOUR_SIMULATOR_UDID \
  --app-id com.example.yourapp \
  --goal 'Open the Profile screen' \
  --expect-text 'Profile' \
  --max-steps 5 \
  --output runs/my-app-profile
```

Replace the goal and assertion with text from your app. The CLI launches the app; the MCP
facade instead requests launch with `stopApp: false` to continue an existing session.
Choose a final assertion that identifies the destination, rather than text already visible
on the starting screen. Add multiple `--expect-text` flags when needed.

Only `verified` means the model chose DONE and Maestro independently passed the text checks.
`done_unverified`, `blocked`, `step_limit`, `error`, or `timeout` are not verified success.
CLI exit code 0 means verified, 2 means an unverified loop outcome, and 1 indicates an error.

Completed CLI loops write `steps.jsonl`, `result.json`, and `flow.yaml` in the output folder.
Early CLI errors can leave a partial trace without a result file. Use a separate output
directory per experiment if you want to retain prior runs. MCP runs persist error reports
under unique run IDs.

The existing `examples/laya_smoke.py --device ...` specifically tests Apple Settings;
changing its device does not change the app. Use `jev-mobile run` or MCP for your own app.

## 4. Supply text-field values

Free-form text generation is not implemented. Create an ignored file such as
`runs/my-app-values.json` with exact observed field IDs and test values:

```json
{"profile_name_field": "Demo User"}
```

Add `--values runs/my-app-values.json` to the CLI run and use a goal such as
`Enter the supplied name and save the profile`. Replace the ID and final assertion with
those from your app. In MCP, pass the same object as `values`. Field bindings are explicit
because the tested iOS hierarchy does not expose editable control types reliably.

## 5. Connect your AI agent

Quickest path, from the jev-mobile checkout:

```sh
make install
make connect APP=com.example.yourapp PROJECT=/path/to/your-app DEVICE=YOUR_SIMULATOR_UDID
```

`make install` already registers the server user-wide; `connect` adds this app's default
bundle ID for the project. Both configure Claude Code (every discovered config, asking
for each), Codex, and Cursor when they are installed. Restart open agent
sessions afterwards. `--device` is optional when exactly one device is connected.
Remove any separate Maestro MCP server from those clients; `screen`, `screenshot`, and
`run_flow` cover direct control. Manual alternative:

Copy [examples/mcp-laya.json](../examples/mcp-laya.json) into the MCP configuration supported
by your agent. Replace these values:

| Configuration | Value |
| --- | --- |
| `command` | `uv`, or its absolute path if the client cannot find it |
| `args`: `--directory` | Absolute path to the **jev-mobile** checkout |
| `JEV_DEVICE_ID` | Selected simulator UDID |
| `JEV_APP_ID` | Your installed app's bundle ID |
| `LAYA_URL` | `http://127.0.0.1:8081` for the existing service |

This works even when the agent's workspace is your app repository. The client manages the
Jev Mobile stdio process, and the existing LaunchAgent manages Laya. `.env` is not loaded
automatically; the example uses explicit arguments/environment variables. If you choose
to use `.env`, load it with `uv run --env-file .env ...`.

With device/app defaults configured, ask the agent to call:

```json
{
  "goal": "Open the Profile screen",
  "expect_text": ["Profile"],
  "max_steps": 5
}
```

The tool is `run_goal`. Follow failures with `run_report` using the returned `run_id`.
The server has no deadline by default; give the client a long tool timeout too.
Use one controller at a time on the simulator. Keep existing Maestro YAML tests for
deterministic regression checks while evaluating this goal-driven path.

## Diagnose a failure

| Symptom | Next check |
| --- | --- |
| Connection refused on 8081 | Check `/health`, restart the LaunchAgent, then inspect its logs |
| Missing TypeSafe key | Explicitly select `--backend laya`; the default backend is Jev |
| Wrong app or device | Recheck the UDID, installed bundle ID, and foreground app |
| Maestro timeout before any model decision | Check `doctor`, device availability, and concurrent controllers; this happened during local testing |
| Confidence below 0.5 | Inspect the goal and offered UI actions; a lower diagnostic threshold produced wrong actions in testing |
| HTTP 422 from Laya | The server rejected the request; check its response/logs, including context-size limits |
| `step_limit` or repeated taps | Inspect the saved trace; simplify the task before retrying |
| Assertion failed | Check the actual destination and exact visible text; do not mark the goal successful |

Laya's real Settings goal did not complete in validation. Neither this guide nor the passing
classification smoke establishes that your app can be automated reliably. See
[the recorded results](laya.md) and [the agent handoff](handoff.md).
