"""Local Laya policy with a single choice over observed, executable actions."""

import json
from urllib.parse import urlsplit

from .policy import validate_answer

DEFAULT_URL = "http://127.0.0.1:8081"
DEFAULT_MODEL = "aac6fef/laya-typed-decisions-mlx"
DEFAULT_REVISION = "f9e501c2080cc57c13d6887820329758f5351125"


def action_choices(body):
    """Name actions semantically; collapse only nested labels for the same UI control."""
    choices = {}
    elements = {e["index"]: e for e in body["state"]["elements"]}
    for operation in ("TAP", "TYPE_TEXT"):
        question = body["questions"].get(operation.lower() + "_target", {})
        for target, value in question.get("criteria", {}).items():
            label = value["label"] or value["id"]
            name = ("Tap " if operation == "TAP" else "Fill ") + label
            if operation == "TYPE_TEXT":
                name += " with " + json.dumps(value["text_to_enter"], ensure_ascii=False)
            if name in choices:
                previous = elements[choices[name]["target"]]
                current = elements[target]
                a, b = previous["path"], current["path"]
                nested = a and b and (a == b[:len(a)] or b == a[:len(b)])
                same_field = operation == "TAP" or value["id"] == previous["resource_id"]
                if nested and same_field:
                    continue
                name += f" (element {target})"
            choices[name] = {
                "operation": operation, "target": target,
                "value_key": value["id"] if operation == "TYPE_TEXT" else None,
            }
    for operation, name in (
        ("SCROLL_DOWN", "Scroll down"), ("SCROLL_UP", "Scroll up"),
        ("WAIT", "Wait for loading"), ("DONE", "Done: goal complete"), ("BLOCKED", "Blocked"),
    ):
        if operation in body["questions"]["operation"]["criteria"]:
            choices[name] = {"operation": operation, "target": None, "value_key": None}
    return choices


def compact_request(body):
    choices = action_choices(body)
    goal = body["questions"]["operation"]["instructions"]["goal"]
    state = f"Goal: {goal}."
    # Selectable labels are already in the choices. Avoid duplicating the entire hierarchy.
    context = []
    target_labels = {
        e["label"] or e["resource_id"] for e in body["state"]["elements"]
        if any(c["target"] == e["index"] for c in choices.values())
    }
    for element in body["state"]["elements"]:
        label = element["label"] or element["resource_id"]
        if element["value"] or element["checked"] or element["selected"]:
            context.append({
                "label": label, "value": element["value"],
                "checked": element["checked"], "selected": element["selected"],
            })
        elif label not in target_labels:
            context.append(label)
    if context:
        state += "\nOther screen information: " + json.dumps(context, ensure_ascii=False)
    history = body["state"]["recent_actions"][-3:]
    if history:
        state += "\nPrevious actions: " + json.dumps([
            {"action": h.get("action", h["operation"]), "status": h.get("status")}
            for h in history
        ])
    return {
        "state": state,
        "questions": {"action": {
            "type": "choice", "instructions": "Which action should be taken next?",
            "criteria": dict.fromkeys(choices),
        }},
    }


class Laya:
    def __init__(self, client, min_confidence, url=None):
        url = (url or DEFAULT_URL).rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path
        ):
            raise ValueError("LAYA_URL must be a loopback HTTP origin, e.g. http://127.0.0.1:8081")
        self.client = client
        self.url = url
        self.min_confidence = min_confidence

    async def choose(self, body):
        choices = action_choices(body)
        response = await self.client.post(
            self.url + "/v1/systemone", json=compact_request(body), follow_redirects=False
        )
        response.raise_for_status()
        data = response.json()
        answer = data["answers"].get("action", {})
        name = validate_answer(answer, choices, self.min_confidence)
        return {
            **choices[name], "action": name, "confidence": answer["confidence"],
            "model": data.get("model"), "usage": data.get("usage"),
        }
