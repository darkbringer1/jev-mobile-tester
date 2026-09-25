# Agent handoff: local Laya integration

Read `AGENTS.md` first. Work from this standalone **jev-mobile** repository, not the
separate Maestro checkout. The latest user request was to document usage so another
agent can take over here. Their next topic is adapting the local setup to their current app;
the app identity, bundle ID, desired goal, and agent-client configuration have not been chosen.
No app source or external agent configuration has been changed for that integration.

## Start here

1. Read [README.md](../README.md), [your-app.md](your-app.md), and [laya.md](laya.md).
2. Inspect `git status` and recent history: the Laya implementation, tests, and docs were
   committed locally at the user's request. Preserve any subsequent working-tree changes.
   No push was made during setup.
3. Check `http://127.0.0.1:8081/health` before starting any server.
4. For the next app task, discover its existing build/install workflow and bundle ID,
   list available simulators, inspect one screen, and try a small bounded goal with an
   independent final assertion. Obtain missing app identity from the user if it is unclear.

## Implemented

- `laya.py`: one readable action question, observed target mapping, nested-label deduplication,
  explicit field values, choice validation, and a loopback-only HTTP endpoint.
- `laya_server.py`: local MLX inference on a single worker, pinned typed-decisions checkpoint,
  `/health`, `/v1/systemone`, and oversized-state rejection.
- CLI/MCP: `--backend laya` and `--laya-url`; `JEV_BACKEND` / `LAYA_URL` equivalents.
  Jev remains the default, and there is no paid-provider fallback.
- `laya-serve`: foreground inference service. `serve --backend laya`: separate stdio
  controller exposing `devices`, `run_goal`, and `run_report`.
- Optional dependencies and lockfile, local-policy tests, smoke example, and MCP example.

The user's Mac has a LaunchAgent named `com.jev-mobile.laya` serving port 8081 from a
separate wheel installation under `~/Library/Application Support/jev-mobile/laya/`.
It uses cached weights with `HF_HUB_OFFLINE=1`. The Desktop checkout stalled when launched
directly in the background, so the deployed copy deliberately lives outside Desktop.
Source edits do not change the installed server; see [redeployment](laya.md#update-the-installed-service).

## Evidence and limits

- 51 offline tests, Ruff, and wheel/sdist builds passed during implementation.
- The real model passed three small classification examples. `choice`, `score`, and `noul`
  returned valid values across repeated requests. Oversized state returned HTTP 422.
- Tiny repeated inference measurements are recorded in `docs/laya.md`; they are not
  mobile latency, reliability, or comparative token-savings measurements.
- The real Settings → General → About goal failed: the default 0.5 confidence guard
  rejected a decision. A separate 0.3 diagnostic made incorrect repeated taps and hit the
  step limit. The default was not lowered.
- A full stdio MCP goal separately timed out in Maestro after 120 seconds before inference.
  Protocol response/report persistence worked, but end-to-end goal completion did not.
- No paid TypeSafe call was made. Hosted Jev comparison, Android, and the user's own app
  remain unvalidated.

Raw evidence is in ignored `runs/laya-offline-smoke/`, `runs/laya-smoke-actions/`,
`runs/laya-smoke-diagnostic-03/`, and `runs/laya-mcp/`. Do not commit simulator data or credentials.

## Validate changes

```sh
uv sync --locked --extra laya
uv run --extra laya pytest
uv run --extra laya ruff check .
uv build
uv run --extra laya python examples/laya_smoke.py
```

The last command needs the running local service and performs real model requests without
device actions. `examples/check_mcp.py` checks stdio/device discovery and missing-key handling
without paid calls or device actions. `examples/ios_settings_smoke.py` is scripted navigation;
`examples/laya_smoke.py --device ...` is model-controlled navigation. Keep their claims separate.

Preserve offered-choice validation, stale-screen checks, bounded runs, the default confidence
guard, and independent completion assertions. Diagnose model quality and Maestro transport
problems separately. Do not weaken checks to turn a failed goal into a reported success.
