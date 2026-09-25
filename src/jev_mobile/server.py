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
from mcp.types import ImageContent, ToolAnnotations

from .agent import run_agent
from .maestro import connect
from .policy import create_model


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

    async def resolve_device(self, device_id):
        if device_id := device_id or self.device_id:
            return device_id
        data = json.loads(await self.maestro.devices())
        connected = [d["device_id"] for d in data.get("devices", []) if d.get("connected")]
        if len(connected) != 1:
            raise LookupError(f"{len(connected)} connected devices; pass device_id")
        return connected[0]

    async def direct(self, operation, device_id=None):
        """Run one direct Maestro operation under the lock shared with goal runs."""
        if self.lock.locked():
            return {"status": "busy"}
        try:
            async with self.lock, asyncio.timeout(self.timeout):
                return await operation(await self.resolve_device(device_id))
        except TimeoutError:
            return {"status": "timeout", "error": f"Exceeded {self.timeout:g}s"}
        except Exception as error:  # noqa: BLE001 -- keep transport failures bounded for callers
            return {"status": "error", "error": short_error(error)}

    async def screen(self, device_id=None):
        async def observe(device):
            elements = []
            for element in (await self.maestro.observe(device)).elements:
                item = {"text": element.label, "id": element.resource_id, "value": element.value}
                item = {k: v for k, v in item.items() if v}
                if element.checked:
                    item["checked"] = True
                if element.selected:
                    item["selected"] = True
                elements.append(item)
            return {"device": device, "elements": elements}

        return await self.direct(observe, device_id)

    async def screenshot(self, device_id=None):
        return await self.direct(self.maestro.screenshot, device_id)

    async def run_flow(self, commands=None, files=None, device_id=None, app_id=None, env=None):
        if (commands is None) == (not files):
            return {"status": "invalid", "error": "Pass exactly one of commands or files"}
        if commands is not None:
            try:
                documents = [d for d in yaml.safe_load_all(commands) if d is not None]
            except yaml.YAMLError as error:
                return {"status": "invalid", "error": short_error(error)}
            if len(documents) == 2 and isinstance(documents[0], dict):
                app_id = app_id or documents[0].get("appId")
                documents = documents[1:]
            steps = documents[0] if len(documents) == 1 else None
            if not isinstance(steps, list) or not steps:
                return {"status": "invalid", "error": "commands must be a YAML list of steps"}
            app_id = app_id or self.app_id
            if not isinstance(app_id, str) or not app_id:
                return {"status": "invalid", "error": "Set app_id, or a server default"}

            async def execute(device):
                await self.maestro.for_app(app_id).run(device, steps)
                return {"status": "passed", "steps": len(steps)}
        else:

            async def execute(device):
                await self.maestro.run_files(device, [str(Path(f).resolve()) for f in files], env)
                return {"status": "passed", "files": len(files)}

        return await self.direct(execute, device_id)

    async def run(
        self, goal, expect_text=None, device_id=None, app_id=None, values=None, max_steps=30
    ):
        device_id = device_id or self.device_id
        app_id = app_id or self.app_id
        values = values or {}
        expect_text = expect_text or []
        if not goal.strip() or len(goal) > 4000:
            return {"status": "invalid", "error": "goal must contain 1–4000 characters"}
        if not app_id:
            return {"status": "invalid", "error": "Set app_id, or a server default"}
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
                "error": "Set TYPESAFE_API_KEY or select JEV_BACKEND=laya",
            }
        if self.lock.locked():
            return {"status": "busy"}
        async with self.lock:
            try:
                device_id = await self.resolve_device(device_id)
            except Exception as error:  # noqa: BLE001 -- report before creating a run
                return {"status": "invalid", "error": short_error(error)}
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
    backend=None,
    laya_url=None,
    service=None,
):
    @asynccontextmanager
    async def lifespan(server):
        if service is not None:
            yield service
            return
        async with connect(maestro_command) as maestro, httpx.AsyncClient(timeout=30) as client:
            model = create_model(client, min_confidence, backend, laya_url)
            yield MobileService(
                maestro, model, output, device_id=device_id, app_id=app_id, timeout=timeout
            )

    server = FastMCP(
        "jev-mobile",
        lifespan=lifespan,
        instructions=(
            "Mobile simulator control via Maestro. Prefer screen + run_flow for precise "
            "steps and existing Maestro tests; run_goal delegates a bounded goal to a local "
            "model and only verified means assertions passed. device_id defaults to the "
            "configured or single connected device."
        ),
    )

    @server.tool(
        structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    )
    async def devices(ctx: Context) -> str:
        """List connected devices. Skip when device_id is configured."""
        return compact(await ctx.request_context.lifespan_context.devices())

    @server.tool(
        structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    )
    async def screen(ctx: Context, device_id: str | None = None) -> str:
        """Visible elements as {text,id,value}. Target them in run_flow by text or id."""
        return compact(await ctx.request_context.lifespan_context.screen(device_id))

    @server.tool(
        structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    )
    async def screenshot(ctx: Context, device_id: str | None = None) -> list[ImageContent] | str:
        """PNG of the current screen. Costly; prefer screen for text and IDs."""
        result = await ctx.request_context.lifespan_context.screenshot(device_id)
        return result if isinstance(result, list) else compact(result)

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(destructiveHint=True, idempotentHint=False),
    )
    async def run_flow(
        ctx: Context,
        commands: str | None = None,
        files: list[str] | None = None,
        device_id: str | None = None,
        app_id: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        """Run Maestro steps. commands: YAML list, e.g. '- tapOn: "General"\n- inputText: hi\n
        - assertVisible: About'. Also: launchApp, tapOn {id}, eraseText, back, scroll,
        swipe, pressKey. files: existing flow paths (env optional). Omit app_id if configured.
        """
        return compact(
            await ctx.request_context.lifespan_context.run_flow(
                commands, files, device_id, app_id, env
            )
        )

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
