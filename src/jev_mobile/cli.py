"""Command line entry point; no credentials are needed for inspect or offline checks."""

import argparse
import asyncio
import json
import os
import sys
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
    setup.add_argument("--backend", choices=("jev", "laya"), default="laya")
    setup.add_argument("--laya-url", help="Local Laya origin; default http://127.0.0.1:8081")
    local = sub.add_parser("laya-serve", help="Serve Laya locally on Apple Silicon (extra: laya)")
    local.add_argument("--port", type=int, default=8081)
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


async def execute(args):
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
