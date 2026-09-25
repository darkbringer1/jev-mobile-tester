"""Loopback-only, single-model MLX serving. No TypeSafe calls or API keys."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial

from .laya import DEFAULT_MODEL, DEFAULT_REVISION


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


def create_app(model_id=DEFAULT_MODEL, revision=DEFAULT_REVISION, loader=None):
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
                load = partial(MLXRuntime, model_id, revision)
            else:
                load = loader
            agent = await loop.run_in_executor(pool, load)
            app.state.agent = agent
            app.state.pool = pool
            yield

    async def health(request):
        return JSONResponse({
            "status": "ready", "backend": "laya-mlx", "model": model_id,
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


def serve(port=8081, model_id=DEFAULT_MODEL, revision=DEFAULT_REVISION):
    import uvicorn

    uvicorn.run(create_app(model_id, revision), host="127.0.0.1", port=port, workers=1)
