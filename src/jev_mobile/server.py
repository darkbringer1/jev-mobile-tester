"""Local MCP facade: one goal in, a compact outcome out."""

import asyncio
import contextlib
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
from .maestro import ManagedMaestro
from .policy import create_model


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def short_error(error, secrets=(), limit=240):
    if isinstance(error, BaseExceptionGroup):
        message = "; ".join(short_error(child, secrets, limit) for child in error.exceptions)
    else:
        message = str(error)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return " ".join(message.split())[:limit]


def clamp_wait(seconds):
    return min(max(float(seconds), 0), MAX_WAIT)


def visible(screen):
    """One line per element: `text #id = value [checked] [selected]`, empty parts omitted.

    Flat strings cost roughly a third less context than {"text":...} objects, and every
    screen read and run_flow result carries them.
    """
    lines = []
    for element in screen.elements:
        parts = [element.label]
        if element.resource_id:
            parts.append("#" + element.resource_id)
        if element.value:
            parts.append("= " + element.value)
        if element.checked:
            parts.append("[checked]")
        if element.selected:
            parts.append("[selected]")
        lines.append(" ".join(part for part in parts if part))
    return lines


WAIT = 45  # Seconds a tool call waits before returning a running run_id; under client limits.
MAX_WAIT = 110


class DeviceBusy(Exception):
    pass


class MobileService:
    def __init__(self, maestro, model, output: Path, *, device_id=None, app_id=None, timeout=None):
        self.maestro = maestro
        self.model = model
        self.output = output
        self.device_id = device_id
        self.app_id = app_id
        self.timeout = timeout
        self.lock = asyncio.Lock()
        self.jobs = {}  # run_id -> (task, report) while running

    def busy(self):
        return {"status": "busy", **({"run_id": next(iter(self.jobs))} if self.jobs else {})}

    async def devices(self):
        if self.lock.locked():
            return self.busy()
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
            device = device_id
        else:
            data = json.loads(await self.maestro.devices())
            connected = [d["device_id"] for d in data.get("devices", []) if d.get("connected")]
            if len(connected) != 1:
                raise LookupError(f"{len(connected)} connected devices; pass device_id")
            device = connected[0]
        # A second driver on one simulator makes both controllers fail unpredictably.
        check = getattr(self.maestro, "foreign_drivers", None)
        if check and (pids := await check(device)):
            raise DeviceBusy(
                f"Another Maestro iOS driver is running (xcodebuild pid "
                f"{', '.join(map(str, pids))}). Maestro drivers share port 22087, so only one "
                "can control any simulator at a time; stop that Maestro session first"
            )
        return device

    async def reset(self):
        """Stop Maestro work that outlived its caller; the next call starts a fresh driver."""
        reset = getattr(self.maestro, "reset", None)
        if reset:
            with contextlib.suppress(Exception):
                await asyncio.shield(reset())

    async def direct(self, operation, device_id=None):
        """Run one short Maestro operation under the lock shared with runs."""
        if self.lock.locked():
            return self.busy()
        try:
            async with self.lock, asyncio.timeout(self.timeout):
                return await operation(await self.resolve_device(device_id))
        except DeviceBusy as error:
            return {"status": "busy", "error": str(error)}
        except TimeoutError:
            await self.reset()
            return {"status": "timeout", "error": f"Exceeded {self.timeout:g}s"}
        except Exception as error:  # noqa: BLE001 -- keep transport failures bounded for callers
            return {"status": "error", "error": short_error(error)}

    async def screen(self, device_id=None, raw=False):
        async def observe(device):
            if raw:
                return {"device": device, "raw": await self.maestro.inspect(device)}
            return {"device": device, "elements": visible(await self.maestro.observe(device))}

        return await self.direct(observe, device_id)

    async def screenshot(self, device_id=None):
        return await self.direct(self.maestro.screenshot, device_id)

    async def start(self, kind, device_id, work, wait=WAIT, secrets=(), progress=None):
        """Run work(report, device) in the background; wait up to `wait` seconds for it.

        Clients abandon slow tool calls, so long runs return a run_id that run_report
        can wait on. The run keeps the device lock until it finishes or is cancelled.
        """
        wait = clamp_wait(wait)
        if self.lock.locked():
            return self.busy()
        await self.lock.acquire()
        try:
            device = await self.resolve_device(device_id)
        except DeviceBusy as error:
            self.lock.release()
            return {"status": "busy", "error": str(error)}
        except Exception as error:  # noqa: BLE001 -- report before creating a run
            self.lock.release()
            return {"status": "invalid" if kind == "goal" else "error", "error": short_error(error)}
        run_id = uuid.uuid4().hex
        directory = self.output / run_id
        directory.mkdir(parents=True)
        report = {
            "run_id": run_id,
            "kind": kind,
            "status": "running",
            "steps": [],
            "commands": [],
            "started": time.perf_counter(),
        }
        task = asyncio.create_task(self.execute(report, directory, work, device, secrets))
        self.jobs[run_id] = (task, report)
        return await self.wait(run_id, wait, progress)

    async def execute(self, report, directory, work, device, secrets):
        try:
            async with asyncio.timeout(self.timeout):
                await work(report, device, directory)
        except TimeoutError:
            report.update(status="timeout", error=f"Run exceeded {self.timeout:g}s")
            await self.reset()
        except asyncio.CancelledError:
            report["status"] = "cancelled"
            await self.reset()
        except Exception as error:  # noqa: BLE001 -- persist failed runs at service boundary
            report.update(status="error", error=short_error(error, secrets))
            detail = short_error(error, secrets, 4000)
            if detail != report["error"]:
                report["error_detail"] = detail
        finally:
            report["ms"] = round((time.perf_counter() - report.pop("started")) * 1000)
            (directory / "result.json").write_text(json.dumps(report, indent=2))
            self.jobs.pop(report["run_id"], None)
            self.lock.release()
        return self.summary(report)

    async def wait(self, run_id, seconds, progress=None):
        task, report = self.jobs.get(run_id) or (None, None)
        if task is None:
            return None
        deadline = time.monotonic() + seconds
        while not task.done() and (remaining := deadline - time.monotonic()) > 0:
            # Shielded: an abandoned tool call must not cancel the run.
            await asyncio.wait({task}, timeout=min(remaining, 10))
            if progress and not task.done():
                await progress(len(report["steps"]))
        if task.done():
            return task.result()
        return {
            "status": "running",
            "run_id": run_id,
            "steps": len(report["steps"]),
            "ms": round((time.perf_counter() - report["started"]) * 1000),
        }

    async def cancel(self, run_id):
        task, _ = self.jobs.get(run_id) or (None, None)
        if task is None:
            return {"status": "not_running", "run_id": run_id}
        task.cancel()
        return await asyncio.shield(task)

    async def close(self):
        for task, _ in list(self.jobs.values()):
            task.cancel()
        await asyncio.gather(*(task for task, _ in self.jobs.values()), return_exceptions=True)

    async def run_flow(
        self,
        commands=None,
        files=None,
        device_id=None,
        app_id=None,
        env=None,
        wait=WAIT,
        progress=None,
    ):
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

            async def work(report, device, directory):
                report.update(app_id=app_id, commands=steps, counts={"steps": len(steps)})
                async with self.observing(report, device):
                    await self.maestro.for_app(app_id).run(device, steps)
                report["status"] = "passed"
        else:
            paths = [str(Path(f).resolve()) for f in files]

            async def work(report, device, directory):
                report.update(files=paths, counts={"files": len(paths)})
                async with self.observing(report, device):
                    await self.maestro.run_files(device, paths, env)
                report["status"] = "passed"

        return await self.start("flow", device_id, work, wait, progress=progress)

    @contextlib.asynccontextmanager
    async def observing(self, report, device):
        """Attach the resulting screen, passed or failed, to save the agent a screen call."""
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(20):
                    report["screen"] = visible(await self.maestro.observe(device))

    async def run(
        self,
        goal,
        expect_text=None,
        device_id=None,
        app_id=None,
        values=None,
        max_steps=30,
        wait=WAIT,
        progress=None,
    ):
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

        async def work(report, device, directory):
            steps, commands = report["steps"], report["commands"]
            bound = self.maestro.for_app(app_id)
            with (directory / "steps.jsonl").open("w") as trace:

                def emit(step):
                    steps.append(step)
                    trace.write(compact(step) + "\n")
                    trace.flush()

                try:
                    # Continue an existing app session; never clear state or stop it implicitly.
                    launch = {"launchApp": {"appId": app_id, "stopApp": False}}
                    await bound.run(device, [launch])
                    commands.append(launch)
                    result = await run_agent(
                        bound,
                        self.model,
                        device,
                        goal,
                        values,
                        expect_text,
                        max_steps=max_steps,
                        emit=emit,
                        on_commands=commands.extend,
                    )
                    report["status"] = result.status
                finally:
                    flow = yaml.safe_dump({"appId": app_id}) + "---\n"
                    flow += yaml.safe_dump(commands, sort_keys=False, allow_unicode=True)
                    (directory / "flow.yaml").write_text(flow)

        secrets = [os.getenv("TYPESAFE_API_KEY", ""), *values.values()]
        return await self.start("goal", device_id, work, wait, secrets, progress)

    @staticmethod
    def summary(report):
        result = {"status": report["status"], "run_id": report["run_id"], "ms": report["ms"]}
        if report.get("kind", "goal") == "goal":
            result["steps"] = len(report["steps"])
        else:
            result.update(report.get("counts", {}))
        if report.get("error"):
            result["error"] = report["error"]
        if "screen" in report:
            result["screen"] = report["screen"]
        usages = [step["usage"] for step in report["steps"] if isinstance(step.get("usage"), dict)]
        if usages:
            result["jev_tokens"] = {
                "input": sum(u.get("input_tokens", 0) for u in usages),
                "output": sum(u.get("output_tokens", 0) for u in usages),
            }
        return result

    async def report(self, run_id, last_steps=3, wait=0):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id) or not 0 <= last_steps <= 10:
            return {"status": "invalid", "error": "Use returned run_id; last_steps must be 0–10"}
        wait = clamp_wait(wait)
        if run_id in self.jobs:
            _, live = self.jobs[run_id]
            result = await self.wait(run_id, wait)
            if result["status"] == "running":
                result["recent"] = self.recent(live["steps"], last_steps)
                return result
        path = self.output / run_id / "result.json"
        if not path.is_file():
            return {"status": "not_found"}
        report = json.loads(path.read_text())
        result = self.summary(report)
        result["recent"] = self.recent(report["steps"], last_steps)
        if report.get("error_detail"):
            result["error_detail"] = report["error_detail"]
        flow = path.with_name("flow.yaml")
        if flow.is_file():
            result["flow"] = str(flow.resolve())
        return result

    @staticmethod
    def recent(steps, count):
        keys = ("step", "action", "operation", "target", "status", "confidence", "candidates")
        return [
            {k: step[k] for k in keys if k in step} for step in (steps[-count:] if count else [])
        ]


def progress(ctx):
    """Progress notifications keep the request visibly alive for clients that honor them."""

    async def notify(steps):
        with contextlib.suppress(Exception):
            await ctx.report_progress(steps)

    return notify


def create_server(
    *,
    maestro_command="maestro",
    output=Path("runs/mcp"),
    device_id=None,
    app_id=None,
    timeout=None,
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
        maestro = ManagedMaestro(maestro_command)
        async with httpx.AsyncClient(timeout=30) as client:
            model = create_model(client, min_confidence, backend, laya_url)
            mobile = MobileService(
                maestro, model, output, device_id=device_id, app_id=app_id, timeout=timeout
            )
            try:
                yield mobile
            finally:
                await mobile.close()
                await maestro.reset()

    server = FastMCP(
        "jev-mobile",
        lifespan=lifespan,
        instructions=(
            "Mobile simulator control via Maestro. Prefer screen + run_flow for precise "
            "steps and existing Maestro tests; run_goal delegates a bounded goal to a local "
            "model and only verified means assertions passed. device_id defaults to the "
            "configured or single connected device. Long runs return status running with a "
            "run_id: call run_report(run_id, wait=60) until it finishes, or run_cancel."
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
    async def screen(ctx: Context, device_id: str | None = None, raw: bool = False) -> str:
        """Visible elements, one per line: `text #id = value [checked]`. Target them in
        run_flow by text (tapOn: "text") or id (tapOn: {id: "id"}).
        raw: Maestro's unfiltered hierarchy, only to debug a missing element."""
        return compact(await ctx.request_context.lifespan_context.screen(device_id, raw))

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
        wait: int = WAIT,
    ) -> str:
        """Run Maestro steps. Batch every step you can predict into ONE call (each call costs
        a turn): e.g. '- tapOn: "General"\n- tapOn: "About"\n- assertVisible: "iOS Version"'.
        Also: launchApp, tapOn {id}, inputText, eraseText, back, scroll, swipe, pressKey,
        extendedWaitUntil. files: existing flow paths (env optional). Omit app_id if configured.
        The result includes the resulting screen, so a separate screen call is rarely needed.
        Returns running + run_id if not done within wait seconds; then use run_report.
        """
        return compact(
            await ctx.request_context.lifespan_context.run_flow(
                commands, files, device_id, app_id, env, wait, progress(ctx)
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
        wait: int = WAIT,
    ) -> str:
        """Run a mobile goal via Jev. expect_text: exact final texts; values: field ID to text.
        Omit device_id/app_id when configured. Returns compact outcome; only verified is success.
        Returns running + run_id if not done within wait seconds; then use run_report.
        """
        return compact(
            await ctx.request_context.lifespan_context.run(
                goal,
                expect_text,
                device_id,
                app_id,
                values,
                max_steps,
                wait,
                progress(ctx),
            )
        )

    @server.tool(
        structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    )
    async def run_report(run_id: str, ctx: Context, last_steps: int = 3, wait: int = 0) -> str:
        """Read a run summary, up to 10 recent steps, and exported flow path.
        wait: seconds to wait for a running run to finish (max 110)."""
        return compact(await ctx.request_context.lifespan_context.report(run_id, last_steps, wait))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True),
    )
    async def run_cancel(run_id: str, ctx: Context) -> str:
        """Stop a running run_flow/run_goal and its Maestro driver; frees the device."""
        return compact(await ctx.request_context.lifespan_context.cancel(run_id))

    return server
