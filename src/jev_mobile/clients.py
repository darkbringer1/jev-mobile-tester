"""Register the local MCP server with AI agent clients in one command."""

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import httpx

NAME = "jev-mobile"
CLIENTS = ("claude", "codex", "cursor", "json")
TOOL_TIMEOUT = 600  # Codex needs a number; allow flows that wait on slow app network calls.


def server_spec(args):
    command = shutil.which(NAME) or str(Path(sys.argv[0]).absolute())
    maestro = shutil.which(args.maestro) or args.maestro
    # GUI clients may lack the shell PATH; keep only what Maestro, Java, and xcrun need.
    tools = [shutil.which(tool) for tool in (maestro, "java")]
    system = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
    dirs = [str(Path(tool).parent) for tool in tools if tool] + list(system)
    env = {"PATH": ":".join(dict.fromkeys(dirs)), "JEV_BACKEND": args.backend}
    for key, value in (
        ("JEV_APP_ID", args.app_id),
        ("JEV_DEVICE_ID", args.device),
        ("LAYA_URL", args.laya_url),
        ("JAVA_HOME", os.getenv("JAVA_HOME")),
    ):
        if value:
            env[key] = value
    return {"command": command, "args": ["--maestro", maestro, "serve"], "env": env}


def detected():
    found = [c for c in ("claude", "codex") if shutil.which(c)]
    if shutil.which("cursor") or (Path.home() / ".cursor").is_dir():
        found.append("cursor")
    return found or ["json"]


def claude_configs():
    """Claude Code config dirs: None is the default ~/.claude.json; others via CLAUDE_CONFIG_DIR."""
    home = Path.home()
    found = [None] if (home / ".claude.json").exists() or (home / ".claude").is_dir() else []
    # Profiles such as `CLAUDE_CONFIG_DIR=~/.claude-work claude` keep separate MCP servers.
    for path in sorted(home.glob(".claude-*")):
        if path.is_dir() and any((path / f).exists() for f in (".claude.json", "settings.json")):
            found.append(path)
    if os.getenv("CLAUDE_CONFIG_DIR"):
        found.append(Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser())
    return list(dict.fromkeys(found)) or [None]


def config_label(config):
    return "~/.claude.json" if config is None else str(config).replace(str(Path.home()), "~", 1)


def confirmed(configs, assume_yes):
    if assume_yes or len(configs) < 2 or not sys.stdin.isatty():
        return configs
    chosen = []
    for config in configs:
        answer = input(f"Register {NAME} in Claude Code config {config_label(config)}? [Y/n] ")
        if answer.strip().lower() in ("", "y", "yes"):
            chosen.append(config)
    return chosen


def register_claude(spec, global_scope, config=None):
    scope = "user" if global_scope else "local"
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    if config is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config)
    subprocess.run(
        ["claude", "mcp", "remove", NAME, "-s", scope],
        capture_output=True,
        check=False,
        env=env,
    )
    options = [part for key, value in spec["env"].items() for part in ("-e", f"{key}={value}")]
    subprocess.run(
        ["claude", "mcp", "add", NAME, "-s", scope, *options, "--", spec["command"], *spec["args"]],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return f"Claude Code {config_label(config)} ({scope} scope)"


def codex_block(spec):
    lines = [
        f"[mcp_servers.{NAME}]",
        f"command = {json.dumps(spec['command'])}",
        f"args = {json.dumps(spec['args'])}",
        f"tool_timeout_sec = {TOOL_TIMEOUT}",
        "startup_timeout_sec = 60",
        "",
        f"[mcp_servers.{NAME}.env]",
        *(f"{key} = {json.dumps(value)}" for key, value in spec["env"].items()),
    ]
    return "\n".join(lines) + "\n"


def without_codex_server(text):
    header = re.compile(r"^\s*\[+\s*([^\]]+?)\s*\]+\s*(#.*)?$")
    kept, skipping = [], False
    for line in text.splitlines(keepends=True):
        match = header.match(line)
        if match:
            table = match.group(1).replace('"', "")
            skipping = table == f"mcp_servers.{NAME}" or table.startswith(f"mcp_servers.{NAME}.")
        if not skipping:
            kept.append(line)
    return "".join(kept).rstrip() + "\n"


def register_codex(spec, _global_scope):
    path = Path(os.getenv("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    original = path.read_text() if path.exists() else ""
    base = without_codex_server(original) if original.strip() else ""
    updated = (base + "\n" if base else "") + codex_block(spec)
    tomllib.loads(updated)  # Never write a config Codex cannot parse.
    path.parent.mkdir(parents=True, exist_ok=True)
    if original:
        path.with_suffix(".toml.bak").write_text(original)
    path.write_text(updated)
    return f"Codex ({path})"


def register_cursor(spec, global_scope):
    path = (Path.home() if global_scope else Path.cwd()) / ".cursor" / "mcp.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    config.setdefault("mcpServers", {})[NAME] = spec
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    return f"Cursor ({path})"


def warnings(args):
    problems = []
    if not shutil.which(args.maestro):
        problems.append(f"Maestro executable not found: {args.maestro}")
    if args.backend == "laya":
        url = (args.laya_url or os.getenv("LAYA_URL") or "http://127.0.0.1:8081").rstrip("/")
        try:
            status = httpx.get(url + "/health", timeout=3).json().get("status")
        except (httpx.HTTPError, ValueError):
            status = None
        if status != "ready":
            problems.append(f"Laya is not ready at {url}; run_goal needs it (see docs/laya.md)")
    elif not os.getenv("TYPESAFE_API_KEY"):
        problems.append("TYPESAFE_API_KEY is unset; run_goal will return unavailable")
    return problems


def setup(args):
    spec = server_spec(args)
    registered = []
    for client in args.client or detected():
        if client == "json":
            print(json.dumps({"mcpServers": {NAME: spec}}, indent=2))
            continue
        if client == "claude":
            configs = confirmed(claude_configs(), args.yes)
            jobs = [
                (client, lambda c=c: register_claude(spec, args.global_scope, c)) for c in configs
            ]
        else:
            register = {"codex": register_codex, "cursor": register_cursor}[client]
            jobs = [(client, lambda r=register: r(spec, args.global_scope))]
        for name, job in jobs:
            try:
                registered.append(job())
            except (OSError, ValueError, subprocess.CalledProcessError) as error:
                detail = getattr(error, "stderr", None) or error
                print(f"{name}: registration failed: {str(detail).strip()}", file=sys.stderr)
    for line in registered:
        print(f"registered {NAME} with {line}")
    for problem in warnings(args):
        print(f"warning: {problem}", file=sys.stderr)
    if registered:
        print("Restart open agent sessions to load the tools. Remove the separate Maestro MCP")
        print("server from those clients to avoid two controllers on one simulator.")
    return 0 if registered or args.client == ["json"] else 1
