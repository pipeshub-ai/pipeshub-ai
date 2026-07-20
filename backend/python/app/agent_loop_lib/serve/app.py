from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.control_plane.config import ControlPlaneConfig
from app.agent_loop_lib.control_plane.control_plane import ControlPlane
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.serve.playground import PLAYGROUND_HTML

"""SSE serving (Phase 4): exposes `Agent.stream()`'s AG-UI-aligned event
stream (agent/streaming.py, events/base.py) over plain HTTP as
Server-Sent Events, plus a zero-build-step static playground for manually
poking a running agent from a browser.

Deliberately a single `POST /runs` endpoint that starts a run and streams
it to completion in one request/response — like OpenAI's streaming
completions — rather than a create-then-poll/subscribe pair, since there's
no session storage or multi-client fan-out concern to justify the extra
surface: each request gets exactly one Agent, one run, one client.

ONE ControlPlane is built at app startup (Starlette lifespan) and shared
by every request — expensive to construct (storage backends, transports),
cheap to reuse — while each request gets its own fresh `Agent` (an
`AgentSpec` from `ControlPlane.make_spec()` bound to the shared
`ControlPlane.runtime`), exactly the CLI's per-query pattern (cli.py's
run_query/repl) just reachable over HTTP instead of a terminal.
"""


def _sse_frame(event: str, data: dict[str, Any]) -> bytes:
    # SSE frames are newline-delimited; a bare "\n" inside `data` would
    # split the field across multiple "data:" lines per spec, so this
    # relies on json.dumps never emitting literal newlines (it doesn't,
    # by default, for standard str/number/bool/None/dict/list content).
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


async def _agent_event_stream(agent: Agent, goal: Goal) -> AsyncIterator[bytes]:
    yield _sse_frame("run_started", {"goal": goal.description})
    try:
        async for event in agent.stream(goal):
            yield _sse_frame(event.event_type.value, event.payload)
        result = agent.last_stream_result
        payload = result.model_dump(mode="json") if result is not None else {"success": False}
        yield _sse_frame("done", payload)
    except Exception as exc:
        yield _sse_frame("error", {"error": str(exc)})


async def runs_endpoint(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be JSON"}, status_code=400)

    goal_text = (body or {}).get("goal")
    if not goal_text or not isinstance(goal_text, str):
        return JSONResponse({"error": "'goal' (string) is required"}, status_code=400)

    role_name = body.get("role", "assistant")
    overrides: dict[str, Any] = {}
    if body.get("model"):
        overrides["model"] = body["model"]
    if body.get("max_turns"):
        overrides["max_turns"] = body["max_turns"]

    control_plane: ControlPlane = request.app.state.control_plane
    try:
        agent_spec = control_plane.make_spec(role_name, **overrides)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    agent = Agent(agent_spec, control_plane.runtime)
    goal = Goal(description=goal_text)
    return StreamingResponse(
        _agent_event_stream(agent, goal),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def playground_endpoint(request: Request) -> HTMLResponse:
    return HTMLResponse(PLAYGROUND_HTML)


def create_app(cfg: ControlPlaneConfig | None = None) -> Starlette:
    """Build the ASGI app. `cfg` is a regular `ControlPlaneConfig` — same
    object the CLI and `AgentBuilder` construct — defaulting to a bare
    `ControlPlaneConfig()` (callers almost always want to pass their own
    api_key/model/tools/hooks)."""
    control_plane = ControlPlane(cfg if cfg is not None else ControlPlaneConfig())

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await control_plane.start()
        app.state.control_plane = control_plane
        try:
            yield
        finally:
            await control_plane.stop()

    return Starlette(
        routes=[
            Route("/runs", runs_endpoint, methods=["POST"]),
            Route("/health", health_endpoint, methods=["GET"]),
            Route("/playground", playground_endpoint, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
