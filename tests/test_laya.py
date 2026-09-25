import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from jev_mobile.laya import Laya, action_choices, compact_request
from jev_mobile.laya_server import create_app
from jev_mobile.policy import create_model, request_body
from jev_mobile.screen import parse_screen


def request():
    screen = parse_screen((Path(__file__).parents[1] / "examples/screen.json").read_text())
    return request_body(screen, "Open Settings", [], {"search_field": "Coffee"})


def answer(body, choice="Tap Settings"):
    return {"answers": {
        name: {
            "choice": choice, "confidence": 1,
            "probabilities": {key: int(key == choice) for key in question["criteria"]},
        }
        for name, question in body["questions"].items()
    }, "usage": {"input_tokens": 50, "output_tokens": 0}}


def test_local_backend_needs_no_key_and_does_not_forward_a_cloud_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "never-send-this")
    monkeypatch.setenv("JEV_BACKEND", "laya")
    body = request()

    def respond(req):
        assert str(req.url) == "http://127.0.0.1:8081/v1/systemone"
        assert "authorization" not in req.headers
        assert "jev-latest" not in req.content.decode()
        sent = json.loads(req.content)
        assert sent["state"].startswith("Goal: Open Settings")
        assert 'Fill Search with "Coffee"' in sent["questions"]["action"]["criteria"]
        return httpx.Response(200, json=answer(sent))

    async def choose():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await create_model(client, 0.5).choose(body)

    assert asyncio.run(choose())["target"] == "1"


@pytest.mark.parametrize("url", [
    "https://api.typesafe.ai", "http://0.0.0.0:8081", "http://example.com",
    "http://secret@localhost:8081", "http://localhost:8081/redirect",
])
def test_local_backend_rejects_nonlocal_or_ambiguous_origins(url):
    with pytest.raises(ValueError, match="loopback"):
        Laya(None, 0.5, url)


@pytest.mark.parametrize("problem", ["unknown", "low_confidence", "missing_probability"])
def test_local_answers_keep_existing_choice_validation(problem):
    body = request()
    data = answer(compact_request(body))
    if problem == "unknown":
        data["answers"]["action"]["choice"] = "invented"
    elif problem == "low_confidence":
        data["answers"]["action"]["confidence"] = 0.1
    else:
        data["answers"]["action"]["probabilities"].pop("Tap Search")

    async def choose():
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json=data))
        async with httpx.AsyncClient(transport=transport) as client:
            return await Laya(client, 0.5).choose(body)

    with pytest.raises(ValueError):
        asyncio.run(choose())


def test_compact_request_retains_choices_without_mutating_jev_request():
    body = request()
    before = copy.deepcopy(body)
    compact = compact_request(body)
    assert body == before
    choices = action_choices(body)
    assert set(compact["questions"]["action"]["criteria"]) == set(choices)
    assert choices['Fill Search with "Coffee"'] == {
        "operation": "TYPE_TEXT", "target": "2", "value_key": "search_field",
    }
    assert "bounds" not in compact["state"]


def test_local_server_health_prediction_and_validation():
    calls = []

    class Agent:
        def predict(self, state, questions):
            calls.append((state, questions))
            return answer({"questions": questions})

    with TestClient(create_app(loader=Agent)) as client:
        assert client.get("/health").json()["status"] == "ready"
        body = compact_request(request())
        response = client.post("/v1/systemone", json=body)
        assert response.status_code == 200
        assert response.json()["answers"]["action"]["choice"] == "Tap Settings"
        assert response.json()["inference_ms"] >= 0
        assert calls == [(body["state"], body["questions"])]
        for malformed in ([], {}, {"state": "test", "questions": {}}, {"state": "test"}):
            assert client.post("/v1/systemone", json=malformed).status_code == 422
        assert len(calls) == 1


def test_local_policy_refuses_redirects():
    async def choose():
        calls = []

        def redirect(req):
            calls.append(req)
            return httpx.Response(307, headers={"Location": "https://example.com"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await Laya(client, 0.5).choose(request())
        assert len(calls) == 1

    asyncio.run(choose())


def test_nested_labels_collapse_but_distinct_fields_keep_separate_actions():
    screen = parse_screen(json.dumps({"elements": [
        {"b": "[0,0][100,100]", "a11y": "About", "rid": "about", "c": [
            {"b": "[0,0][90,90]", "a11y": "About"},
        ]},
        {"b": "[0,100][100,150]", "a11y": "Name", "rid": "first"},
        {"b": "[0,150][100,200]", "a11y": "Name", "rid": "last"},
    ]}))
    choices = action_choices(request_body(screen, "Open About", [], {"first": "A", "last": "B"}))
    assert sum(name.startswith("Tap About") for name in choices) == 1
    assert sum(name.startswith("Tap Name") for name in choices) == 2
    assert choices['Fill Name with "A"']["value_key"] == "first"
    assert choices['Fill Name with "B"']["value_key"] == "last"


def test_compact_state_preserves_values_states_and_action_history():
    body = request()
    element = body["state"]["elements"][0]
    element.update(value="Enabled", checked=True, selected=True)
    body["state"]["recent_actions"] = [
        {"operation": "TAP", "action": "Tap General", "status": "executed"},
    ]
    state = compact_request(body)["state"]
    assert "Enabled" in state and '"checked": true' in state
    assert '"selected": true' in state and "Tap General" in state
