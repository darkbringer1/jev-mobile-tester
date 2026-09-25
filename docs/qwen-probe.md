# Qwen3.5-4B as a local decision model — probe results

Status: wired in as a local runtime (`jev-mobile laya-serve --runtime qwen`). Not yet
calibrated or measured live; see "Using it" and "Next" below.

## Why

`run_goal` refused trivial goals with Laya on a real iOS app: "Tap Next" with Next on screen
scored 0.19 against the 0.5 gate. In agent runs, every Claude turn re-reads ~26k tokens of
base context, so decisions a local model can make on its own are the largest token saving
available (see [benchmarks.md](benchmarks.md)).

## Method

`examples/qwen_probe.py`, standalone (no server, no simulator):

- Options come from a closed list, the same shape `laya.action_choices()` builds
  ("Tap X", "Fill X with …", "Scroll down", "Done: goal complete", "Blocked").
- Options are labelled A, B, C…; one forward pass, softmax over the letter tokens' logits.
  One probability per option, no length bias, the same `choice/probabilities/confidence`
  contract Laya returns, so `validate_answer` and the confidence gate apply unchanged.
- Chat template with `enable_thinking=False`.
- Model: `mlx-community/Qwen3.5-4B-MLX-4bit` (3.0 GB). Download with `HF_HUB_DISABLE_XET=1`:
  the default Xet transfer stalled at a few MB here; plain HTTP ran at ~3.5 MB/s.

## Results (M-series Mac, 2026-09-25)

| Goal | Pick | Confidence |
|---|---|---|
| Tap Next on a form (Laya: 0.19) | Tap Next ✅ | **0.99** |
| Open a request's detail from a listing | Tap OT-100 ✅ | 0.93 |
| Open a listing from a module hub | Tap Self Overtime ✅ | 0.98 |
| Open My Payroll (tile visible) | Tap My Payroll ✅ | 0.93 |
| Open Payroll (tile not visible) | Tap Menu ✅ (Menu lists it) | 0.75 |
| Log in, fields empty | Fill Email ✅ | 0.43 |
| Log in, both filled, **with** history | Tap LOGIN ✅ | 0.97 |
| Log in, both filled, **without** history | Fill Email again ❌ | 0.56 |
| Requested value on screen → goal done | Done ✅ | **0.50** (Edit 0.44) |

8/9 right. Load 0.7 s from disk; first decision ~1.2 s (warm-up), then **55–110 ms**
per decision at 106–133 prompt tokens.

## Reading

- Clear navigation steps: right and sure (0.93–0.99).
- The one miss had no action history; jev always sends history. Its confidence (0.56) sat
  below a ~0.6 gate, so a gate there would have escalated instead of acting.
- Weak spot: deciding the goal is **done** (0.50, barely above "Tap Edit"). Likely prompt
  fixes: state what counts as done (e.g. "the requested value is visible"), and let the Done
  option carry what was found.
- 9 hand-written cases: good for comparing models, not a calibration set.

## Using it

`QwenRuntime` in `laya_server.py` serves the same `/v1/systemone` contract as Laya, so the
jev client doesn't change. The model is pinned to revision `32f3e8e` and needs the
`laya` extra (which now includes `mlx-lm`):

```sh
uv sync --locked --extra laya
HF_HUB_DISABLE_XET=1 uv run --extra laya hf download mlx-community/Qwen3.5-4B-MLX-4bit  # once, 2.8 GB
uv run --extra laya jev-mobile laya-serve --runtime qwen --port 8082
```

Point jev at it: `JEV_BACKEND=laya LAYA_URL=http://127.0.0.1:8082`, e.g. with
`jev-mobile setup --laya-url http://127.0.0.1:8082`. `run_goal` then decides locally
with Qwen. Laya's service on 8081 keeps running unchanged.

## Wired-in comparison (examples/compare_backends.py, 12 other hand-written cases)

These cases go through jev's real request path (`request_body` → `compact_request`), not
the probe's own prompt.

| | Correct | Median per decision | Right when confidence ≥ 0.5 |
|---|---|---|---|
| Laya | 5/12 | 12 ms | 5 of 9 |
| Qwen3.5-4B | **8/12** | 61 ms | **7 of 8** |

Qwen got the sequences Laya fails: moving on after a tap (General → About), recognising a
finished goal, tapping Sign In once both fields were filled, and confirming a dialog.
Misses: scrolling toward an off-screen target (chose Done, 0.46), a value already on
screen (0.30), a tab choice (0.42), and one confident miss: on an empty login form it
chose "Tap Sign In" (0.74) instead of filling Email. jev's request doesn't say the fields
are empty (targets are listed only as choices), so the next step is a prompt fix.

## Next

1. Prompt: show current field values in "Fill" choices, and state what counts as done.
2. Calibrate before trusting the gate: label decisions from existing Maestro flows (each
   step = screen + goal + correct action), hold out whole screens, fit a temperature on
   held-out data, and pick the threshold for ~95% precision instead of 0.5.
3. Live `run_goal` on the real app (in the app's repo), measuring Claude tokens, steps,
   and escalations against the agent driving by itself.
