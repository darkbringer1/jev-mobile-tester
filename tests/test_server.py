import asyncio
import json
from pathlib import Path

import pytest
import yaml
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ImageContent

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

    async def screenshot(self, device_id):
        return [ImageContent(type="image", data="cG5n", mimeType="image/png")]

    async def run_files(self, device_id, files, env=None):
        self.calls.append((None, files))

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
    assert asyncio.run(svc.report(result["run_id"]))["status"] == "timeout"


def test_concurrent_run_is_busy_and_cancellation_releases_lock(tmp_path):
    async def exercise():
        entered = asyncio.Event()

        class WaitingModel:
            async def choose(self, body):
                entered.set()
                await asyncio.Event().wait()

        svc = service(tmp_path, WaitingModel())
        svc.maestro.resets = 0

        async def reset():
            svc.maestro.resets += 1

        svc.maestro.reset = reset
        first = asyncio.create_task(svc.run("Wait"))
        await entered.wait()
        run_id = next(iter(svc.jobs))
        assert await svc.run("Other goal") == {"status": "busy", "run_id": run_id}
        assert await svc.devices() == {"status": "busy", "run_id": run_id}
        # An abandoned tool call leaves the run going; its result stays retrievable.
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert svc.lock.locked()
        assert (await svc.report(run_id))["status"] == "running"
        cancelled = await svc.cancel(run_id)
        assert cancelled["status"] == "cancelled"
        assert svc.maestro.resets == 1  # Maestro restarted so its driver stops too.
        assert not svc.lock.locked()
        report = json.loads(next(tmp_path.glob("*/result.json")).read_text())
        assert report["status"] == "cancelled"
        assert (await svc.cancel(run_id))["status"] == "not_running"

    asyncio.run(exercise())


def test_report_is_bounded_and_rejects_path_traversal(tmp_path):
    svc = service(tmp_path, Model("TAP", "TAP", "DONE"))
    result = asyncio.run(svc.run("Open Settings"))
    assert len(asyncio.run(svc.report(result["run_id"], 1))["recent"]) == 1
    assert asyncio.run(svc.report(result["run_id"], 0))["recent"] == []
    assert asyncio.run(svc.report("../../.env"))["status"] == "invalid"
    assert asyncio.run(svc.report(result["run_id"], 11))["status"] == "invalid"
    # Oversized waits are capped rather than rejected.
    assert asyncio.run(svc.report(result["run_id"], wait=500))["status"] == result["status"]


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
            assert {t.name for t in tools} == {
                "devices",
                "screen",
                "screenshot",
                "run_flow",
                "run_goal",
                "run_report",
                "run_cancel",
            }
            assert all(t.outputSchema is None for t in tools)
            devices = await client.call_tool("devices", {})
            assert json.loads(devices.content[0].text)["devices"] == [
                {"id": "sim", "name": "iPhone", "platform": "ios"}
            ]
            image = await client.call_tool("screenshot", {})
            assert [c.type for c in image.content] == ["image"]
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


def test_screen_is_compact_and_omits_empty_fields(tmp_path):
    result = asyncio.run(service(tmp_path).screen())
    assert result["device"] == "sim"
    assert result["elements"] and all(item for item in result["elements"])
    assert not any(v == "" for item in result["elements"] for v in item.values())


def test_run_flow_wraps_commands_with_default_app(tmp_path):
    svc = service(tmp_path)
    result = asyncio.run(svc.run_flow('- tapOn: "General"\n- back'))
    assert result.keys() == {"status", "run_id", "ms", "steps", "screen"}
    assert (result["status"], result["steps"]) == ("passed", 2)
    assert {"text": "Settings", "id": "settings_button"} in result["screen"]
    report = json.loads((tmp_path / result["run_id"] / "result.json").read_text())
    assert report["commands"] == [{"tapOn": "General"}, "back"]
    assert svc.maestro.calls == [("com.example.app", [{"tapOn": "General"}, "back"])]


def test_run_flow_accepts_full_flow_and_files(tmp_path):
    svc = MobileService(Device(), Model(), tmp_path, device_id="sim")
    flow = "appId: com.full.app\n---\n- launchApp\n"
    assert asyncio.run(svc.run_flow(flow))["status"] == "passed"
    assert asyncio.run(svc.run_flow(files=["flows/login.yaml"]))["status"] == "passed"
    assert svc.maestro.calls[0] == ("com.full.app", ["launchApp"])
    assert svc.maestro.calls[1][1][0].endswith("flows/login.yaml")


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"commands": "- back", "files": ["a.yaml"]}, {"commands": "tapOn: x"}, {"commands": ":"}],
)
def test_run_flow_rejects_invalid_input_before_device_actions(tmp_path, kwargs):
    svc = service(tmp_path)
    assert asyncio.run(svc.run_flow(**kwargs))["status"] == "invalid"
    assert svc.maestro.calls == []


def test_single_connected_device_is_used_by_default(tmp_path):
    svc = MobileService(Device(), Model(), tmp_path, app_id="com.example.app")
    assert asyncio.run(svc.run("Open"))["status"] == "done_unverified"
    assert asyncio.run(svc.screen())["device"] == "sim"


def test_long_flow_returns_run_id_and_report_waits_for_result(tmp_path):
    async def exercise():
        release = asyncio.Event()

        class SlowDevice(Device):
            async def run(self, device_id, commands):
                await release.wait()

        svc = MobileService(SlowDevice(), None, tmp_path, device_id="sim", app_id="app")
        pending = await svc.run_flow("- back", wait=0)
        assert pending["status"] == "running"
        assert (await svc.report(pending["run_id"], wait=0))["status"] == "running"
        asyncio.get_running_loop().call_later(0.05, release.set)
        done = await svc.report(pending["run_id"], wait=5)
        assert done["status"] == "passed"
        assert not svc.lock.locked()

    asyncio.run(exercise())


def test_foreign_maestro_driver_refuses_device_actions(tmp_path):
    svc = service(tmp_path)

    async def foreign(device):
        return [4242]

    svc.maestro.foreign_drivers = foreign
    for call in (svc.screen(), svc.run_flow("- back"), svc.run("Open")):
        result = asyncio.run(call)
        assert result["status"] == "busy"
        assert "4242" in result["error"]
    assert svc.maestro.calls == []
    assert not svc.lock.locked()


def test_raw_screen_returns_maestro_hierarchy(tmp_path):
    svc = service(tmp_path)

    async def inspect(device):
        return '{"elements": []}'

    svc.maestro.inspect = inspect
    assert asyncio.run(svc.screen(raw=True)) == {"device": "sim", "raw": '{"elements": []}'}


def test_low_confidence_step_is_reported_with_candidates(tmp_path):
    from jev_mobile.policy import LowConfidence

    class Unsure:
        async def choose(self, body):
            raise LowConfidence(0.19, 0.5, {"Tap Next": 0.19, "Tap Back": 0.1, "Blocked": 0.05})

    svc = service(tmp_path, Unsure())
    result = asyncio.run(svc.run("Tap Next"))
    assert result["status"] == "error"
    assert "Tap Next 0.19" in result["error"]
    recent = asyncio.run(svc.report(result["run_id"]))["recent"]
    assert recent[-1]["status"] == "low_confidence"
    assert recent[-1]["candidates"][0] == {"action": "Tap Next", "p": 0.19}


def test_failed_flow_reports_reason_and_resulting_screen(tmp_path):
    class FailingDevice(Device):
        def for_app(self, app_id):
            return self

        async def run(self, device_id, commands):
            raise RuntimeError("login.yaml: Assertion is false: " + '"Welcome" is visible ' * 30)

    svc = MobileService(FailingDevice(), None, tmp_path, device_id="sim", app_id="app")
    result = asyncio.run(svc.run_flow("- assertVisible: Welcome"))
    assert result["status"] == "error"
    assert result["error"].startswith("login.yaml: Assertion is false")
    assert result["screen"]
    report = asyncio.run(svc.report(result["run_id"]))
    assert len(report["error_detail"]) > len(result["error"])
