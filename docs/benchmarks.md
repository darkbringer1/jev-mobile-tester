# jev-mobile vs Maestro MCP — agent benchmarks

App under test: a production iOS HR app (SwiftUI, mock backend), Debug build.
Simulators: iPhone 17, iOS 26.5, freshly created per run, app-only install.
Agents: Claude Haiku sub-agents, one per runner, identical prompts except tool names.
Tokens: real `usage` fields from each agent transcript, deduped per message id
(input incl. cache reads + output). All numbers 2026-09-25.

## Method notes that changed the results

- **Run runners one at a time.** Both start the iOS Maestro driver on host port 22087;
  simulators share the Mac's network, so two drivers on two sims collide. Earlier parallel
  runs failed jev's file runs for this reason (hidden then by truncated errors).
- Restart the MCP server after changing jev: an editable install only loads new code in a
  new process. `/mcp` → Reconnect; check the process start time.
- Clean state per run: new sims, clean build, stale `xcodebuild test-without-building`
  drivers killed, port 22087 free.

## Results

### Exploratory + one flow (run a 65-step flow, then navigate to a detail and read a field)

| | Maestro MCP | jev (post-fixes) |
|---|---|---|
| Result | pass / pass | pass / pass |
| Wall time | 3.6 min | 3.2 min |
| Turns | 13 | 10 |
| Total tokens | 591k | **429k (−27%)** |
| Screen payload | 15.5k chars | 2.9k chars |

### Smoke suite, 12 flows, one run call per flow

| | Maestro MCP | jev (screen on every pass) | jev (one-line results) | jev (+100 s wait, e9bef31) |
|---|---|---|---|---|
| Result | 12/12 | 12/12 | 12/12 | 12/12 |
| Wall time | 11.2 min | 11.3 min | 11.1 min | 11.3 min |
| Turns | 16 | 17 | 17 | **16** |
| Result payload | ~430 chars/flow | ~2,000 chars/flow | 126 chars/flow | **84 chars/flow** |
| Total tokens | 692k | 793k (+15%) | 731k (+5.6%) | **684k (−1.2%)** |

The one-line column's +5.6% was exactly one extra turn: the cold first flow returned
`running` and needed one `run_report`. With the 100 s default wait the cold flow (107 s
including login) finished in one call, turn counts matched, and jev came out marginally
cheaper on smaller results alone.

## Conclusions

1. **Turns decide cost.** Each turn re-reads ~26k base context plus history; one turn
   (~45k) outweighs all payload savings. Bytes matter only once turn counts are equal.
2. jev wins exploratory work (smaller screens, fewer reads needed).
3. For scripted suites the runners are equal (jev −1.2%) once results are one line and the
   cold start fits in one call.
4. Biggest remaining levers: whole-suite runs in one call; and local decisions for
   exploratory goals (`run_goal`) — see [qwen-probe.md](qwen-probe.md).

Single run per configuration: directionally clear, not statistically settled.
