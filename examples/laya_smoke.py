"""Real local Laya requests, optionally followed by model-controlled iOS navigation."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from jev_mobile.laya import DEFAULT_URL, Laya
from jev_mobile.maestro import connect
from jev_mobile.server import MobileService


async def smoke(args):
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"classification": []}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(args.url + "/health")
        response.raise_for_status()
        report["server"] = response.json()
        questions = {
            "department": {
                "type": "choice", "instructions": "Which department should handle this?",
                "criteria": {
                    "billing": "payments, invoices and refunds",
                    "technical": "software bugs, crashes and errors",
                    "sales": "pricing and purchasing a new plan",
                },
            }
        }
        for state, expected in (
            ("I was charged twice. Please refund the duplicate payment.", "billing"),
            ("The app crashes every time I try to open it.", "technical"),
            ("How much does an enterprise plan cost for a new customer?", "sales"),
        ):
            response = await client.post(
                args.url + "/v1/systemone", json={"state": state, "questions": questions}
            )
            response.raise_for_status()
            data = response.json()
            actual = data["answers"]["department"]["choice"]
            report["classification"].append({
                "expected": expected, "actual": actual, "passed": actual == expected,
                "inference_ms": data["inference_ms"],
                "confidence": data["answers"]["department"]["confidence"],
            })
        if args.device:
            async with connect(app_id="com.apple.Preferences") as maestro:
                # Setup only: no scripted General/About taps. Laya chooses every goal action.
                await maestro.run(args.device, [
                    {"launchApp": "com.apple.Preferences"}, {"openLink": "App-prefs:"},
                ])
                screen = await maestro.observe(args.device)
                (args.output / "initial-screen.json").write_text(json.dumps(screen.state(), indent=2))
                service = MobileService(
                    maestro, Laya(client, args.min_confidence, args.url), args.output,
                    device_id=args.device, app_id="com.apple.Preferences", timeout=180,
                )
                report["mobile"] = await service.run(
                    "Open General, then About", ["About", "iOS Version"], max_steps=8
                )
    report["passed"] = all(case["passed"] for case in report["classification"]) and (
        not args.device or report["mobile"]["status"] == "verified"
    )
    (args.output / "smoke.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--device", help="Booted English iOS simulator; navigates Settings")
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=Path("runs/laya-smoke"))
    raise SystemExit(asyncio.run(smoke(parser.parse_args())))
