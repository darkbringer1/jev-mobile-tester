# Jev Mobile

Standalone Python project. Initial platform: iOS simulator. Treat Maestro as an external
dependency; do not modify a separate Maestro checkout as part of routine work here.

## Development

Run `uv sync --locked`, `uv run pytest`, `uv run ruff check .`, and `uv build`.
Do not make paid TypeSafe calls without authorization and a supplied API key.
Never commit `.env`, `runs/`, credentials, or personal simulator screenshots.

## Architecture

- `screen.py`: normalize the hierarchy and derive observed selectors.
- `policy.py`: construct and validate Jev choices.
- `maestro.py`: own the persistent Maestro MCP session.
- `agent.py`: execute the bounded observation/decision/action loop.
- `cli.py`: command inputs and artifacts.
- `server.py`: compact MCP facade with `devices`, `run_goal`, and `run_report`.

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
