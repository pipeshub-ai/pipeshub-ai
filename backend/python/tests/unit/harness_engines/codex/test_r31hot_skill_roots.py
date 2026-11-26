"""R31-HOT: a Codex job is offered PipesHub's skills whatever the workspace's trust (CF-02).

The question was whether the PDF skill stopped reaching Codex jobs after R30-ENG made ``/workspace`` untrusted. Codex
0.160 gates only its project config layer on trust (``ConfigLayerSource::Project``); the roots a client sets with
``skills/extraRoots/set`` are user-scope roots with no trust check (``ext/skills/src/host_roots.rs``). So the adapter's
own call is what puts the mounted skills in front of the engine: it must be made, on every open, with the mounted
folder, under the untrusted project and with skill instructions on. No test held that before; the real-binary
check (``test_real_codex_conformance.py`` RXC-5) needs a lane.
"""

from __future__ import annotations

import asyncio
import json
import tomllib
from typing import Any

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.types import EngineMode, EngineProfile, GatewayEndpoints
from harness_core.sandbox import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.binary import sha256sum_program
from harness_core.testing.sandbox.fake import (
    FAKE_PROGRAMS,
    FakeProcessContext,
    FakeSandboxBroker,
)
from harness_engines.codex.adapter import CodexAdapter
from harness_engines.codex.launch import CONFIG_PATH, SKILLS_ENV, WORKDIR
from harness_engines.release import release_allowlist

CODEX = next(r.version for r in release_allowlist() if r.version.kind == "codex")
SKILLS = "/skills"


def _app_server(seen: list[tuple[str, Any]]) -> Any:  # noqa: ANN401 - a fake program
    """codex-app-server as far as ``open`` talks to it: answers every request, records what it was sent."""

    async def program(context: FakeProcessContext) -> int:
        buffered = b""
        while True:
            chunk = await context.read_stdin()
            if not chunk:
                return 0
            buffered += chunk
            while b"\n" in buffered:
                line, buffered = buffered.split(b"\n", 1)
                message = json.loads(line)
                seen.append((message["method"], message.get("params")))
                if "id" not in message:
                    continue
                result: dict[str, Any] = (
                    {"userAgent": f"harness/{CODEX.version} (Ubuntu 24.4.0; x86_64)"}
                    if message["method"] == "initialize"
                    else {}
                )
                await context.emit(json.dumps({"id": message["id"], "result": result}).encode() + b"\n")

    return program


async def test_r31hot_s1_codex_is_given_the_mounted_skills_as_its_own_root_under_an_untrusted_workspace() -> None:
    broker = FakeSandboxBroker(clock=ManualClock(), modes=frozenset(RuntimeMode), programs=dict(FAKE_PROGRAMS))
    spec = SandboxSpec(
        image_digest="codex@sha256:" + "0" * 64,
        mode=RuntimeMode.DOCKER_HARDENED,
        mounts=(MountSpec("/workspace"), MountSpec("/state"), MountSpec(SKILLS)),
    )
    sandbox = await broker.acquire(spec, lease=LeaseRef(holder="s1", fence=1), idempotency_key="s1")
    endpoints = GatewayEndpoints("http://m/v1", ModelApi.RESPONSES, "http://t/mcp", "p" * 16)
    profile = EngineProfile(engine=CODEX, mode=EngineMode.BUILD, access=AccessRouteRef("route-1", "d1"))
    launch = CodexAdapter().render_launch(profile, endpoints, SKILLS)
    config = tomllib.loads(launch.files[CONFIG_PATH].decode())
    assert config["projects"][WORKDIR]["trust_level"] == "untrusted"
    assert config["skills"]["include_instructions"] is True
    assert config["skills"]["bundled"]["enabled"] is False, "only PipesHub's skills, never Codex's bundled ones"
    assert launch.env[SKILLS_ENV] == SKILLS

    seen: list[tuple[str, Any]] = []
    sandbox.install("sha256sum", sha256sum_program(lambda _path: CODEX.binary_digest))
    sandbox.install(launch.argv[0], _app_server(seen))
    session = await CodexAdapter().open(sandbox, launch, None)
    try:
        methods = [method for method, _ in seen]
        assert ("skills/extraRoots/set", {"extraRoots": [SKILLS]}) in seen, seen
        assert methods.index("initialized") < methods.index("skills/extraRoots/set"), methods
    finally:
        await asyncio.wait_for(session.close(), timeout=5)
