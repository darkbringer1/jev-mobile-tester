# Local Laya deployment

Deployed and tested on September 25, 2026, on an Apple M5 Pro with 48 GiB memory,
macOS 27.0. Laya runs on the GPU through the independent `laya-mlx==0.2.0` runtime.

## Running service on this Mac

- Health: <http://127.0.0.1:8081/health>
- Inference: `POST http://127.0.0.1:8081/v1/systemone` with `state` and `questions`.
- LaunchAgent: `~/Library/LaunchAgents/com.jev-mobile.laya.plist`.
- Installed wheel, isolated environment, weights and logs:
  `~/Library/Application Support/jev-mobile/laya/`.
- Logs: `server.stdout.log` and `server.stderr.log` in that directory.
- Starts at login, restarts if it exits, and uses `HF_HUB_OFFLINE=1`.
- Model: `aac6fef/laya-typed-decisions-mlx`, revision
  `f9e501c2080cc57c13d6887820329758f5351125` (843 MB weights, FP16).

The installed service is a wheel snapshot, independent of this checkout's `.venv`.
Editing source or syncing development dependencies does not update it. Rebuild the wheel
and install it into the service environment before restarting to deploy later changes.

```sh
# Status / restart
launchctl print "gui/$(id -u)/com.jev-mobile.laya"
launchctl kickstart -k "gui/$(id -u)/com.jev-mobile.laya"

# Stop; start again
launchctl bootout "gui/$(id -u)/com.jev-mobile.laya"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.jev-mobile.laya.plist"
```

To uninstall, stop the agent and remove its plist and the Application Support directory.
No global Python packages, Maestro sources, or paid TypeSafe calls were used.

## Use it from Jev Mobile

```sh
uv sync --locked --extra laya
uv run --extra laya python examples/laya_smoke.py
uv run --extra laya jev-mobile serve --backend laya \
  --device YOUR_SIMULATOR_UDID --app-id com.apple.Preferences
```

Use [the local MCP example](../examples/mcp-laya.json) in a stdio MCP client. No API key
is required. `--backend laya` or `JEV_BACKEND=laya` opts in explicitly; the default Jev
backend remains available. The optional `LAYA_URL` / `--laya-url` accepts a loopback HTTP
origin only. There is no automatic cloud fallback.

On another Apple Silicon Mac, start the server in a separate terminal:

```sh
uv sync --locked --extra laya
uv run --extra laya jev-mobile laya-serve
```

The first run downloads the pinned checkpoint to the Hugging Face cache. A manual server
must remain running; do not start it on port 8081 while the LaunchAgent is already serving.

## Test commands

Run from the `jev-mobile` directory. This first command makes real local model requests
without opening an app or connecting to Maestro:

```sh
uv run --extra laya python examples/laya_smoke.py --output runs/laya-classification
```

Check `runs/laya-classification/smoke.json`; the classification examples should report
`"passed": true`. To attempt the Settings goal on a booted English-language simulator:

```sh
uv run --extra laya jev-mobile devices
uv run --extra laya python examples/laya_smoke.py \
  --device YOUR_SIMULATOR_UDID --output runs/laya-ios-test
```

This script always targets `com.apple.Preferences`. It exits 0 only if the classification
checks and, when requested, the verified mobile goal pass. Results are in `smoke.json`
and nested run directories. The mobile test failed in recorded validation below.
To target a different app, follow [your-app.md](your-app.md) rather than editing this smoke test.

## Update the installed service

These commands apply to the existing installation on this Mac, after validating source
changes. Run from the `jev-mobile` checkout:

```sh
uv build
launchctl bootout "gui/$(id -u)/com.jev-mobile.laya"
uv pip install \
  --python "$HOME/Library/Application Support/jev-mobile/laya/.venv/bin/python" \
  --reinstall-package jev-mobile \
  'dist/jev_mobile-0.1.0-py3-none-any.whl[laya]'
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.jev-mobile.laya.plist"
```

Use the actual wheel filename if the project version changes. Wait for model loading, then
check `/health` and rerun the classification smoke test. The install step may need network
access for changed dependencies; the running inference service still uses Hub offline mode.
For inference errors, inspect `server.stderr.log` under the Application Support directory.

## What was tested

- 51 offline tests pass, including local HTTP behavior, no credential forwarding,
  unknown/low-confidence choices, redirects, action mapping, nested labels, field values,
  the existing agent guards, and MCP protocol behavior. Ruff and wheel/sdist builds pass.
- The deployed service starts from cached weights with Hub offline mode enabled.
- Three live classification examples (billing, technical support, sales) all selected
  the expected answer. The sales answer had low confidence: correct choice alone does
  not establish reliability.
- Twelve repeated real requests exercised `choice`, `score`, and `noul`. All selected
  billing and returned bounded numeric outputs. The last eleven requests had a median
  **12.11 ms** server inference time; the first took **56.96 ms**. This tiny repeated
  workload is not a mobile performance or accuracy benchmark.
- An oversized request returns HTTP 422 before inference, avoiding silent state truncation.
- The real stdio MCP transport connects to Maestro and returns compact outcomes.
  A separate full local-backend MCP goal timed out waiting for Maestro after 120 seconds,
  before model inference; its error and report were persisted correctly. The direct
  simulator smoke runs below did reach Laya. Full MCP goal completion is not validated.

Artifacts remain in ignored `runs/laya-offline-smoke/`, `runs/laya-smoke-actions/`,
`runs/laya-smoke-diagnostic-03/`, and `runs/laya-mcp/`. They can contain simulator data.

## Mobile navigation result: not successful

The real model attempted Settings → General → About on the iPhone 17 Pro / iOS 26.5
simulator. At the unchanged default confidence threshold of 0.5, the local policy rejected
the model's low-confidence answer before executing a goal action.

A separate diagnostic at 0.3 repeatedly selected the wrong Settings item and exhausted
its eight-step budget. It did not satisfy the completion assertions. The default remains
0.5; lowering the threshold is not a solution for inaccurate choices.

Laya's action adapter uses a single readable choice list, keeps observed target IDs locally,
collapses only nested duplicate labels, and preserves field values and recent actions.
This is an experimental integration, not a verified replacement for hosted Jev. Larger
choice sets and multi-step UI goals need further evaluation, better task decomposition,
or domain-specific training. No TypeSafe comparison was performed.

Sources: [Laya upstream](https://github.com/NandhaKishorM/laya),
[MLX runtime](https://github.com/mizorewww/laya-mlx),
[checkpoint](https://huggingface.co/aac6fef/laya-typed-decisions-mlx).
