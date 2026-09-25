import sys

import pytest

from jev_mobile.cli import main

SERVER = """#!{python}
import json
from mcp.server.fastmcp import FastMCP
server = FastMCP("fake")
@server.tool()
def list_devices() -> str:
    return json.dumps({{"devices": [{{"device_id": "emulator-1", "connected": True}}]}})
@server.tool()
def run(device_id: str, files: list[str] | None = None, dir: str | None = None,
        include_tags: list[str] | None = None) -> str:
    names = files or ["a.yaml", "b.yaml"]
    failed = [n for n in names if "broken" in n]
    results = [{{"file": n, "success": n not in failed, "error": "Assertion is false"}}
               for n in names]
    return json.dumps({{"success": not failed, "total_flows": len(names), "results": results}})
server.run()
"""


@pytest.fixture
def maestro(tmp_path):
    fake = tmp_path / "maestro"
    fake.write_text(SERVER.format(python=sys.executable))
    fake.chmod(0o755)
    return str(fake)


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["jev-mobile", *argv])
    with pytest.raises(SystemExit) as exit_info:
        main()
    return exit_info.value.code


def test_suite_pass_prints_one_line(tmp_path, maestro, monkeypatch, capsys):
    flows = tmp_path / "flows"
    flows.mkdir()
    code = run_cli(monkeypatch, "--maestro", maestro, "test", str(flows), "--include-tags", "smoke")
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    assert len(out) == 1
    assert out[0].startswith("jev-mobile test: passed 2 flows in ")


def test_failure_names_the_flow_and_exits_nonzero(tmp_path, maestro, monkeypatch, capsys):
    good, broken = tmp_path / "good.yaml", tmp_path / "broken.yaml"
    good.write_text("appId: a\n---\n- back\n")
    broken.write_text("appId: a\n---\n- back\n")
    code = run_cli(monkeypatch, "--maestro", maestro, "test", str(good), str(broken))
    out = capsys.readouterr().out
    assert code == 1
    assert "1/2 flows failed: broken.yaml: Assertion is false" in out


def test_tags_require_a_directory(tmp_path, maestro, monkeypatch, capsys):
    flow = tmp_path / "a.yaml"
    flow.write_text("appId: a\n---\n- back\n")
    code = run_cli(monkeypatch, "--maestro", maestro, "test", str(flow), "--include-tags", "x")
    assert code == 1
    assert "need a directory" in capsys.readouterr().err
