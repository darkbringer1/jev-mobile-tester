"""Command line entry point; no credentials are needed for inspect or offline checks."""

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

import httpx
import yaml

from .agent import run_agent
from .maestro import connect
from .policy import create_model, request_body
from .screen import parse_screen

DEFAULT_RUNS = "~/Library/Application Support/jev-mobile/runs"


def parser():
    root = argparse.ArgumentParser(description="Mobile automation with Jev + Maestro")
    root.add_argument("--maestro", default="maestro", help="Path to the Maestro executable")
    sub = root.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Serve compact goal tools over local MCP stdio")
    serve.add_argument("--device", default=os.getenv("JEV_DEVICE_ID"))
    serve.add_argument("--app-id", default=os.getenv("JEV_APP_ID"))
    serve.add_argument(
        "--output",
        type=Path,
        default=Path(os.getenv("JEV_RUNS", DEFAULT_RUNS)).expanduser(),
        help="Run reports directory, outside the agent's workspace by default",
    )
    serve.add_argument(
        "--timeout", type=float, help="Optional per-call deadline in seconds; default none"
    )
    serve.add_argument("--min-confidence", type=float, default=0.5)
    serve.add_argument(
        "--idle-release",
        type=float,
        default=float(os.getenv("JEV_IDLE_RELEASE", "120")),
        help="Free the simulator driver after this many idle seconds (0: never)",
    )
    setup = sub.add_parser("setup", help="Register the MCP server with local AI agent clients")
    setup.add_argument("--app-id", help="Default bundle ID; agents can still pass app_id")
    setup.add_argument("--device", help="Default device; else the single connected device")
    setup.add_argument(
        "--client",
        action="append",
        choices=("claude", "codex", "cursor", "json"),
        help="Repeatable; default: every detected client",
    )
    setup.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Claude user scope and ~/.cursor instead of this project (Codex is always global)",
    )
    setup.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Register in every discovered Claude Code config without asking",
    )
    setup.add_argument(
        "--agent",
        action="store_true",
        help="Write .claude/agents/sim-tester.md: a small verification agent (then skip registration unless --client)",
    )
    setup.add_argument(
        "--git-hook",
        action="store_true",
        help="Write a pre-push hook that runs `jev-mobile test` on --flows with --hook-tags",
    )
    setup.add_argument("--flows", default="maestro", help="Flow directory in the app repo")
    setup.add_argument("--hook-tags", default="smoke", help="Comma-separated tags for the hook")
    setup.add_argument("--backend", choices=("jev", "laya"), default="laya")
    setup.add_argument("--laya-url", help="Local Laya origin; default http://127.0.0.1:8081")
    local = sub.add_parser("laya-serve", help="Serve Laya locally on Apple Silicon (extra: laya)")
    local.add_argument("--port", type=int, default=8081)
    test = sub.add_parser(
        "test", help="Run Maestro flows without a model; prints one line, exits 1 on failure"
    )
    test.add_argument("paths", nargs="+", type=Path, help="Flow files, or one flow directory")
    test.add_argument("--include-tags", default="", help="Comma-separated; directory runs only")
    test.add_argument("--exclude-tags", default="", help="Comma-separated; directory runs only")
    test.add_argument("--device", default=os.getenv("JEV_DEVICE_ID"))
    test.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    test.add_argument(
        "--wait-free",
        type=float,
        default=180,
        help="Seconds to wait for another Maestro driver (e.g. an idle agent session) to exit",
    )
    sub.add_parser("devices", help="List devices through Maestro MCP")
    sub.add_parser("doctor", help="Check the installed Maestro MCP tool contract")
    inspect = sub.add_parser("inspect", help="Show the normalized element table")
    inspect.add_argument("--device", required=True)
    plan = sub.add_parser("request", help="Build a Jev request from an offline hierarchy fixture")
    plan.add_argument("--screen", type=Path, required=True)
    plan.add_argument("--goal", required=True)
    run = sub.add_parser("run", help="Execute a natural-language goal on a running simulator")
    run.add_argument("--device", required=True)
    run.add_argument("--app-id", required=True)
    run.add_argument("--goal", required=True)
    run.add_argument("--values", type=Path, help="JSON object of named field values")
    run.add_argument(
        "--expect-text", action="append", default=[], help="Exact visible text to verify"
    )
    run.add_argument("--max-steps", type=int, default=30)
    run.add_argument("--min-confidence", type=float, default=0.5)
    run.add_argument("--output", type=Path, default=Path("runs/latest"))
    for command in (run, serve):
        command.add_argument("--backend", choices=("jev", "laya"), default=None)
        command.add_argument("--laya-url", help="Local Laya origin; default http://127.0.0.1:8081")
    return root


async def run_tests(args):
    """Model-free suite run for hooks and CI: zero agent tokens on pass, one line on failure."""
    from .maestro import failure_reason, flow_total, foreign_drivers

    paths = [path.expanduser().resolve() for path in args.paths]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise ValueError(f"Not found: {', '.join(missing)}")
    folder = paths[0] if len(paths) == 1 and paths[0].is_dir() else None
    if not folder and any(path.is_dir() for path in paths):
        raise ValueError("Pass one directory, or flow files only")
    include = [t for t in args.include_tags.split(",") if t]
    exclude = [t for t in args.exclude_tags.split(",") if t]
    if (include or exclude) and not folder:
        raise ValueError("--include-tags/--exclude-tags need a directory")
    env = dict(item.split("=", 1) for item in args.env if "=" in item)
    started = time.monotonic()
    async with connect(args.maestro) as maestro:
        device = args.device
        if not device:
            data = json.loads(await maestro.devices())
            connected = [d["device_id"] for d in data.get("devices", []) if d.get("connected")]
            if len(connected) != 1:
                raise ValueError(f"{len(connected)} connected devices; pass --device")
            device = connected[0]
        deadline = time.monotonic() + args.wait_free
        while pids := await asyncio.to_thread(foreign_drivers, device):
            if time.monotonic() >= deadline:
                print(
                    f"jev-mobile test: busy: another Maestro driver (pid "
                    f"{', '.join(map(str, pids))}) holds port 22087",
                    file=sys.stderr,
                )
                return 3
            await asyncio.sleep(2)
        try:
            text = await maestro.run_files(
                device,
                [str(p) for p in paths] if not folder else None,
                env,
                str(folder) if folder else None,
                include,
                exclude,
            )
        except RuntimeError as error:
            reason = str(error)
            with contextlib.suppress(ValueError, AttributeError, IndexError):
                reason = failure_reason(json.loads(reason.split(": ", 1)[1])) or reason
            print(f"jev-mobile test: FAILED ({elapsed(started)}): {reason}")
            return 1
    total = flow_total(text)
    count = f"{total} flows" if total is not None else "flows"
    print(f"jev-mobile test: passed {count} in {elapsed(started)}")
    return 0


def elapsed(started):
    seconds = time.monotonic() - started
    return f"{seconds / 60:.1f} min" if seconds >= 90 else f"{seconds:.0f} s"


async def execute(args):
    if args.command == "test":
        return await run_tests(args)
    if args.command == "request":
        screen = parse_screen(args.screen.read_text())
        print(json.dumps(request_body(screen, args.goal, [], {}), indent=2))
        return 0
    values = {}
    if args.command == "run":
        if (args.backend or os.getenv("JEV_BACKEND", "jev")) == "jev" and not os.getenv(
            "TYPESAFE_API_KEY"
        ):
            raise ValueError("Set TYPESAFE_API_KEY before running a live goal")
        if args.max_steps < 1 or not 0 <= args.min_confidence <= 1:
            raise ValueError("max-steps must be positive and min-confidence must be in [0, 1]")
        if args.values:
            values = json.loads(args.values.read_text())
            if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
                raise ValueError("--values must contain a JSON object of strings")
        if "${" in args.app_id:
            raise ValueError("App ID cannot contain Maestro expression syntax")
        args.output.mkdir(parents=True, exist_ok=True)
    async with connect(args.maestro, getattr(args, "app_id", None)) as maestro:
        if args.command == "doctor":
            required = {"list_devices", "inspect_screen"}
            missing = required - maestro.tools.keys()
            if missing or not ({"run", "run_flow"} & maestro.tools.keys()):
                raise RuntimeError(f"Incompatible Maestro MCP tools: {sorted(maestro.tools)}")
            print(json.dumps({"status": "ready", "tools": sorted(maestro.tools)}, indent=2))
            return 0
        if args.command == "devices":
            print(await maestro.devices())
            return 0
        if args.command == "inspect":
            print(json.dumps((await maestro.observe(args.device)).state(), indent=2))
            return 0
        launch = {"launchApp": args.app_id}
        await maestro.run(args.device, [launch])
        with (args.output / "steps.jsonl").open("w") as trace:

            def emit(entry):
                line = json.dumps(entry)
                trace.write(line + "\n")
                trace.flush()
                print(line, flush=True)

            async with httpx.AsyncClient(timeout=30) as client:
                model = create_model(client, args.min_confidence, args.backend, args.laya_url)
                if model is None:
                    raise ValueError("Set TYPESAFE_API_KEY or use --backend laya")
                result = await run_agent(
                    maestro,
                    model,
                    args.device,
                    args.goal,
                    values,
                    args.expect_text,
                    max_steps=args.max_steps,
                    emit=emit,
                )
        (args.output / "result.json").write_text(json.dumps(asdict(result), indent=2))
        header = yaml.safe_dump({"appId": args.app_id}, sort_keys=False)
        commands = yaml.safe_dump([launch, *result.commands], sort_keys=False, allow_unicode=True)
        (args.output / "flow.yaml").write_text(header + "---\n" + commands)
        print(f"{result.status}: {result.elapsed_ms:.0f} ms; output: {args.output}")
        return 0 if result.status == "verified" else 2


def main():
    args = parser().parse_args()
    try:
        if args.command == "laya-serve":
            from .laya_server import serve

            serve(port=args.port)
            return
        if args.command == "setup":
            from .clients import setup

            raise SystemExit(setup(args))
        if args.command == "serve":
            from .server import create_server

            if args.timeout is not None and args.timeout <= 0:
                raise ValueError("timeout must be positive")
            if not 0 <= args.min_confidence <= 1:
                raise ValueError("min-confidence must be in [0, 1]")
            create_server(
                maestro_command=args.maestro,
                output=args.output,
                device_id=args.device,
                app_id=args.app_id,
                timeout=args.timeout,
                min_confidence=args.min_confidence,
                backend=args.backend,
                laya_url=args.laya_url,
                idle_release=args.idle_release,
            ).run()
            return
        raise SystemExit(asyncio.run(execute(args)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:  # noqa: BLE001 -- present transport exception groups at the CLI boundary
        print(f"jev-mobile: {error_message(error)}", file=sys.stderr)
        raise SystemExit(1) from None


def error_message(error):
    if isinstance(error, BaseExceptionGroup):
        return "; ".join(error_message(child) for child in error.exceptions)
    return str(error)
