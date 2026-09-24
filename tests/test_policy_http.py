import asyncio
import json
from pathlib import Path

import httpx

from jev_mobile.policy import Jev, request_body
from jev_mobile.screen import parse_screen


def test_jev_request_and_selected_head_only():
    screen = parse_screen((Path(__file__).parents[1] / "examples/screen.json").read_text())
    body = request_body(screen, "Open Settings", [], {"search_field": "Coffee"})

    def respond(request):
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test-placeholder"
        sent = json.loads(request.content)
        assert sent["questions"]["type_text_target"]["criteria"]["2"]["text_to_enter"] == "Coffee"
        operation_options = sent["questions"]["operation"]["criteria"]
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "answers": {
                    "operation": {
                        "choice": "TAP",
                        "confidence": 1,
                        "probabilities": {op: int(op == "TAP") for op in operation_options},
                    },
                    "tap_target": {
                        "choice": "1",
                        "confidence": 1,
                        "probabilities": {"1": 1, "2": 0},
                    },
                    # Speculative answers cannot override the selected operation's target.
                    "type_text_target": {"choice": "invented-target"},
                },
            },
        )

    async def choose():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await Jev(client, "test-placeholder", 0.5).choose(body)

    decision = asyncio.run(choose())
    assert decision["operation"] == "TAP"
    assert decision["target"] == "1"
    assert decision["value_key"] is None
