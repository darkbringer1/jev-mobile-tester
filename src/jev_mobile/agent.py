"""Bounded observe/decide/validate/act loop with independent completion assertions."""

import asyncio
import time
from dataclasses import dataclass, field

from .policy import LowConfidence, request_body
from .screen import literal_regex, selector


@dataclass
class RunResult:
    status: str
    steps: list[dict] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    elapsed_ms: float = 0


def action_commands(decision, screen, values):
    operation = decision["operation"]
    if operation in {"SCROLL_UP", "SCROLL_DOWN"}:
        return [{"swipe": {"direction": "DOWN" if operation == "SCROLL_UP" else "UP"}}]
    if operation not in {"TAP", "TYPE_TEXT"}:
        raise ValueError(f"Unsupported action: {operation}")
    element = next((e for e in screen.elements if e.index == decision["target"]), None)
    allowed = element is not None and (
        operation in element.operations
        if operation == "TAP"
        else "TAP" in element.operations and element.resource_id in values
    )
    if not allowed:
        raise ValueError("Decision targets an unavailable element or operation")
    commands = [{"tapOn": selector(element, screen)}]
    if operation == "TYPE_TEXT":
        value = values[element.resource_id]
        if not isinstance(value, str) or "${" in value:
            raise ValueError("Field values must be strings without Maestro expression syntax")
        if element.value:
            commands.append({"eraseText": len(element.value)})
        commands.append({"inputText": value})
    return commands


async def run_agent(
    maestro,
    model,
    device,
    goal,
    values,
    expected_text,
    *,
    max_steps=30,
    emit=None,
    on_commands=None,
):
    started = time.perf_counter()
    result = RunResult("step_limit")
    history = []
    try:
        for step in range(max_steps):
            timings = {}
            phase = time.perf_counter()
            screen = await maestro.observe(device)
            timings["observe_ms"] = (time.perf_counter() - phase) * 1000
            body = request_body(screen, goal, history, values)
            phase = time.perf_counter()
            try:
                decision = await model.choose(body)
            except LowConfidence as error:
                # Record what the model leaned toward; run_report surfaces it.
                entry = {
                    "step": step + 1,
                    "status": "low_confidence",
                    "confidence": error.confidence,
                    "candidates": [{"action": n, "p": round(p, 3)} for n, p in error.candidates],
                }
                result.steps.append(entry)
                if emit:
                    emit(entry)
                raise
            timings["decision_ms"] = (time.perf_counter() - phase) * 1000
            entry = {"step": step + 1, **decision, "timings": timings}
            operation = decision["operation"]
            phase = time.perf_counter()
            if operation == "BLOCKED":
                entry["status"] = "blocked"
                result.status = "blocked"
            elif operation == "DONE":
                if expected_text:
                    checks = [
                        {"assertVisible": {"text": literal_regex(text)}} for text in expected_text
                    ]
                    if any("${" in text for text in expected_text):
                        raise ValueError("Assertion text cannot contain Maestro expression syntax")
                    await maestro.run(device, checks)
                    result.commands.extend(checks)
                    if on_commands:
                        on_commands(checks)
                    result.status = "verified"
                else:
                    result.status = "done_unverified"
                entry["status"] = result.status
            elif operation == "WAIT":
                await asyncio.sleep(0.3)
                entry["status"] = "waited"
            else:
                # A full second observation is deliberate for the initial prototype.
                fresh = await maestro.observe(device)
                if fresh.fingerprint != screen.fingerprint:
                    entry["status"] = "stale_reobserve"
                else:
                    commands = action_commands(decision, screen, values)
                    await maestro.run(device, commands)
                    result.commands.extend(commands)
                    if on_commands:
                        on_commands(commands)
                    entry["status"] = "executed"
            timings["execute_ms"] = (time.perf_counter() - phase) * 1000
            result.steps.append(entry)
            history.append({**decision, "status": entry["status"]})
            if emit:
                emit(entry)
            if operation in {"DONE", "BLOCKED"}:
                break
    finally:
        result.elapsed_ms = (time.perf_counter() - started) * 1000
    return result
