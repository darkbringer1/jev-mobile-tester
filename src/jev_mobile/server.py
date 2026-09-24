"""Local MCP facade: one goal in, a compact outcome out."""

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import yaml
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from .agent import run_agent
from .maestro import connect
from .policy import Jev


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def short_error(error, secrets=()):
    if isinstance(error, BaseExceptionGroup):
        message = "; ".join(short_error(child, secrets) for child in error.exceptions)
    else:
        message = str(error)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return " ".join(message.split())[:240]


class MobileService:
    def __init__(self, maestro, model, output: Path, *, device_id=None, app_id=None, timeout=180):
        self.maestro = maestro
        self.model = model
        self.output = output
        self.device_id = device_id
        self.app_id = app_id
        self.timeout = timeout
        self.lock = asyncio.Lock()

    async def devices(self):
        if self.lock.locked():
            return {"status": "busy"}
        try:
            async with self.lock:
                data = json.loads(await self.maestro.devices())
        except Exception as error:  # noqa: BLE001 -- keep transport failures bounded for callers
            return {
                "status": "error",
                "error": short_error(error, [os.getenv("TYPESAFE_API_KEY", "")]),
            }
        return {
            "devices": [
                {"id": d["device_id"], "name": d["name"], "platform": d["platform"]}
                for d in data.get("devices", [])
                if d.get("connected")
            ]
        }

    async def run(
        self, goal, expect_text=None, device_id=None, app_id=None, values=None, max_steps=30
    ):
        device_id = device_id or self.device_id
        app_id = app_id or self.app_id
        values = values or {}
        expect_text = expect_text or []
        if not goal.strip() or len(goal) > 4000:
            return {"status": "invalid", "error": "goal must contain 1–4000 characters"}
        if not device_id or not app_id:
            return {"status": "invalid", "error": "Set device_id and app_id, or server defaults"}
        if not 1 <= max_steps <= 100:
            return {"status": "invalid", "error": "max_steps must be 1–100"}
        if len(expect_text) > 20 or len(values) > 100:
            return {"status": "invalid", "error": "At most 20 assertions and 100 field values"}
        literals = [app_id, *expect_text, *values.keys(), *values.values()]
        if any(not isinstance(v, str) or not v or len(v) > 4000 or "${" in v for v in literals):
            return {"status": "invalid", "error": "Use nonempty literal strings without ${...}"}
        if self.model is None:
            return {
                "status": "unavailable",
                "error": "Set TYPESAFE_API_KEY in the server environment",
            }
        if self.lock.locked():
            return {"status": "busy"}
        async with self.lock:
            run_id = uuid.uuid4().hex
            directory = self.output / run_id
            directory.mkdir(parents=True)
            started = time.perf_counter()
            steps, commands = [], []
            report = {"run_id": run_id, "status": "running", "steps": steps, "commands": commands}
            bound = self.maestro.for_app(app_id)
            secrets = [os.getenv("TYPESAFE_API_KEY", ""), *values.values()]
            with (directory / "steps.jsonl").open("w") as trace:

                def emit(step):
                    steps.append(step)
                    trace.write(compact(step) + "\n")
                    trace.flush()

                try:
                    async with asyncio.timeout(self.timeout):
                        # Continue an existing app session; never clear state or stop it implicitly.
                        launch = {"launchApp": {"appId": app_id, "stopApp": False}}
                        await bound.run(device_id, [launch])
                        commands.append(launch)
                        result = await run_agent(
                            bound,
                            self.model,
                            device_id,
                            goal,
                            values,
                            expect_text,
                            max_steps=max_steps,
                            emit=emit,
                            on_commands=commands.extend,
                        )
                        report["status"] = result.status
                except TimeoutError:
                    report.update(status="timeout", error=f"Run exceeded {self.timeout:g}s")
                except asyncio.CancelledError:
                    report["status"] = "cancelled"
                    raise
                except Exception as error:  # noqa: BLE001 -- persist failed runs at service boundary
                    report.update(status="error", error=short_error(error, secrets))
                finally:
                    report["ms"] = round((time.perf_counter() - started) * 1000)
                    (directory / "result.json").write_text(json.dumps(report, indent=2))
                    flow = yaml.safe_dump({"appId": app_id}) + "---\n"
                    flow += yaml.safe_dump(commands, sort_keys=False, allow_unicode=True)
                    (directory / "flow.yaml").write_text(flow)
            return self.summary(report)

    @staticmethod
    def summary(report):
        result = {
            "status": report["status"],
            "run_id": report["run_id"],
            "steps": len(report["steps"]),
            "ms": report["ms"],
        }
        if report.get("error"):
            result["error"] = report["error"]
        usages = [step["usage"] for step in report["steps"] if isinstance(step.get("usage"), dict)]
        if usages:
            result["jev_tokens"] = {
                "input": sum(u.get("input_tokens", 0) for u in usages),
                "output": sum(u.get("output_tokens", 0) for u in usages),
            }
        return result

    def report(self, run_id, last_steps=3):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id) or not 0 <= last_steps <= 10:
            return {"status": "invalid", "error": "Use returned run_id; last_steps must be 0–10"}
        path = self.output / run_id / "result.json"
        if not path.is_file():
            return {"status": "not_found"}
        report = json.loads(path.read_text())
        result = self.summary(report)
        result["recent"] = [
            {k: step[k] for k in ("step", "operation", "target", "status") if k in step}
            for step in (report["steps"][-last_steps:] if last_steps else [])
        ]
        result["flow"] = str(path.with_name("flow.yaml").resolve())
        return result


def create_server(
    *,
    maestro_command="maestro",
    output=Path("runs/mcp"),
    device_id=None,
    app_id=None,
    timeout=180,
    min_confidence=0.5,
    service=None,
):
    @asynccontextmanager
    async def lifespan(server):
        if service is not None:
            yield service
            return
        async with connect(maestro_command) as maestro, httpx.AsyncClient(timeout=30) as client:
            key = os.getenv("TYPESAFE_API_KEY")
            model = Jev(client, key, min_confidence) if key else None
            yield MobileService(
                maestro, model, output, device_id=device_id, app_id=app_id, timeout=timeout
            )

    server = FastMCP(
        "jev-mobile",
        lifespan=lifespan,
        instructions=(
            "Delegate mobile goals with run_goal. Only verified means assertions passed. "
            "Use run_report only for debugging. No screen dumps needed."
        ),
    )

    @server.tool(
        structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    )
    async def devices(ctx: Context) -> str:
        """List connected devices. Skip when device_id is configured."""
        return compact(await ctx.request_context.lifespan_context.devices())

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(destructiveHint=True, idempotentHint=False),
    )
    async def run_goal(
        goal: str,
        ctx: Context,
        expect_text: list[str] | None = None,
        device_id: str | None = None,
        app_id: str | None = None,
        values: dict[str, str] | None = None,
        max_steps: int = 30,
    ) -> str:
        """Run a mobile goal via Jev. expect_text: exact final texts; values: field ID to text.
        Omit device_id/app_id when configured. Returns compact outcome; only verified is success.
        """
        return compact(
            await ctx.request_context.lifespan_context.run(
                goal,
                expect_text,
                device_id,
                app_id,
                values,
                max_steps,
            )
        )

    @server.tool(
        structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    )
    async def run_report(run_id: str, ctx: Context, last_steps: int = 3) -> str:
        """Read a run summary, up to 10 recent steps, and exported flow path."""
        return compact(ctx.request_context.lifespan_context.report(run_id, last_steps))

    return server
