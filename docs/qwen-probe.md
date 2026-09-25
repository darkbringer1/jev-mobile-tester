# Qwen3.5-4B as a local decision model — probe results

Status: probe only. Nothing in `src/` uses Qwen yet. This page is the handoff for wiring it in.

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

## Wiring it in (for the implementing agent)

1. Add a `QwenRuntime` next to `MLXRuntime` in `laya_server.py` with the same
   `predict(state, questions) -> {"answers": {name: {choice, probabilities, confidence}}}`.
   `create_app(loader=...)` already accepts a custom loader, so the client (`laya.py`)
   does not change. Serve it on another port (e.g. 8082).
2. Compare with `examples/compare_backends.py --url laya=… --url qwen=…`.
3. Calibrate before trusting the gate: label decisions from existing Maestro flows
   (each step = screen + goal + correct action), hold out whole screens, fit a
   temperature on held-out data, and pick the threshold for ~95% precision instead of 0.5.
4. Then a live `run_goal` on a simulator, measuring Claude tokens, steps, and escalations.
