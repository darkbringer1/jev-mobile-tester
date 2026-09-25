"""Probe: can a general local LLM (default Qwen3.5-4B, MLX 4-bit) pick the next UI action
from a closed list, and how sure is it? Standalone: no server, no simulator.

Scoring: options are labelled A, B, C...; the model's next-token logits over those letters
give one probability per option in a single forward pass (no length bias). That matches the
probabilities/confidence contract Laya returns, so the same confidence gate applies.

    uv venv qwenv && uv pip install --python qwenv/bin/python mlx-lm
    HF_HUB_DISABLE_XET=1 qwenv/bin/hf download mlx-community/Qwen3.5-4B-MLX-4bit
    qwenv/bin/python examples/qwen_probe.py [model-id]

Hand-written screens: compare models with each other, don't read it as navigation reliability.
"""
import string
import sys
import time

import mlx.core as mx
from mlx_lm import load

MODEL = sys.argv[1] if len(sys.argv) > 1 else "mlx-community/Qwen3.5-4B-MLX-4bit"
LETTERS = string.ascii_uppercase

t0 = time.perf_counter()
model, tok = load(MODEL)
print(f"load {time.perf_counter() - t0:.1f}s")

letter_ids = []
for ch in LETTERS:
    ids = tok.encode(ch, add_special_tokens=False)
    assert len(ids) == 1, (ch, ids)
    letter_ids.append(ids[0])


def decide(goal, screen, options, history=()):
    labels = LETTERS[: len(options)]
    lines = "\n".join(f"{l}. {o}" for l, o in zip(labels, options))
    user = (
        f"You are testing a mobile app. Goal: {goal}\n"
        f"Screen: {screen}\n"
        + (f"Previous actions: {', '.join(history)}\n" if history else "")
        + f"Options:\n{lines}\n"
        "Reply with the letter of the single best next action."
    )
    messages = [{"role": "user", "content": user}]
    try:
        prompt = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                         enable_thinking=False)
    except TypeError:
        prompt = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
    ids = mx.array(tok.encode(prompt, add_special_tokens=False))[None]
    t = time.perf_counter()
    logits = model(ids)[0, -1]
    picked = mx.take(logits, mx.array(letter_ids[: len(options)]))
    probs = mx.softmax(picked.astype(mx.float32)).tolist()
    ms = (time.perf_counter() - t) * 1000
    best = max(range(len(options)), key=probs.__getitem__)
    ranked = sorted(zip(options, probs), key=lambda x: -x[1])[:3]
    return options[best], probs[best], ms, ranked, len(ids[0])


CASES = [
    ("Tap Next on the Overtime form",
     "Overtime, Overtime Details, Date Sep 30 2026, Shift Afternoon Shift, Compensation Type Select",
     ["Tap Back", "Tap Save Draft", "Tap Date", "Tap Compensation Type", "Tap Next",
      "Scroll down", "Done: goal complete", "Blocked"], "Tap Next"),
    ("Open request OT-100's detail",
     "Self Overtime, All, Draft, Pending, Approved, OT-101 Pending, OT-100 Draft, New Request",
     ["Tap Back", "Tap All", "Tap Draft", "Tap OT-101", "Tap OT-100", "Tap New Request",
      "Scroll down", "Done: goal complete", "Blocked"], "Tap OT-100"),
    ("Open the Self Overtime listing",
     "Time, Incomplete Entry, Change Shift, Absent, Short Hours, Self Overtime, Planned Overtime",
     ["Tap Back", "Tap Incomplete Entry", "Tap Change Shift", "Tap Absent", "Tap Short Hours",
      "Tap Self Overtime", "Tap Planned Overtime", "Done: goal complete", "Blocked"],
     "Tap Self Overtime"),
    ("Read the Compensation Method of OT-100",
     "OT-100 detail, Status Draft, Date 18 Sep 2026, Compensation Method Overtime Pay, Reason Heavy Workload",
     ["Tap Back", "Tap Edit", "Scroll down", "Done: goal complete", "Blocked"],
     "Done: goal complete"),
    ("Log in with the mock account",
     "Let's get started, EMAIL, ID, QR, MSFT, Email, Password, Remember me, LOGIN, Forgot Password?",
     ['Fill Email with "user@example.com"', 'Fill Password with "password123"', "Tap LOGIN",
      "Tap Forgot Password?", "Tap QR", "Blocked"], 'Fill Email with "user@example.com"'),
    ("Log in with the mock account",
     "Let's get started, Email user@example.com, Password ••••••, Remember me, LOGIN",
     ['Fill Email with "user@example.com"', 'Fill Password with "password123"', "Tap LOGIN",
      "Tap Forgot Password?", "Blocked"], "Tap LOGIN",
     ('Fill Email with "user@example.com" (done)', 'Fill Password with "password123" (done)')),
    # Same screen without history: the one miss (0.56). jev always sends history.
    ("Log in with the mock account",
     "Let's get started, Email user@example.com, Password ••••••, Remember me, LOGIN",
     ['Fill Email with "user@example.com"', 'Fill Password with "password123"', "Tap LOGIN",
      "Tap Forgot Password?", "Blocked"], "Tap LOGIN"),
    ("Open the Payroll page from the dashboard",
     "Hello, Alex, Calendar, OCR Claim, Leave, My Payslip, Clock In, Notice Board, Quick Action",
     ["Tap Calendar", "Tap Leave", "Tap My Payslip", "Tap Clock In", "Scroll down",
      "Tap Menu", "Done: goal complete", "Blocked"], "Tap Menu"),   # Menu lists My Payroll
    ("Open My Payroll",
     "Hello, Alex, Calendar, Leave, My Payslip, Clock In, Notice Board, Quick Action, My Payroll",
     ["Tap Calendar", "Tap My Payslip", "Tap My Payroll", "Scroll down", "Tap Menu",
      "Done: goal complete", "Blocked"], "Tap My Payroll"),
]

right = 0
for goal, screen, options, expected, *history in CASES:
    choice, conf, ms, ranked, n = decide(goal, screen, options, history[0] if history else ())
    ok = choice == expected
    right += ok
    top = "; ".join(f"{o} {p:.2f}" for o, p in ranked)
    print(f"{'OK ' if ok else 'BAD'} conf={conf:.2f} {ms:5.0f}ms {n:3d}tok | {goal} -> {choice} | {top}")
print(f"{right}/{len(CASES)} right")
