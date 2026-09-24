<p align="center">
  <img src="assets/readme-hero.svg" alt="Jev Mobile — mobile automation for AI agents. One goal in. A compact result out." width="1200" />
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-63e6be" alt="MIT license" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-72b8ff" alt="Python 3.11 and newer" />
  <a href="docs/mcp.md"><img src="https://img.shields.io/badge/MCP-local_stdio-9bb5ff" alt="Local MCP server over stdio" /></a>
  <img src="https://img.shields.io/badge/status-early_preview-f4c777" alt="Early preview" />
</p>

# Jev Mobile

**Let your AI agent use your iOS simulator.**

I'm building Jev Mobile to keep mobile automation simple for AI agents. Give it a goal,
let Jev pick the next action, and let Maestro do the tapping. Your agent gets a short
result when it's done, while the screen data and action loop stay inside the server.

The idea came from [Browser Use's jev-ultrafast](https://github.com/browser-use/jev-ultrafast).
I wanted to bring that approach to mobile apps with Maestro, and make it available as
a local MCP server.

By [Doğukaan Kılıçarslan](https://github.com/darkbringer1) · [MIT](LICENSE)

## The idea

Your agent sends a task like:

> Open Settings, go to General, then About.

Jev Mobile reads the screen, gives Jev a list of possible actions, runs the selected
action through Maestro, and repeats. You can supply final text checks so Maestro
verifies the result. The run also leaves behind a trace and an exported Maestro YAML flow.

The MCP interface has just three tools:

- **`devices`** — find a connected device.
- **`run_goal`** — hand over a task and get a compact outcome.
- **`run_report`** — look at the recent steps if you need to debug something.

The aim is to use less of your agent's context. I haven't measured total token savings
yet. The server runs locally; Jev itself is called through TypeSafe's API.

## Try it

You'll need Python 3.11+, [uv](https://docs.astral.sh/uv/), Xcode, a booted iOS simulator,
and [Maestro](https://github.com/mobile-dev-inc/maestro) with MCP support. The tested
Maestro build is 2.10.0 and supports `maestro mcp --no-viewer`.

From your checkout:

```sh
uv sync --locked
uv run jev-mobile doctor
uv run jev-mobile devices
cp .env.example .env
```

Add your [TypeSafe API key](https://docs.typesafe.ai/) to `.env`:

```dotenv
TYPESAFE_API_KEY=your-key-here
```

For a first task, open Settings on an English-language simulator and try:

```sh
uv run --env-file .env jev-mobile run \
  --device YOUR_SIMULATOR_UDID \
  --app-id com.apple.Preferences \
  --goal 'Open General, then About' \
  --expect-text 'About' \
  --expect-text 'iOS Version'
```

This is a suggested task to try; the live Jev demo is still on the todo list.
Device discovery and the offline tests work without an API key.

## Use it with your agent

Add [this MCP config](examples/mcp.json) to a client that supports stdio MCP. Replace
the paths, simulator ID, and app ID with your own. Your client will start the server.

You can also launch it directly:

```sh
uv run --env-file .env jev-mobile serve \
  --device YOUR_SIMULATOR_UDID \
  --app-id com.apple.Preferences
```

With the device and app configured, a `run_goal` call looks like this:

```json
{
  "goal": "Open General, then About",
  "expect_text": ["About", "iOS Version"]
}
```

Only `verified` means the checks you supplied passed. If you leave out `expect_text`,
the run can finish as `done_unverified`.

The server allows up to 180 seconds per goal by default, so set your client's tool
timeout a little higher. More options are in the [MCP guide](docs/mcp.md).

## A couple of useful bits

**Text fields:** pass `values` in an MCP call, or `--values examples/values.json` in
the CLI. It's a map of field IDs to the text you want entered:

```json
{"search_field": "Coffee"}
```

You'll need to know the field's accessibility/resource ID. Jev chooses which field
to fill; free-form text generation isn't implemented yet.

**Saved runs:** CLI output goes to `runs/latest/`. MCP output goes to
`runs/mcp/<run-id>/`. Each run saves its steps, a result summary, and `flow.yaml`.
You can replay a CLI export with:

```sh
maestro test runs/latest/flow.yaml
```

You may need to adjust the starting state or add waits before reusing an exported flow.

## Still early

The local MCP connection works, the offline tests pass, and the scripted iOS check
navigates Settings → General → About. Live Jev runs and Android testing are next.
There are no speed or cost benchmarks yet.

Right now this works with UI elements Maestro can identify. Unlabelled controls,
custom-drawn screens, complex gestures, and ambiguous targets still need work.
For the details, see the [validation notes](docs/validation.md).

Live runs send screen element data, your goal, and supplied field values to TypeSafe.
Traces and exported flows can contain entered text. `.env` and `runs/` are ignored by
Git; use demo data when sharing a recording.

## Hack on it

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv build
```

Check the local MCP connection without paid calls or device actions:

```sh
uv run python examples/check_mcp.py
```

Try the scripted Settings check without a Jev key:

```sh
uv run python examples/ios_settings_smoke.py --device YOUR_SIMULATOR_UDID
```

I'd welcome help with a live demo, better text input, Android support, and measuring
how much agent context this saves. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Thanks

Built with [Maestro](https://github.com/mobile-dev-inc/maestro) and
[TypeSafe Jev](https://docs.typesafe.ai/). Thanks to the Browser Use team for sharing
[jev-ultrafast](https://github.com/browser-use/jev-ultrafast), which inspired this project.

This is my independent project, not an official project from those teams.
[MIT license](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)
