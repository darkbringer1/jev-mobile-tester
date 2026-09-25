"""Loopback-only, single-model MLX serving. No TypeSafe calls or API keys."""

import asyncio
import json
import string
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial

from .laya import DEFAULT_MODEL, DEFAULT_REVISION

QWEN_MODEL = "mlx-community/Qwen3.5-4B-MLX-4bit"
QWEN_REVISION = "32f3e8ecf65426fc3306969496342d504bfa13f3"
LABELS = string.ascii_uppercase + string.ascii_lowercase


class MLXRuntime:
    def __init__(self, model_id, revision):
        import laya_mlx

        self.agent = laya_mlx.load(model_id, revision=revision, device="gpu")

    def predict(self, state, questions):
        from laya_mlx.common import build_prefix, serialize_state

        agent = self.agent
        state_tokens = agent.tok(
            serialize_state(state).replace(agent.tok.mask_token, " "), add_special_tokens=False
        )["input_ids"]
        for question in questions.values():
            prefix, _ = build_prefix(
                agent.tok, agent._to_internal(question), agent.cfg.get("head_max_len", 192)
            )
            if len(prefix) + len(state_tokens) + 1 > agent.cfg.get("max_len", 512):
                raise ValueError("Laya context exceeded; shorten the goal or narrow the screen")
        return agent.predict(state, questions)


def choice_prompt(state, question, labels):
    state = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    instructions = question.get("instructions") or "Choose one option."
    if not isinstance(instructions, str):
        instructions = json.dumps(instructions, ensure_ascii=False)
    options = "\n".join(
        f"{label}. {name}" + (f" ({description})" if description else "")
        for label, (name, description) in zip(labels, question["criteria"].items())
    )
    return (
        f"You are testing a mobile app.\n{state}\n{instructions}\nOptions:\n{options}\n"
        "Reply with the letter of the single best option."
    )


class QwenRuntime:
    """A general instruction model used as a closed-choice classifier.

    Options are labelled A, B, C...; one forward pass, then a softmax over the label
    tokens' logits gives one probability per option with no length bias: the same
    choice/probabilities/confidence contract Laya returns (see docs/qwen-probe.md).
    """

    def __init__(self, model_id=QWEN_MODEL, revision=QWEN_REVISION, loaded=None):
        if loaded is None:
            from mlx_lm import load

            loaded = load(model_id, revision=revision)
        self.model, self.tok = loaded
        self.label_ids = []
        for label in LABELS:
            ids = self.tok.encode(label, add_special_tokens=False)
            if len(ids) != 1:
                break
            self.label_ids.append(ids[0])

    def render(self, content):
        messages = [{"role": "user", "content": content}]
        try:
            return self.tok.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=False, enable_thinking=False
            )
        except TypeError:
            return self.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)

    def score(self, prompt, count):
        import mlx.core as mx

        ids = mx.array(self.tok.encode(prompt, add_special_tokens=False))[None]
        logits = self.model(ids)[0, -1]
        picked = mx.take(logits, mx.array(self.label_ids[:count])).astype(mx.float32)
        return mx.softmax(picked).tolist(), ids.shape[1]

    def predict(self, state, questions):
        answers, tokens = {}, 0
        for name, question in questions.items():
            if question.get("type") != "choice":
                raise ValueError("The Qwen runtime answers choice questions only")
            criteria = question.get("criteria") or {}
            if not 2 <= len(criteria) <= len(self.label_ids):
                raise ValueError(f"Choice questions need 2–{len(self.label_ids)} options")
            labels = LABELS[: len(criteria)]
            probabilities, count = self.score(
                self.render(choice_prompt(state, question, labels)), len(criteria)
            )
            tokens += count
            ranked = dict(zip(criteria, probabilities))
            choice = max(ranked, key=ranked.get)
            answers[name] = {
                "type": "choice",
                "choice": choice,
                "confidence": ranked[choice],
                "probabilities": ranked,
            }
        return {"answers": answers, "usage": {"input_tokens": tokens, "output_tokens": 0}}


RUNTIMES = {
    "laya": (MLXRuntime, DEFAULT_MODEL, DEFAULT_REVISION),
    "qwen": (QwenRuntime, QWEN_MODEL, QWEN_REVISION),
}


def create_app(model_id=DEFAULT_MODEL, revision=DEFAULT_REVISION, loader=None, backend="laya"):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    @asynccontextmanager
    async def lifespan(app):
        # All MLX work stays on one worker, including requests whose clients time out.
        with ThreadPoolExecutor(max_workers=1) as pool:
            loop = asyncio.get_running_loop()
            if loader is None:
                load = partial(RUNTIMES[backend][0], model_id, revision)
            else:
                load = loader
            agent = await loop.run_in_executor(pool, load)
            app.state.agent = agent
            app.state.pool = pool
            yield

    async def health(request):
        return JSONResponse({
            "status": "ready", "backend": f"{backend}-mlx", "model": model_id,
            "revision": revision, "device": "gpu",
        })

    async def predict(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or "state" not in body:
                raise ValueError("Expected an object with state and questions")
            questions = body.get("questions")
            if not isinstance(questions, dict) or not 1 <= len(questions) <= 10:
                raise ValueError("Provide 1–10 questions")
            started = time.perf_counter()
            result = await asyncio.get_running_loop().run_in_executor(
                request.app.state.pool,
                partial(request.app.state.agent.predict, body["state"], questions),
            )
            return JSONResponse({
                **result, "model": model_id,
                "inference_ms": round((time.perf_counter() - started) * 1000, 2),
            })
        except (ValueError, TypeError, KeyError) as error:
            return JSONResponse({"error": str(error)}, status_code=422)

    return Starlette(
        routes=[Route("/health", health), Route("/v1/systemone", predict, methods=["POST"])],
        lifespan=lifespan,
    )


def serve(port=8081, runtime="laya", model_id=None, revision=None):
    import uvicorn

    _, default_model, default_revision = RUNTIMES[runtime]
    app = create_app(
        model_id or default_model,
        revision or (default_revision if model_id is None else None),
        backend=runtime,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, workers=1)
