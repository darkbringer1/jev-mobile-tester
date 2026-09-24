import asyncio
import copy
import json
import math
from pathlib import Path

import pytest

from jev_mobile.agent import action_commands, run_agent
from jev_mobile.policy import request_body, validate_answer
from jev_mobile.screen import literal_regex, parse_screen, selector


@pytest.fixture
def screen():
    return parse_screen((Path(__file__).parents[1] / "examples/screen.json").read_text())


def test_compact_ios_hierarchy_and_compatible_targets(screen):
    assert len(screen.elements) == 2
    body = request_body(screen, "Search for Coffee", [], {"search_field": "Coffee"})
    assert set(body["questions"]["tap_target"]["criteria"]) == {"1", "2"}
    assert set(body["questions"]["type_text_target"]["criteria"]) == {"2"}
    assert body["questions"]["type_text_target"]["criteria"]["2"]["text_to_enter"] == "Coffee"


def test_disabled_element_remains_context_but_cannot_be_targeted():
    screen = parse_screen(
        json.dumps({"elements": [{"b": "[0,0][100,100]", "txt": "Save", "enabled": False}]})
    )
    assert screen.elements[0].operations == ()
    assert "TAP" not in request_body(screen, "Save", [], {})["questions"]["operation"]["criteria"]


def test_ambiguous_labels_are_not_offered():
    screen = parse_screen(
        json.dumps(
            {
                "elements": [
                    {"b": "[0,0][100,100]", "txt": "Open"},
                    {"b": "[0,100][100,200]", "txt": "Open"},
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="unique"):
        selector(screen.elements[0], screen)
    assert "tap_target" not in request_body(screen, "Open", [], {})["questions"]


def test_java_regex_literal_escaping():
    assert literal_regex("A.* (B)") == r"\QA.* (B)\E"
    assert literal_regex(r"A\EB") == r"\QA\E\\E\QB\E"


@pytest.mark.parametrize(
    "answer",
    [
        {"choice": "C", "probabilities": {"A": 1, "B": 0}, "confidence": 1},
        {"choice": "A", "probabilities": {"A": math.nan, "B": 0}, "confidence": 1},
        {"choice": "A", "probabilities": {"A": 1}, "confidence": 1},
        {"choice": "B", "probabilities": {"A": 0.9, "B": 0.1}, "confidence": 1},
        {"choice": "A", "probabilities": {"A": True, "B": False}, "confidence": 1},
    ],
)
def test_invalid_model_choices_fail_closed(answer):
    with pytest.raises(ValueError, match="Invalid"):
        validate_answer(answer, {"A": "first", "B": "second"}, 0.5)


def test_confidence_threshold():
    with pytest.raises(ValueError, match="below threshold"):
        validate_answer(
            {"choice": "A", "probabilities": {"A": 1}, "confidence": 0.2}, {"A": "only choice"}, 0.5
        )


def test_action_uses_observed_id(screen):
    commands = action_commands({"operation": "TAP", "target": "1"}, screen, {})
    assert commands == [{"tapOn": {"id": r"\Qsettings_button\E"}}]


def test_text_rejects_maestro_expression(screen):
    with pytest.raises(ValueError, match="expression"):
        action_commands(
            {"operation": "TYPE_TEXT", "target": "2", "value_key": "query"},
            screen,
            {"search_field": "${1 + 1}"},
        )


class FakeMaestro:
    def __init__(self, screens, fail_assert=False):
        self.screens = iter(screens)
        self.calls = []
        self.fail_assert = fail_assert

    async def observe(self, device):
        return next(self.screens)

    async def run(self, device, commands):
        self.calls.append(commands)
        if self.fail_assert:
            raise RuntimeError("assertion failed")


class FakeModel:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)

    async def choose(self, body):
        return next(self.decisions)


def test_stale_screen_never_executes_old_target(screen):
    fresh = copy.deepcopy(screen)
    fresh.fingerprint = "changed"
    maestro = FakeMaestro([screen, fresh, fresh])
    model = FakeModel({"operation": "TAP", "target": "1"}, {"operation": "BLOCKED"})
    result = asyncio.run(run_agent(maestro, model, "device", "Open Settings", {}, [], max_steps=2))
    assert maestro.calls == []
    assert result.steps[0]["status"] == "stale_reobserve"
    assert result.status == "blocked"


def test_done_requires_external_assertions(screen):
    maestro = FakeMaestro([screen])
    result = asyncio.run(
        run_agent(
            maestro, FakeModel({"operation": "DONE"}), "device", "Open Settings", {}, ["Settings"]
        )
    )
    assert result.status == "verified"
    assert maestro.calls == [[{"assertVisible": {"text": r"\QSettings\E"}}]]


def test_done_without_assertions_is_unverified(screen):
    maestro = FakeMaestro([screen])
    result = asyncio.run(
        run_agent(maestro, FakeModel({"operation": "DONE"}), "device", "Open Settings", {}, [])
    )
    assert result.status == "done_unverified"
    assert maestro.calls == []


def test_failed_assertion_cannot_report_success(screen):
    maestro = FakeMaestro([screen], fail_assert=True)
    with pytest.raises(RuntimeError, match="assertion failed"):
        asyncio.run(
            run_agent(
                maestro,
                FakeModel({"operation": "DONE"}),
                "device",
                "Open Settings",
                {},
                ["Settings"],
            )
        )


def test_step_budget_stops_repeated_actions(screen):
    maestro = FakeMaestro([screen, screen])
    result = asyncio.run(
        run_agent(
            maestro,
            FakeModel({"operation": "TAP", "target": "1"}),
            "device",
            "Open Settings",
            {},
            [],
            max_steps=1,
        )
    )
    assert result.status == "step_limit"
    assert len(maestro.calls) == 1


def test_ios_field_without_role_can_be_explicitly_bound():
    screen = parse_screen(
        json.dumps({"elements": [{"b": "[0,0][200,40]", "a11y": "Search", "rid": "search_field"}]})
    )
    body = request_body(screen, "Search Coffee", [], {"search_field": "Coffee"})
    assert set(body["questions"]["type_text_target"]["criteria"]) == {"1"}
    commands = action_commands(
        {"operation": "TYPE_TEXT", "target": "1"}, screen, {"search_field": "Coffee"}
    )
    assert commands[-1] == {"inputText": "Coffee"}


def test_case_insensitive_labels_are_ambiguous():
    screen = parse_screen(
        json.dumps(
            {
                "elements": [
                    {"b": "[0,0][100,50]", "txt": "Save"},
                    {"b": "[0,50][100,100]", "txt": "SAVE"},
                ]
            }
        )
    )
    assert "tap_target" not in request_body(screen, "Save", [], {})["questions"]


def test_switch_state_changes_fingerprint():
    data = {"elements": [{"b": "[0,0][100,50]", "a11y": "Wi-Fi", "checked": False}]}
    before = parse_screen(json.dumps(data))
    data["elements"][0]["checked"] = True
    assert before.fingerprint != parse_screen(json.dumps(data)).fingerprint


def test_nested_native_row_and_label_share_a_target():
    screen = parse_screen(
        json.dumps(
            {
                "elements": [
                    {
                        "b": "[0,0][300,60]",
                        "a11y": "About",
                        "c": [{"b": "[10,10][100,50]", "a11y": "About"}],
                    }
                ]
            }
        )
    )
    assert selector(screen.elements[0], screen) == {"text": r"\QAbout\E"}
    assert selector(screen.elements[1], screen) == {"text": r"\QAbout\E"}
