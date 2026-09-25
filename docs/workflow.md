# Simulator testing in your development workflow

The goal: every finished UI feature gets checked on the simulator by default, without an
agent spending tens of thousands of tokens driving it step by step.

Runs 6–8 in the README showed that **agent turns drive cost**. Each turn re-reads the
agent's whole context (about 26k tokens of base context before any history), so a check
that takes 15 turns costs roughly 15 times that. This workflow keeps the model out of
anything it doesn't need to do:

| Stage | Who does it | Model tokens |
|---|---|---|
| Open the screen under test | a debug route in the app | none |
| Write the flow for a new feature | `sim-tester` agent, once | a few small turns |
| Check the feature | `sim-tester`, one `run_flow(files=…)` call | one short turn |
| Regression suite | `jev-mobile test` from a git hook or CI | **none on pass** |
| Fix a failure | your main agent, from one line of output | only when something broke |

## 1. Add a debug route to the app, once

Don't navigate to the screen under test, and don't temporarily make it the root view. Let
the app open any screen, with fixture data, from a launch argument. Maestro passes
`launchApp` arguments to iOS as `-key value` launch arguments, which `UserDefaults` exposes:

```yaml
- launchApp:
    arguments:
      jevRoute: "overtime/OT-100"
      jevFixture: "manager"
```

A starting point for a SwiftUI app. Keep it in `#if DEBUG` so release builds never see it:

```swift
#if DEBUG
enum DebugLaunch {
    /// Route to open at launch, e.g. "overtime/OT-100".
    static var route: String? { UserDefaults.standard.string(forKey: "jevRoute") }
    /// Named fixture: signed-in user and mock data, e.g. "manager".
    static var fixture: String? { UserDefaults.standard.string(forKey: "jevFixture") }
}
#endif
```

At startup, when `DebugLaunch.route` is set:

1. Apply the fixture first (mock services, a signed-in test user) so the screen doesn't
   wait on the network.
2. Parse the route into your existing typed route (`Route.overtime(id: "OT-100")`).
3. Set the navigation path directly, e.g. `path = [.overtimeList, .overtime(id:)]`, or ask
   your coordinator to open it. Keep the normal back stack so "Back" still works.
4. Skip onboarding, what's-new sheets, and permission prompts for debug launches.

Adding a new screen then means adding one case to the route parser. Give interactive views
`accessibilityIdentifier`s: flows that target ids survive text and localization changes.

## 2. Keep flows next to the code

```
maestro/
  features/overtime-compensation.yaml   # one per feature; tags: [smoke, overtime]
  smoke/…                               # existing suites
```

A feature flow opens its screen through the route and checks what the feature promises:

```yaml
appId: com.example.app
tags: [smoke, overtime]
---
- launchApp:
    arguments:
      jevRoute: "overtime/OT-100"
      jevFixture: "manager"
- extendedWaitUntil:
    visible: "Compensation Method"
    timeout: 15000
- assertVisible: "Overtime Pay"
```

Use `extendedWaitUntil` instead of fixed sleeps. Most of the per-flow time in runs 7–8
(40–50 s) was navigation and waiting; a routed flow skips most of that.

## 3. Add the agent and the hook to the app repo

From the jev-mobile checkout:

```sh
make workflow PROJECT=~/code/your-app APP=com.example.app FLOWS=maestro TAGS=smoke
```

This writes two files into the app repo:

- `.claude/agents/sim-tester.md`: a Claude Code subagent on Haiku that can only use the
  jev-mobile tools and the file tools. It reuses or writes `maestro/features/<feature>.yaml`,
  runs it once, and answers `PASS <file>` or `FAIL <file>: <reason>`.
- `.git/hooks/pre-push`: runs `jev-mobile test maestro --include-tags smoke` before every
  push, with no model. It never overwrites a pre-push hook that isn't jev-mobile's. Skip it
  once with `JEV_SKIP=1 git push`.

Then tell your main agent when to use it, in the app's `CLAUDE.md`:

```markdown
## Simulator checks
After finishing a UI feature, add its debug route if it needs one, then delegate the
check to the `sim-tester` agent with the feature name, the route, and the text that proves
it works. Don't drive the simulator from the main conversation.
```

## 4. Run suites without a model

```sh
jev-mobile test maestro --include-tags smoke          # a directory, filtered by tags
jev-mobile test maestro/features/overtime.yaml        # specific files
```

It prints one line (`passed 12 flows in 11.1 min`, or `FAILED …: 2/12 flows failed:
a.yaml: <reason>; …`) and exits 0 on pass, 1 on failure, and 3 if another Maestro driver
kept the simulator busy. Use it in hooks, `make`, or CI.

Only one Maestro-driven iOS simulator can run per Mac, because every Maestro driver uses
port 22087. An agent session's jev server releases its driver after 120 idle seconds
(`--idle-release`, or `JEV_IDLE_RELEASE`), and `jev-mobile test` waits up to 180 seconds
(`--wait-free`) for that before it gives up.

## What this doesn't measure yet

The per-stage costs above follow from the measured runs, but this end-to-end workflow
hasn't been measured. To compare it with an agent driving every check, count turns and
total tokens for the same features both ways, as in the README runs.
