import asyncio
import json
from pathlib import Path

import pytest
import yaml
from mcp.shared.memory import create_connected_server_and_client_session

from jev_mobile.screen import parse_screen
from jev_mobile.server import MobileService, compact, create_server


class Device:
    def __init__(self, fail_assert=False):
        self.calls = []
        self.app_id = None
        self.fail_assert = fail_assert
        self.screen = parse_screen((Path(__file__).parents[1] / "examples/screen.json").read_text())

    def for_app(self, app_id):
        bound = Device(self.fail_assert)
        bound.calls = self.calls
        bound.app_id = app_id
        return bound

    async def devices(self):
        return json.dumps(
            {
                "devices": [
                    {"device_id": "sim", "name": "iPhone", "platform": "ios", "connected": True},
                    {"device_id": "off", "name": "Off", "platform": "ios", "connected": False},
                ]
            }
        )

    async def observe(self, device_id):
        return self.screen

    async def run(self, device_id, commands):
        self.calls.append((self.app_id, commands))
        if self.fail_assert and "assertVisible" in commands[0]:
            raise RuntimeError("assertion failed")


class Model:
    def __init__(self, *operations):
        self.operations = iter(operations or ("DONE",))

    async def choose(self, body):
        return {
            "operation": next(self.operations),
            "target": "1",
            "usage": {"input_tokens": 100, "output_tokens": 5},
        }


def service(tmp_path, model=None, **kwargs):
    return MobileService(
        Device(), model or Model(), tmp_path, device_id="sim", app_id="com.example.app", **kwargs
    )


def test_goal_returns_compact_summary_and_keeps_trace_local(tmp_path):
    svc = service(tmp_path, Model("TAP", "DONE"))
    response = asyncio.run(svc.run("Open Settings", ["Settings"]))
    assert response["status"] == "verified"
    assert response["steps"] == 2
    assert response["jev_tokens"] == {"input": 200, "output": 10}
    assert len(compact(response)) < 256
    assert "elements" not in response and "commands" not in response
    directory = tmp_path / response["run_id"]
    assert len((directory / "steps.jsonl").read_text().splitlines()) == 2
    config, flow = list(yaml.safe_load_all((directory / "flow.yaml").read_text()))
    assert config == {"appId": "com.example.app"}
    assert flow[0] == {"launchApp": {"appId": "com.example.app", "stopApp": False}}
    assert flow[-1] == {"assertVisible": {"text": r"\QSettings\E"}}


def test_missing_key_does_not_launch_or_touch_device(tmp_path):
    svc = MobileService(Device(), None, tmp_path, device_id="sim", app_id="app")
    assert asyncio.run(svc.run("Open settings"))["status"] == "unavailable"
    assert svc.maestro.calls == []
    assert not list(tmp_path.iterdir())


def test_missing_assertions_cannot_be_verified(tmp_path):
    response = asyncio.run(service(tmp_path).run("Open Settings"))
    assert response["status"] == "done_unverified"


def test_failure_preserves_report_and_previous_commands(tmp_path):
    svc = service(tmp_path, Model("TAP", "DONE"))
    svc.maestro.fail_assert = True
    response = asyncio.run(svc.run("Open Settings", ["Settings"]))
    assert response["status"] == "error"
    report = json.loads((tmp_path / response["run_id"] / "result.json").read_text())
    assert len(report["commands"]) == 2  # Launch and tap succeeded; assertion did not.
    assert response["error"] == "assertion failed"


def test_timeout_stops_loop_and_persists_result(tmp_path):
    class SlowModel:
        async def choose(self, body):
            await asyncio.sleep(10)

    svc = service(tmp_path, SlowModel(), timeout=0.01)
    result = asyncio.run(svc.run("Open Settings"))
    assert result["status"] == "timeout"
    assert len(svc.maestro.calls) == 1
    assert svc.report(result["run_id"])["status"] == "timeout"


def test_concurrent_run_is_busy_and_cancellation_releases_lock(tmp_path):
    async def exercise():
        entered = asyncio.Event()

        class WaitingModel:
            async def choose(self, body):
                entered.set()
                await asyncio.Event().wait()

        svc = service(tmp_path, WaitingModel())
        first = asyncio.create_task(svc.run("Wait"))
        await entered.wait()
        assert await svc.run("Other goal") == {"status": "busy"}
        assert await svc.devices() == {"status": "busy"}
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not svc.lock.locked()
        report = json.loads(next(tmp_path.glob("*/result.json")).read_text())
        assert report["status"] == "cancelled"

    asyncio.run(exercise())


def test_report_is_bounded_and_rejects_path_traversal(tmp_path):
    svc = service(tmp_path, Model("TAP", "TAP", "DONE"))
    result = asyncio.run(svc.run("Open Settings"))
    assert len(svc.report(result["run_id"], 1)["recent"]) == 1
    assert svc.report(result["run_id"], 0)["recent"] == []
    assert svc.report("../../.env")["status"] == "invalid"
    assert svc.report(result["run_id"], 11)["status"] == "invalid"


def test_invalid_literals_rejected_before_device_actions(tmp_path):
    svc = service(tmp_path)
    result = asyncio.run(svc.run("Open", values={"field": "${dangerous()}"}))
    assert result["status"] == "invalid"
    assert svc.maestro.calls == []


def test_app_override_does_not_change_default_session(tmp_path):
    svc = service(tmp_path, Model("DONE", "DONE"))
    asyncio.run(svc.run("Open", app_id="com.other.app"))
    asyncio.run(svc.run("Open"))
    assert [app for app, _ in svc.maestro.calls] == ["com.other.app", "com.example.app"]
    assert svc.maestro.app_id is None


def test_full_mcp_protocol_no_duplicate_payload(tmp_path):
    async def exercise():
        svc = service(tmp_path, Model("TAP", "DONE"))
        async with create_connected_server_and_client_session(create_server(service=svc)) as client:
            tools = (await client.list_tools()).tools
            assert {t.name for t in tools} == {"devices", "run_goal", "run_report"}
            assert all(t.outputSchema is None for t in tools)
            devices = await client.call_tool("devices", {})
            assert json.loads(devices.content[0].text)["devices"] == [
                {"id": "sim", "name": "iPhone", "platform": "ios"}
            ]
            result = await client.call_tool(
                "run_goal", {"goal": "Open Settings", "expect_text": ["Settings"]}
            )
            assert not result.isError
            assert result.structuredContent is None
            assert len(result.content) == 1
            response = json.loads(result.content[0].text)
            assert response["status"] == "verified"
            assert len(result.content[0].text) < 256
            report = await client.call_tool("run_report", {"run_id": response["run_id"]})
            assert len(json.loads(report.content[0].text)["recent"]) == 2
            # Protocol schema validation rejects malformed calls before execution.
            bad = await client.call_tool("run_goal", {"goal": {"unexpected": "object"}})
            assert bad.isError

    asyncio.run(exercise())


def test_device_transport_errors_stay_compact(tmp_path):
    class BrokenDevice:
        async def devices(self):
            raise RuntimeError("connection failed " + "details " * 1000)

    svc = MobileService(BrokenDevice(), None, tmp_path)
    result = asyncio.run(svc.devices())
    assert result["status"] == "error"
    assert len(compact(result)) < 300
