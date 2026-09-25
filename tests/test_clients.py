import json
import tomllib

from jev_mobile.clients import register_codex, register_cursor

SPEC = {"command": "/bin/jev-mobile", "args": ["serve"], "env": {"JEV_APP_ID": "com.x"}}


def test_codex_replaces_only_its_own_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "o3"\n[mcp_servers.jev-mobile]\ncommand = "old"\n'
        '[mcp_servers.jev-mobile.env]\nA = "1"\n[mcp_servers.other]\ncommand = "keep"\n'
    )
    register_codex(SPEC, False)
    register_codex(SPEC, False)
    data = tomllib.loads(config.read_text())
    assert data["model"] == "o3"
    assert data["mcp_servers"]["other"] == {"command": "keep"}
    server = data["mcp_servers"]["jev-mobile"]
    assert server["command"] == "/bin/jev-mobile"
    assert server["env"] == {"JEV_APP_ID": "com.x"}
    assert server["tool_timeout_sec"] > 180
    assert (tmp_path / "config.toml.bak").exists()


def test_cursor_merges_project_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text('{"mcpServers": {"other": {"command": "x"}}}')
    register_cursor(SPEC, False)
    servers = json.loads(path.read_text())["mcpServers"]
    assert servers == {"other": {"command": "x"}, "jev-mobile": SPEC}
