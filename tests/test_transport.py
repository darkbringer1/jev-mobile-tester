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


def fake_maestro(tmp_path, marker):
    import sys

    fake = tmp_path / "maestro"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('fake')\n"
        "@server.tool()\n"
        "def list_devices() -> str:\n"
        "    return '{\"devices\": [], \"pid\": %d}' % os.getpid()\n"
        "@server.tool()\n"
        "def run(device_id: str, yaml: str) -> str:\n"
        "    time.sleep(3)\n"
        f"    open({str(marker)!r}, 'w').close()\n"
        "    return '{\"success\": true}'\n"
        "server.run()\n"
    )
    fake.chmod(0o755)
    return fake


def alive(pid):
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_reset_stops_in_flight_maestro_work_and_restarts(tmp_path):
    from jev_mobile.maestro import ManagedMaestro

    marker = tmp_path / "finished"
    fake = fake_maestro(tmp_path, marker)

    async def exercise():
        maestro = ManagedMaestro(str(fake))
        first = json.loads(await maestro.devices())["pid"]
        call = asyncio.create_task(maestro.for_app("app").run("sim", ["back"]))
        await asyncio.sleep(0.5)
        call.cancel()
        await maestro.reset()
        assert not alive(first)
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


def test_suite_failures_count_failed_flows():
    from jev_mobile.maestro import Maestro, flow_total

    results = [{"file": f"f{i}.yaml", "success": i != 3, "error": "boom"} for i in range(12)]
    text = json.dumps({"success": False, "total_flows": 12, "results": results})
    with pytest.raises(RuntimeError, match=r"^1/12 flows failed: f3.yaml: boom$"):
        Maestro.checked(text)
    assert flow_total(json.dumps({"success": True, "total_flows": 12})) == 12
    assert flow_total("Flow ran") is None


def test_idle_process_releases_and_restarts_on_next_call(tmp_path):
    from jev_mobile.maestro import ManagedMaestro

    fake = fake_maestro(tmp_path, tmp_path / "unused")

    async def exercise():
        maestro = ManagedMaestro(str(fake), idle=0.5)
        first = json.loads(await maestro.devices())["pid"]
        await asyncio.sleep(0.2)
        assert alive(first)  # Still warm between quick calls.
        assert json.loads(await maestro.devices())["pid"] == first
        await asyncio.sleep(3)
        assert not alive(first)  # Released: the driver port is free for other runners.
        second = json.loads(await maestro.devices())["pid"]
        assert second != first
        await maestro.reset()

    asyncio.run(exercise())
