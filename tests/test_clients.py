import json
import tomllib

from jev_mobile import clients
from jev_mobile.clients import claude_configs, register_claude, register_codex, register_cursor

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
    assert server["tool_timeout_sec"] == 600
    assert (tmp_path / "config.toml.bak").exists()


def test_cursor_merges_project_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text('{"mcpServers": {"other": {"command": "x"}}}')
    register_cursor(SPEC, False)
    servers = json.loads(path.read_text())["mcpServers"]
    assert servers == {"other": {"command": "x"}, "jev-mobile": SPEC}


def test_claude_configs_find_default_and_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    (tmp_path / ".claude.json").write_text("{}")
    (tmp_path / ".claude-work").mkdir()
    (tmp_path / ".claude-work" / ".claude.json").write_text("{}")
    (tmp_path / ".claude-empty").mkdir()
    (tmp_path / ".claude.json.backup").write_text("{}")
    assert claude_configs() == [None, tmp_path / ".claude-work"]
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude-work"))
    assert claude_configs() == [None, tmp_path / ".claude-work"]


def test_register_claude_targets_config_dir(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/elsewhere")
    monkeypatch.setattr(clients.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw["env"])))
    register_claude(SPEC, True, tmp_path)
    register_claude(SPEC, True, None)
    assert [env.get("CLAUDE_CONFIG_DIR") for _, env in calls] == [str(tmp_path)] * 2 + [None] * 2
    assert calls[1][0][:6] == ["claude", "mcp", "add", "jev-mobile", "-s", "user"]


def test_rival_maestro_servers_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(clients.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    config = {
        "mcpServers": {
            "jev-mobile": {"command": "/bin/jev-mobile", "args": ["--maestro", "maestro"]}
        },
        "projects": {
            "/code/app": {"mcpServers": {"maestro": {"command": "maestro", "args": ["mcp"]}}}
        },
    }
    (tmp_path / ".claude.json").write_text(json.dumps(config))
    assert clients.rival_maestro_servers() == ["~/.claude.json: maestro (/code/app)"]
