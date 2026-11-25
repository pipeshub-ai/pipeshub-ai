"""E2E3-F6: Codex's first model request carries the tool gateway's tools however late the gateway answers.

The E2E3 matrix cell ``test_sb_knowledge_then_code_then_an_artifact[codex]`` once completed with no tool call under
heavy host load: Codex 0.160 waits about a second for an MCP server that is not ``required`` and then sends the
turn's first model request without its tools (``startup_timeout_sec`` notwithstanding), so the scripted model found
no gateway tool and answered ``tool unavailable``. The worker's tool gateway had answered ``initialize`` 3 s late.

Here the pinned binary (copied out of the engine's release image) runs against a model double and a tool gateway
that answers ``initialize`` after ``LATE_S``, with the config ``render_launch`` writes. The control pins the vendor
behaviour the fix rests on: rendered without ``required``, the same late gateway leaves every tool out. A resumed
thread (``thread/resume`` in a fresh app server, as a later attempt opens it) waits for the gateway the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess
import tomllib
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.types import EngineMode, EngineProfile, GatewayEndpoints
from harness_engines.codex.launch import (
    CONFIG_PATH,
    MCP_SERVER,
    TOKEN_ENV,
    TOOLS_TOKEN_ENV,
    exposed_tool_name,
    render_launch,
    render_toml,
    thread_settings,
)
from tests.unit.harness.engines.lane_releases import lane_release

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.docker, pytest.mark.timeout(180, method="thread")]

# Past Codex's own grace for a server that is not required (about 1 s on 0.160), well inside the startup timeout.
LATE_S = 3.0
TOOL = "knowledgegraph__search"
BINARY_IN_IMAGE = "/usr/local/bin/codex-app-server"


@pytest.fixture(scope="module")
def codex_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The release image's binary, copied out through a stopped ``pxh-`` container that is removed whatever happens."""
    image = lane_release("codex").image
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=False).returncode != 0:
        pytest.skip(f"{image} is not built on this host")
    target = tmp_path_factory.mktemp("codex-bin") / "codex-app-server"
    name = f"pxh-e2e3f6-{uuid.uuid4().hex[:8]}"
    try:
        subprocess.run(["docker", "create", "--name", name, image], capture_output=True, check=True)
        subprocess.run(["docker", "cp", f"{name}:{BINARY_IN_IMAGE}", str(target)], capture_output=True, check=True)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    return target


class _Unopened(Exception):
    def __init__(self, method: str, model_requests: int) -> None:
        super().__init__(f"Codex refused {method}")
        self.method = method
        self.model_requests = model_requests


class _Doubles:
    """The model double (Responses SSE, one text answer) and a tool gateway whose ``initialize`` answers late."""

    def __init__(self, late_s: float, *, refuse: bool = False) -> None:
        self.late_s = late_s
        # A gateway that never comes up: what the egress proxy answers while nothing listens behind it.
        self.refuse = refuse
        self.requested_tools: list[list[str]] = []

    @property
    def model_requests(self) -> int:
        return len(self.requested_tools)

    async def model(self, request: web.Request) -> web.StreamResponse:
        body: dict[str, Any] = await request.json()
        names: list[str] = []
        for tool in body.get("tools", []):
            if tool.get("type") == "namespace":
                names += [f"{tool['name']}__{inner['name']}" for inner in tool.get("tools", [])]
            else:
                names.append(str(tool.get("name") or tool.get("type")))
        self.requested_tools.append(names)
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        message = {"type": "message", "role": "assistant", "id": "m1", "content": [{"type": "output_text", "text": "ok"}]}
        usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
        for event in (
            {"type": "response.created", "response": {"id": "r1"}},
            {"type": "response.output_item.done", "item": message},
            {"type": "response.completed", "response": {"id": "r1", "usage": usage}},
        ):
            await response.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
        await response.write_eof()
        return response

    async def tools(self, request: web.Request) -> web.Response:
        if self.refuse:
            return web.Response(status=503)
        if request.method != "POST":
            return web.Response(status=405)
        message: dict[str, Any] = await request.json()
        if "id" not in message:
            return web.Response(status=202)
        result: dict[str, Any] = {}
        if message["method"] == "initialize":
            await asyncio.sleep(self.late_s)
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": MCP_SERVER, "version": "1"},
            }
        elif message["method"] == "tools/list":
            result = {"tools": [{"name": TOOL, "description": "Search", "inputSchema": {"type": "object"}}]}
        return web.json_response({"jsonrpc": "2.0", "id": message["id"], "result": result})


async def _first_request_tools(
    binary: Path,
    home: Path,
    *,
    required: bool,
    late_s: float = LATE_S,
    resume: str | None = None,
    refuse: bool = False,
) -> tuple[str, list[str]]:
    """One turn on a fresh app server; ``resume``: on that thread (``thread/resume``), else a new one. The thread's id
    and the tools the turn's first model request carried; ``_Unopened`` if Codex would not open the thread."""
    doubles = _Doubles(late_s, refuse=refuse)
    app = web.Application()
    app.router.add_post("/v1/responses", doubles.model)
    app.router.add_route("*", "/mcp", doubles.tools)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port: int = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
    endpoints = GatewayEndpoints(
        model_base_url=f"http://127.0.0.1:{port}/v1",
        model_api=ModelApi.RESPONSES,
        tools_mcp_url=f"http://127.0.0.1:{port}/mcp",
        token_placeholder="pxh-model-placeholder-0123",
        tools_token_placeholder="pxh-tools-placeholder-4567",
    )
    release = lane_release("codex")
    launch = render_launch(
        EngineProfile(engine=release.version, mode=EngineMode.BUILD, access=AccessRouteRef("route-1", "d1")),
        endpoints,
        "",
    )
    config = tomllib.loads(launch.files[CONFIG_PATH].decode())
    if not required:
        config["mcp_servers"][MCP_SERVER].pop("required")
    (home / "config.toml").write_text(render_toml(config))
    work = home / "work"
    work.mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "CODEX_HOME": str(home),
        "HOME": str(home),
        TOKEN_ENV: endpoints.token_placeholder,
        TOOLS_TOKEN_ENV: endpoints.tools_placeholder,
    }
    process = await asyncio.create_subprocess_exec(
        str(binary), *launch.argv[1:], stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=env, cwd=str(work),
    )
    assert process.stdin is not None and process.stdout is not None
    answers: dict[int, asyncio.Future[dict[str, Any]]] = {}
    completed = asyncio.Event()

    async def read() -> None:
        assert process.stdout is not None
        async for line in process.stdout:
            message = json.loads(line)
            if "method" not in message and message.get("id") in answers:
                answers.pop(message["id"]).set_result(message)
            elif message.get("method") == "turn/completed":
                completed.set()

    reader = asyncio.ensure_future(read())

    async def request(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        answers[request_id] = asyncio.get_running_loop().create_future()
        assert process.stdin is not None
        process.stdin.write((json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode())
        await process.stdin.drain()
        answer = await asyncio.wait_for(answers[request_id], 60)
        if "error" in answer and method in ("thread/start", "thread/resume"):
            raise _Unopened(method, doubles.model_requests)
        assert "result" in answer, (method, answer)
        return answer["result"]

    try:
        await request(1, "initialize", {"clientInfo": {"name": "harness", "version": "1"}, "capabilities": {}})
        process.stdin.write(b'{"method":"initialized"}\n')
        settings = {**thread_settings(""), "cwd": str(work)}
        if resume is None:
            thread = await request(2, "thread/start", settings)
        else:
            # As ``CodexSession._open_thread`` resumes it.
            thread = await request(2, "thread/resume", {**settings, "threadId": resume, "excludeTurns": True})
        thread_id: str = thread["thread"]["id"]
        await request(3, "turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": "hi"}]})
        await asyncio.wait_for(completed.wait(), 60)
    finally:
        reader.cancel()
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        await runner.cleanup()
    assert doubles.requested_tools, "Codex made no model request"
    return thread_id, doubles.requested_tools[0]


async def test_e2e3_f6_a_late_tool_gateway_still_gives_codexs_first_request_its_tools(
    codex_binary: Path, tmp_path: Path
) -> None:
    _, tools = await _first_request_tools(codex_binary, tmp_path, required=True)
    assert exposed_tool_name(TOOL) in tools, tools


async def test_e2e3_f6_control_without_required_codex_sends_the_first_request_without_them(
    codex_binary: Path, tmp_path: Path
) -> None:
    _, tools = await _first_request_tools(codex_binary, tmp_path, required=False)
    assert exposed_tool_name(TOOL) not in tools, tools


async def _resumed_first_request_tools(binary: Path, home: Path, *, required: bool) -> list[str]:
    """A thread started against a prompt gateway, then resumed by a fresh app server whose gateway is late."""
    thread_id, _ = await _first_request_tools(binary, home, required=required, late_s=0.0)
    resumed, tools = await _first_request_tools(binary, home, required=required, resume=thread_id)
    assert resumed == thread_id, "Codex resumed another thread"
    return tools


async def test_e2e3_f6_a_resumed_thread_waits_for_a_late_tool_gateway_too(codex_binary: Path, tmp_path: Path) -> None:
    tools = await _resumed_first_request_tools(codex_binary, tmp_path, required=True)
    assert exposed_tool_name(TOOL) in tools, tools


async def test_e2e3_f6_control_without_required_a_resumed_thread_goes_without_them(
    codex_binary: Path, tmp_path: Path
) -> None:
    tools = await _resumed_first_request_tools(codex_binary, tmp_path, required=False)
    assert exposed_tool_name(TOOL) not in tools, tools


async def test_e2e3_f6_a_resumed_thread_whose_gateway_never_comes_up_is_never_opened(
    codex_binary: Path, tmp_path: Path
) -> None:
    """What ``CodexSession`` turns into ``EngineUnhealthy``: Codex refuses ``thread/resume`` and calls no model."""
    thread_id, _ = await _first_request_tools(codex_binary, tmp_path, required=True, late_s=0.0)
    with pytest.raises(_Unopened) as refused:
        await _first_request_tools(codex_binary, tmp_path, required=True, resume=thread_id, refuse=True)
    assert (refused.value.method, refused.value.model_requests) == ("thread/resume", 0)
