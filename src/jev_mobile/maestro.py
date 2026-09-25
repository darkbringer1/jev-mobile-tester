"""A persistent stdio MCP connection to the user's Maestro installation."""

import json
import os
from contextlib import asynccontextmanager

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

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

    async def observe(self, device):
        return parse_screen(await self.call("inspect_screen", {"device_id": device}))

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
