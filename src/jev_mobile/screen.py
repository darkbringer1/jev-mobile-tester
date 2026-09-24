"""Normalize Maestro compact JSON and its older CSV hierarchy format."""

import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from itertools import pairwise


@dataclass(frozen=True)
class Element:
    index: str
    label: str
    resource_id: str
    role: str
    value: str
    bounds: str
    operations: tuple[str, ...]
    checked: bool = False
    selected: bool = False
    aliases: tuple[str, ...] = ()
    path: tuple[int, ...] = ()


@dataclass
class Screen:
    elements: list[Element]
    fingerprint: str

    def state(self):
        return [asdict(element) for element in self.elements]


def flag(value, default=False):
    if value is None or value == "":
        return default
    return value is True or str(value).lower() in {"true", "1"}


def parse_screen(payload: str) -> Screen:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        rows = list(csv.DictReader(io.StringIO(payload)))
        if not rows or "bounds" not in rows[0]:
            raise ValueError(
                "Unsupported hierarchy; expected Maestro compact JSON or CSV"
            ) from None
        nodes = rows
    else:
        if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
            raise ValueError("Unsupported hierarchy; expected an elements array")  # noqa: TRY004

        def flatten(items, parent=()):
            for index, item in enumerate(items):
                path = (*parent, index)
                yield {**item, "_path": path}
                yield from flatten(item.get("c", []), path)

        nodes = list(flatten(data["elements"]))

    elements = []
    for node in nodes:
        bounds = str(node.get("b", node.get("bounds", "")))
        coords = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", bounds)
        if not coords:
            continue
        x1, y1, x2, y2 = map(int, coords.groups())
        if x2 <= x1 or y2 <= y1 or x2 <= 0 or y2 <= 0:
            continue
        label = str(
            node.get("a11y")
            or node.get("accessibility")
            or node.get("txt")
            or node.get("text")
            or node.get("hint")
            or ""
        )
        rid = str(node.get("rid") or node.get("resource_id") or "")
        role = str(node.get("cls") or node.get("class") or "")
        value = str(node.get("val") or node.get("value") or "")
        editable = any(part in role.lower() for part in ("textfield", "textview", "edittext"))
        # Unlabelled nodes remain context only unless they have a stable resource ID.
        operations = []
        if flag(node.get("enabled"), True) and (label or rid):
            operations.append("TAP")
            if editable:
                operations.append("TYPE_TEXT")
        if label or rid or operations:
            elements.append(
                Element(
                    str(len(elements) + 1),
                    label,
                    rid,
                    role,
                    value,
                    bounds,
                    tuple(operations),
                    flag(node.get("checked")),
                    flag(node.get("selected")),
                    tuple(
                        str(node[key])
                        for key in ("a11y", "accessibility", "txt", "text", "val", "value", "hint")
                        if node.get(key)
                    ),
                    tuple(node.get("_path", ())),
                )
            )
    canonical = json.dumps([asdict(e) for e in elements], sort_keys=True)
    return Screen(elements, hashlib.sha256(canonical.encode()).hexdigest())


def literal_regex(value: str) -> str:
    # Maestro uses Java regex, whose literal quoting differs from Python's re.escape.
    return "\\Q" + value.replace("\\E", "\\E\\\\E\\Q") + "\\E"


def selector(element: Element, screen: Screen) -> dict:
    for attribute, key in (("resource_id", "id"), ("label", "text")):
        value = getattr(element, attribute)
        matches = [
            e
            for e in screen.elements
            if (
                value.casefold() in {item.casefold() for item in e.aliases}
                if key == "text"
                else e.resource_id.casefold() == value.casefold()
            )
        ]
        # Native accessibility often exposes both a row and its nested text label.
        # Repeated labels on separate branches remain ambiguous.
        ordered = sorted(matches, key=lambda e: len(e.path))
        nested = (
            bool(ordered)
            and all(e.path for e in ordered)
            and all(
                len(parent.path) < len(child.path) and child.path[: len(parent.path)] == parent.path
                for parent, child in pairwise(ordered)
            )
        )
        if value and (len(matches) == 1 or nested):
            if "${" in value:
                raise ValueError("Maestro expression syntax in element text is unsupported")
            return {key: literal_regex(value)}
    raise ValueError("Target has no unique ID or label; refusing an ambiguous tap")
