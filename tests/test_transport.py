import asyncio
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
