# Jev Mobile

Standalone Python project. Initial platform: iOS simulator. Treat Maestro as an external
dependency; do not modify a separate Maestro checkout as part of routine work here.

## Development

Run `uv sync --locked`, `uv run pytest`, `uv run ruff check .`, and `uv build`
(`make test` runs the first three). The Makefile is the user-facing install path;
`make laya` redeploys the installed LaunchAgent from source.
For Laya development, retain optional dependencies with `uv sync --locked --extra laya`
and `uv run --extra laya ...` for pytest, Ruff, and examples.
Do not make paid TypeSafe calls without authorization and a supplied API key.
Never commit `.env`, `runs/`, credentials, or personal simulator screenshots.

## Architecture

- `screen.py`: normalize the hierarchy and derive observed selectors.
- `policy.py`: construct and validate Jev choices.
- `laya.py`: compact readable action choices and loopback-only Laya HTTP policy.
- `laya_server.py`: pinned MLX checkpoint, serialized GPU inference, and context checks.
- `maestro.py`: own the persistent Maestro MCP session.
- `agent.py`: execute the bounded observation/decision/action loop.
- `cli.py`: command inputs and artifacts.
- `server.py`: compact MCP facade: direct `devices`, `screen`, `screenshot`, `run_flow`,
  and goal-level `run_goal`, `run_report`.
- `clients.py`: `jev-mobile setup` registration for Claude Code, Codex, and Cursor.

Keep model output restricted to offered choices. Keep ordinary MCP responses compact
and detail retrieval explicit. Preserve independent completion assertions.

## Integration notes

The tested Maestro 2.10.0 build supports `mcp --no-viewer`. Disabling its optional viewer
avoided a SimulatorKit load failure in the local validation environment. Every MCP `run`
call needs a full YAML flow with `appId`, `---`, and commands. The tested iOS hierarchy
omits control types; `values`/`--values` explicitly identifies editable fields by ID.

`uv run python examples/check_mcp.py` tests the real stdio transport without paid calls
or device actions. `examples/ios_settings_smoke.py` performs scripted device navigation.
Clearly distinguish offline tests, scripted smoke tests, and live Jev evaluation.
Do not advertise model reliability, latency, or token savings from synthetic checks.

## Local Laya handoff

Start with `docs/handoff.md`, then `docs/laya.md` and `docs/your-app.md`.
The existing user LaunchAgent `com.jev-mobile.laya` serves `127.0.0.1:8081` from a separate
wheel install in `~/Library/Application Support/jev-mobile/laya/`, with Hub offline mode.
Source edits do not update that installed service; deployment steps are in `docs/laya.md`.
Check `/health` before starting another server on the same port.

`--backend laya` (or `JEV_BACKEND=laya`) requires no TypeSafe key. There is no cloud fallback.
The real model passed three classification examples but failed the Settings navigation goal;
a separate full MCP goal timed out in Maestro before inference. Preserve these distinctions.
Do not lower confidence or remove completion assertions just to make a smoke test pass.
The user's own app integration has not been configured or validated yet.
