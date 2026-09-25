"""Compare local systemone backends on next-UI-action choices, offline (no simulator).

Each case is a screen, a goal, and the one correct next action. Requests go through the
same path run_goal uses (request_body -> Laya.choose), with the confidence gate off so
every answer is recorded. These are hand-written screens, not live runs: use the results
to compare backends with each other, not to claim navigation reliability.

    uv run python examples/compare_backends.py --url laya=http://127.0.0.1:8081 \\
        --url simple-jev=http://127.0.0.1:8082
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from jev_mobile.laya import Laya
from jev_mobile.policy import request_body
from jev_mobile.screen import parse_screen


def screen(*elements):
    """Elements as (label, id) or label; ids make fields typeable when values name them."""
    items = []
    for row, element in enumerate(elements):
        label, rid = element if isinstance(element, tuple) else (element, "")
        items.append({"a11y": label, "rid": rid, "b": f"[0,{row * 60}][390,{row * 60 + 50}]"})
    return parse_screen(json.dumps({"elements": items}))


def done(action):
    return {"operation": "TAP", "action": action, "status": "executed"}


CASES = [
    (
        "settings root",
        screen("Settings", "Wi-Fi", "Bluetooth", "General", "Accessibility", "Privacy & Security"),
        "Open General, then About",
        [],
        {},
        "Tap General",
    ),
    (
        "settings general",
        screen("Settings", "General", "About", "Software Update", "AirDrop", "Keyboard"),
        "Open General, then About",
        [done("Tap General")],
        {},
        "Tap About",
    ),
    (
        "settings about reached",
        screen("General", "About", "iOS Version", "26.5", "Model Name", "iPhone 17"),
        "Open General, then About",
        [done("Tap General"), done("Tap About")],
        {},
        "Done: goal complete",
    ),
    (
        "overtime detail tabs",
        screen("Back", "OT-100", "Audit Log", "Details", "Olivia Rhye, pending", "Afternoon Shift"),
        "Open the audit log of this overtime request",
        [],
        {},
        "Tap Audit Log",
    ),
    (
        "form next",
        screen("Back", "Request Overtime", "Date", "Start Time", "End Time", "Notes", "Next"),
        "Tap Next",
        [],
        {},
        "Tap Next",
    ),
    (
        "login fill email",
        screen("Welcome back", ("Email", "email_field"), ("Password", "password_field"), "Sign In"),
        "Sign in as the test user",
        [],
        {"email_field": "qa@worksy.test", "password_field": "hunter2"},
        'Fill Email with "qa@worksy.test"',
    ),
    (
        "login submit",
        screen(
            "Welcome back",
            ("Email", "email_field"),
            ("Password", "password_field"),
            "Sign In",
        ),
        "Sign in as the test user",
        [
            {
                "operation": "TYPE_TEXT",
                "action": 'Fill Email with "qa@worksy.test"',
                "status": "executed",
            },
            {
                "operation": "TYPE_TEXT",
                "action": 'Fill Password with "hunter2"',
                "status": "executed",
            },
        ],
        {"email_field": "qa@worksy.test", "password_field": "hunter2"},
        "Tap Sign In",
    ),
    (
        "tab bar",
        screen("Dashboard", "Clock in", "Home", "Requests", "Team", "Profile"),
        "Open my profile",
        [],
        {},
        "Tap Profile",
    ),
    (
        "target below the fold",
        screen("Shifts", "Morning Shift", "Midday Shift", "Afternoon Shift", "Evening Shift"),
        "Open the Night Shift",
        [],
        {},
        "Scroll down",
    ),
    (
        "value verified",
        screen("Back", "OT-100", "Compensation Method", "Overtime Pay", "Approver", "Lana Steiner"),
        "Check that the compensation method is Overtime Pay",
        [],
        {},
        "Done: goal complete",
    ),
    (
        "confirm alert",
        screen("Delete this request?", "This can't be undone.", "Cancel", "Delete"),
        "Delete overtime request OT-100",
        [done("Tap Delete request")],
        {},
        "Tap Delete",
    ),
    (
        "choose among many",
        screen(
            "Requests",
            "Search",
            "Filter",
            "Leave",
            "Overtime",
            "Expense",
            "Shift Swap",
            "Remote Work",
            "Business Trip",
            "Training",
            "Equipment",
            "Other",
        ),
        "Create a new expense request",
        [],
        {},
        "Tap Expense",
    ),
]


async def evaluate(name, url, client):
    model = Laya(client, 0.0, url)
    rows = []
    for case, observed, goal, history, values, expected in CASES:
        body = request_body(observed, goal, history, values)
        started = time.perf_counter()
        try:
            decision = await model.choose(body)
            error = None
        except (httpx.HTTPError, KeyError, ValueError) as failure:
            decision, error = {}, str(failure)[:200]
        rows.append(
            {
                "case": case,
                "expected": expected,
                "chosen": decision.get("action"),
                "passed": decision.get("action") == expected,
                "confidence": round(decision.get("confidence", 0), 3),
                "ms": round((time.perf_counter() - started) * 1000),
                **({"error": error} if error else {}),
            }
        )
    passed = sum(row["passed"] for row in rows)
    latencies = sorted(row["ms"] for row in rows)
    return {
        "backend": name,
        "url": url,
        "passed": f"{passed}/{len(rows)}",
        "median_ms": latencies[len(latencies) // 2],
        "cases": rows,
    }


async def main(args):
    results = []
    async with httpx.AsyncClient(timeout=120) as client:
        for spec in args.url:
            name, _, url = spec.partition("=")
            results.append(await evaluate(name, url, client))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2))
    for result in results:
        print(f"\n{result['backend']}: {result['passed']} correct, median {result['median_ms']} ms")
        for row in result["cases"]:
            mark = "ok " if row["passed"] else "BAD"
            got = row.get("error") or f"{row['chosen']} ({row['confidence']})"
            print(f"  {mark} {row['case']:<24} {got}")
    print(f"\nDetails: {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", action="append", required=True, metavar="NAME=URL")
    parser.add_argument("--output", type=Path, default=Path("runs/compare-backends.json"))
    asyncio.run(main(parser.parse_args()))
