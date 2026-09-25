# Initial validation

Local Laya deployment and real-model results are documented separately in [laya.md](laya.md).
The local inference service passes its smoke checks; the tested mobile navigation goal
did not complete successfully.

Validated locally on September 25, 2026:

- 37 offline tests pass, covering hierarchy parsing, compatible action targets, malformed
  model responses, confidence thresholds, stale observations, bounded execution,
  completion assertions, the TypeSafe HTTP contract, and Maestro flow formatting.
- Ruff checks pass.
- Source distribution and Python wheel build successfully.
- A persistent MCP connection works with the locally installed Maestro 2.10.0 build.
- Hierarchy parsing works on an iPhone 17 Pro simulator running iOS 26.5.
- The scripted `examples/ios_settings_smoke.py` test passes:
  Settings → General → About, verifying About and iOS Version with Maestro assertions.

The selected Xcode installation was Xcode 27 beta. Maestro's optional viewer could not
load SimulatorKit; `mcp --no-viewer` allowed device observation and command execution.
No global Xcode setting was changed.

Device testing found two integration requirements now reflected in the implementation:
full YAML flow headers for MCP execution, and handling native accessibility rows with
nested labels. Text fields require explicit ID bindings because this Maestro iOS
hierarchy does not expose control types.

No live TypeSafe call was made. These checks establish local integration and offline
behavior, not Jev goal completion reliability, latency, or comparative performance.
Android and other Maestro versions remain unvalidated.

## Local MCP facade

- The server exposes three tools: `devices`, `run_goal`, and `run_report`.
- Protocol tests exercise a complete fake-model goal, verify one text response without
  duplicate structured content, and keep the sample outcome below 256 bytes.
- Tests cover missing credentials, error persistence, timeout, cancellation, concurrent
  requests, app overrides, report bounds, and traversal rejection.
- `uv run python examples/check_mcp.py` passed against the actual stdio server and local
  Maestro. It found four connected devices and returned an 81-byte missing-key response.
  The child process had no TypeSafe key; no paid requests or device actions occurred.
- Source distribution and wheel build after the server addition.

The full Jev-controlled MCP goal still needs live API validation. Synthetic response
lengths do not establish percentage token savings or total end-to-end cost savings.
