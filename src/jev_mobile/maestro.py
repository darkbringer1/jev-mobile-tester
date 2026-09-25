"""A persistent stdio MCP connection to the user's Maestro installation."""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
from collections import defaultdict
from contextlib import asynccontextmanager

import anyio
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED

from .screen import parse_screen


class Maestro:
    def __init__(self, session, tools, app_id=None):
        self.session = session
        self.tools = tools
        self.app_id = app_id

    def for_app(self, app_id):
        return Maestro(self.session, self.tools, app_id)

    async def call_raw(self, name, arguments):
        result = await self.session.call_tool(name, arguments)
        text = "\n".join(item.text for item in result.content if item.type == "text")
        if result.isError or text.startswith(("Failed to ", "Error:")):
            raise RuntimeError(f"Maestro {name} failed: {text}")
        return result, text

    async def call(self, name, arguments):
        return (await self.call_raw(name, arguments))[1]

    async def screenshot(self, device):
        result, _ = await self.call_raw("take_screenshot", {"device_id": device})
        return [item for item in result.content if item.type == "image"]

    async def devices(self):
        return await self.call("list_devices", {})

    async def inspect(self, device):
        return await self.call("inspect_screen", {"device_id": device})

    async def observe(self, device):
        return parse_screen(await self.inspect(device))

    async def run(self, device, commands):
        if not self.app_id or "${" in self.app_id:
            raise ValueError("A literal app ID is required to execute Maestro commands")
        flow = (
            yaml.safe_dump({"appId": self.app_id}, sort_keys=False)
            + "---\n"
            + yaml.safe_dump(commands, sort_keys=False, allow_unicode=True)
        )
        if "run" in self.tools:
            tool, args = "run", {"device_id": device, "yaml": flow}
        elif "run_flow" in self.tools:
            tool, args = "run_flow", {"device_id": device, "flow_yaml": flow}
        else:
            raise RuntimeError("Maestro MCP must expose run or run_flow")
        return self.checked(await self.call(tool, args))

    async def run_files(self, device, files, env=None):
        if "run" not in self.tools:
            raise RuntimeError("Running flow files needs Maestro's run tool")
        args = {"device_id": device, "files": files, **({"env": env} if env else {})}
        return self.checked(await self.call("run", args))

    @staticmethod
    def checked(text):
        # Older releases may return a command failure without setting MCP isError.
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            result = {}
        if result.get("success") is False or result.get("status", "").lower() in {
            "failed",
            "error",
        }:
            raise RuntimeError(f"Maestro command failed: {text}")
        return text


@asynccontextmanager
async def connect(command="maestro", app_id=None):
    params = StdioServerParameters(
        command=command,
        args=["mcp", "--no-viewer"],
        env={
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "HOME",
                "JAVA_HOME",
                "DEVELOPER_DIR",
                "ANDROID_HOME",
                "ANDROID_SDK_ROOT",
                "MAESTRO_DRIVER_STARTUP_TIMEOUT",
            }
        },
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=None) as session,
    ):
        await session.initialize()
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        yield Maestro(session, tools, app_id)


def process_table():
    output = subprocess.run(
        ["ps", "-Ao", "pid=,ppid=,command="], capture_output=True, text=True, check=False
    ).stdout
    rows = []
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def descendants(rows, root):
    children = defaultdict(list)
    for pid, parent, _ in rows:
        children[parent].append(pid)
    found, pending = set(), [root]
    while pending:
        for child in children[pending.pop()]:
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def drivers(rows, device=None):
    """iOS Maestro drivers: `xcodebuild test-without-building` on a maestro-driver xctestrun."""
    return [
        pid
        for pid, _, command in rows
        if "xcodebuild" in command
        and "maestro-driver" in command
        and (device is None or f"id={device}" in command)
    ]


def foreign_drivers(device, root=None):
    """Drivers on this device that another Maestro process started."""
    rows = process_table()
    ours = descendants(rows, root or os.getpid())
    return [pid for pid in drivers(rows, device) if pid not in ours]


def kill_own_drivers(root=None):
    rows = process_table()
    for pid in drivers([row for row in rows if row[0] in descendants(rows, root or os.getpid())]):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)


def closed(error):
    if isinstance(error, McpError):
        return error.error.code == CONNECTION_CLOSED
    return isinstance(error, anyio.ClosedResourceError | anyio.BrokenResourceError)


class MaestroProcess:
    """One Maestro MCP process, started on first use and restarted to stop in-flight work.

    Maestro keeps executing a flow after its MCP caller gives up, so cancelling a run
    must end the process (and the xcodebuild driver in its process group).
    """

    def __init__(self, command="maestro"):
        self.command = command
        self.current = None
        self.task = None
        self.stop = None
        self.starting = asyncio.Lock()

    async def client(self):
        async with self.starting:
            if self.current is None:
                ready = asyncio.get_running_loop().create_future()
                self.stop = asyncio.Event()
                self.task = asyncio.create_task(self.hold(ready, self.stop))
                await ready
            return self.current

    async def hold(self, ready, stop):
        try:
            async with connect(self.command) as maestro:
                self.current = maestro
                ready.set_result(None)
                await stop.wait()
        except Exception as error:  # noqa: BLE001 -- surface startup failure; later ones restart
            if not ready.done():
                ready.set_exception(error)
        finally:
            self.current = None
            if not ready.done():
                ready.cancel()

    async def reset(self):
        task, self.task = self.task, None
        if task is None:
            return
        await asyncio.to_thread(kill_own_drivers)
        self.stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), 15)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.current = None


class ManagedMaestro:
    """The Maestro interface over a restartable process; see MaestroProcess."""

    def __init__(self, command="maestro", app_id=None, process=None):
        self.process = process or MaestroProcess(command)
        self.app_id = app_id

    def for_app(self, app_id):
        return ManagedMaestro(app_id=app_id, process=self.process)

    async def call(self, method, *args):
        maestro = (await self.process.client()).for_app(self.app_id)
        try:
            return await getattr(maestro, method)(*args)
        except Exception as error:
            if closed(error):
                await self.process.reset()
            raise

    async def devices(self):
        return await self.call("devices")

    async def inspect(self, device):
        return await self.call("inspect", device)

    async def observe(self, device):
        return await self.call("observe", device)

    async def screenshot(self, device):
        return await self.call("screenshot", device)

    async def run(self, device, commands):
        return await self.call("run", device, commands)

    async def run_files(self, device, files, env=None):
        return await self.call("run_files", device, files, env)

    async def reset(self):
        await self.process.reset()

    async def foreign_drivers(self, device):
        return await asyncio.to_thread(foreign_drivers, device)
