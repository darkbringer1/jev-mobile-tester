import asyncio
import json
from types import SimpleNamespace

import pytest
import yaml

from jev_mobile.maestro import Maestro


class Session:
    def __init__(self, text, is_error=False):
        self.text = text
        self.is_error = is_error
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            isError=self.is_error,
            content=[SimpleNamespace(type="text", text=self.text)],
        )


def test_flow_has_required_config_and_document_separator():
    session = Session('{"success": true}')
    maestro = Maestro(session, {"run": object()}, "com.example.demo")
    asyncio.run(maestro.run("simulator", [{"tapOn": {"id": r"\Qsettings\E"}}]))
    name, arguments = session.calls[0]
    assert name == "run"
    documents = list(yaml.safe_load_all(arguments["yaml"]))
    assert documents == [{"appId": "com.example.demo"}, [{"tapOn": {"id": r"\Qsettings\E"}}]]


@pytest.mark.parametrize(
    "text,is_error",
    [
        ('{"success": false}', False),
        ("Failed to run flow: Element not found", False),
        ("Element not found", True),
    ],
)
def test_command_errors_never_become_success(text, is_error):
    maestro = Maestro(Session(text, is_error), {"run": object()}, "com.example.demo")
    with pytest.raises(RuntimeError):
        asyncio.run(maestro.run("simulator", [{"assertVisible": "Missing"}]))


def test_reset_stops_in_flight_maestro_work_and_restarts(tmp_path):
    import os
    import sys

    from jev_mobile.maestro import ManagedMaestro

    marker = tmp_path / "finished"
    fake = tmp_path / "maestro"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('fake')\n"
        "@server.tool()\n"
        "def list_devices() -> str:\n"
        '    return \'{"devices": [], "pid": %d}\' % os.getpid()\n'
        "@server.tool()\n"
        "def run(device_id: str, yaml: str) -> str:\n"
        "    time.sleep(3)\n"
        f"    open({str(marker)!r}, 'w').close()\n"
        "    return '{\"success\": true}'\n"
        "server.run()\n"
    )
    fake.chmod(0o755)

    async def exercise():
        maestro = ManagedMaestro(str(fake))
        first = json.loads(await maestro.devices())["pid"]
        call = asyncio.create_task(maestro.for_app("app").run("sim", ["back"]))
        await asyncio.sleep(0.5)
        call.cancel()
        await maestro.reset()
        with pytest.raises(ProcessLookupError):
            os.kill(first, 0)
        second = json.loads(await maestro.devices())["pid"]
        assert second != first
        await maestro.reset()
        await asyncio.sleep(3)
        assert not marker.exists()  # The abandoned flow never completed.

    asyncio.run(exercise())


def test_flow_file_failures_lead_with_the_reason():
    from jev_mobile.maestro import Maestro

    text = json.dumps(
        {
            "success": False,
            "total_commands_executed": 0,
            "results": [
                {
                    "file": "/very/long/path/" * 20 + "overtime.yaml",
                    "success": False,
                    "error": 'Assertion is false: "Total" is visible',
                }
            ],
        }
    )
    with pytest.raises(RuntimeError, match=r'^overtime.yaml: Assertion is false: "Total"'):
        Maestro.checked(text)
