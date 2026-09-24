"""Check the real stdio server and Maestro connection without paid calls or device actions."""

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check():
    params = StdioServerParameters(
        command=str(Path(sys.executable).parent / "jev-mobile"),
        args=["serve"],
        env={"PATH": os.environ["PATH"], "TYPESAFE_API_KEY": ""},
    )
    async with (
        stdio_client(params) as (reader, writer),
        ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=120)) as client,
    ):
        await client.initialize()
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == {"devices", "run_goal", "run_report"}
        result = await client.call_tool("devices", {})
        assert not result.isError
        devices = json.loads(result.content[0].text)["devices"]
        unavailable = await client.call_tool(
            "run_goal",
            {
                "goal": "No actions should execute without a key",
                "device_id": "not-used",
                "app_id": "not-used",
            },
        )
        assert json.loads(unavailable.content[0].text)["status"] == "unavailable"
        assert unavailable.structuredContent is None
        print(
            json.dumps(
                {
                    "status": "passed",
                    "tools": [t.name for t in tools],
                    "connected_devices": len(devices),
                    "missing_key_response_bytes": len(unavailable.content[0].text.encode()),
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(check())
