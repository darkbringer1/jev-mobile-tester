"""Scripted Maestro smoke test; no Jev calls and no AI performance claim."""

import argparse
import asyncio

from jev_mobile.agent import action_commands
from jev_mobile.cli import error_message
from jev_mobile.maestro import connect
from jev_mobile.screen import selector


async def smoke(device):
    async with connect(app_id="com.apple.Preferences") as maestro:
        await maestro.run(
            device, [{"launchApp": "com.apple.Preferences"}, {"openLink": "App-prefs:"}]
        )
        for label in ("General", "About"):
            for attempt in range(8):
                screen = await maestro.observe(device)
                candidates = []
                for element in screen.elements:
                    if element.label == label:
                        try:
                            selector(element, screen)
                        except ValueError:
                            continue
                        candidates.append(element)
                identified = [e for e in candidates if e.resource_id]
                candidates = identified or candidates
                unique = {str(selector(e, screen)): e for e in candidates}
                candidates = list(unique.values())
                if len(candidates) == 1:
                    commands = action_commands(
                        {"operation": "TAP", "target": candidates[0].index}, screen, {}
                    )
                    await maestro.run(device, commands)
                    expected = "About" if label == "General" else "iOS Version"
                    await maestro.run(device, [{"assertVisible": expected}])
                    print(f"Tapped observed {label}; verified {expected}", flush=True)
                    break
                await maestro.run(device, [{"swipe": {"direction": "DOWN"}}])
            else:
                raise RuntimeError(f"Could not find unique {label!r}; use English Settings root")
        await maestro.run(device, [{"assertVisible": "About"}, {"assertVisible": "iOS Version"}])
        print("PASS: scripted Settings → General → About with independent assertions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(smoke(args.device))
    except Exception as error:  # noqa: BLE001 -- concise command line error
        raise SystemExit(error_message(error)) from None
