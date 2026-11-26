"""SEC1 EC-1..EC-4: the agent cannot rewrite the configuration its engine obeys (real binaries, real broker).

Amp and Copilot (CVE-2025-53773) were steered into editing their own settings to approve tools or add
servers. Here each pinned engine is opened by its real adapter in its real image under the Docker broker
(runc), then the sandbox user, which is what the engine's shell and tools run as, tries to overwrite,
delete, rename and replace every file the engine reads its configuration from, and to plant the
user-scope settings the engine would also read. The engine must still be alive and writing its own state.

Each sandbox reaches a ready tool gateway through the egress proxy (``mcp_startup_lab``): an engine opens only once
its gateway is connected (E2E3-F6), and what is attacked here is the configuration of an engine that came up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.controller import EngineControllerConfig
from harness_core.engines.types import EngineMode, EngineProfile
from harness_core.gateway.types import RunAttemptBinding
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
)
from harness_core.skills.materializer import skills_mount
from harness_core.testing.engines.contract import ACCESS
from harness_core.testing.sandbox.contract import run
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.codex.adapter import CodexAdapter
from harness_engines.opencode.adapter import OpencodeAdapter
from harness_engines.release import release_allowlist
from tests.unit.harness.engines.mcp_startup_lab import (
    MODEL_PREFIX,
    TOOLS_PREFIX,
    LateGateway,
)
from tests.unit.harness.engines.real_stack import OWNER, engine_broker
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_broker import (
        DockerSandboxBroker,
        DockerSandboxHandle,
    )
    from harness_core.engines.ports import EngineAdapter
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

LEASE = LeaseRef("worker-sec1", 1)
CONFIG = EngineControllerConfig(images={})
SKILLS_DIR = "/skills"


@dataclass(frozen=True, slots=True)
class Engine:
    kind: str
    adapter: type[EngineAdapter]
    # The engine's home under the state mount, and the files there it reads its configuration from.
    home: str
    obeyed: tuple[str, ...]
    # Files the engine would also read from its home if they existed (planted by the agent).
    plantable: tuple[str, ...]


ENGINES = [
    pytest.param(
        # Codex reads its managed configuration from /etc/codex, on the read-only root.
        Engine("codex", CodexAdapter, "/state/codex", ("config.toml", "AGENTS.md", "AGENTS.override.md"), ()),
        id="codex",
    ),
    pytest.param(
        Engine(
            "claude_code",
            ClaudeCodeAdapter,
            "/state/claude",
            ("harness-settings.json", "harness-mcp.json", "settings.json", "CLAUDE.md", "skills"),
            # Local settings are read only from a project's .claude/ (hidden, P5.5; and not a setting source).
            (),
        ),
        id="claude_code",
    ),
    # opencode's configuration is its environment and a read-only folder of the image: nothing in /state.
    pytest.param(Engine("opencode", OpencodeAdapter, "/state", (), ()), id="opencode"),
]


def _release(kind: str):  # noqa: ANN202
    return next(r for r in release_allowlist() if r.version.kind == kind)


@pytest.fixture(params=ENGINES)
def engine(request: pytest.FixtureRequest) -> Engine:
    chosen: Engine = request.param
    try:
        import docker

        client = docker.from_env()
        client.images.get(_release(chosen.kind).image)
        client.close()
    except Exception as exc:
        pytest.skip(f"needs Docker and the {chosen.kind} image: {type(exc).__name__}")
    return chosen


@pytest.fixture
async def broker(stack: RealStack, engine: Engine) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.sec1test.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-sec1-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=_release(engine.kind).image)
    try:
        yield created
    finally:
        await created.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
        provider.close()


PLACEHOLDERS = ("phm_" + "a" * 32, "pht_" + "b" * 32)


async def _sandbox(broker: DockerSandboxBroker, engine: Engine, stack: RealStack, base: str) -> DockerSandboxHandle:
    run_id = f"run-sec1-{uuid.uuid4().hex[:8]}"
    stack.models.bind(run_id, "att-sec1", RunAttemptBinding("tenant", "scope", run_id, None, "att-sec1", 1, "", ""))
    spec = SandboxSpec(
        image_digest=_release(engine.kind).image,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=0.5, memory_mb=1024, pids=256, scratch_mb=64),
        egress=EgressPolicy(
            destinations=(
                EgressDestination("model-gateway", (8097,), upstream=base + MODEL_PREFIX),
                EgressDestination("tool-gateway", (8097,), upstream=base + TOOLS_PREFIX),
            ),
            token_routes=(
                TokenRoute(PLACEHOLDERS[0], "model-gateway", "gateway:model"),
                TokenRoute(PLACEHOLDERS[1], "tool-gateway", "gateway:tools"),
            ),
        ),
        mounts=(MountSpec("/workspace"), MountSpec(CONFIG.state_dir), skills_mount(SKILLS_DIR)),
        labels={
            **OWNER,
            "pipeshub.agent": "SEC1",
            "orgId": "tenant",
            "sessionId": "scope",
            "runId": run_id,
            "attemptId": "att-sec1",
        },
    )
    return await broker.acquire(spec, lease=LEASE, idempotency_key=f"sec1-{uuid.uuid4().hex}")


async def _sh(handle: DockerSandboxHandle, script: str) -> tuple[int, str]:
    out, err, code = await run(await handle.exec(["/bin/sh", "-c", script], env={}), timeout_s=60)
    return code, (out + err).decode("utf-8", "replace")


def _attacks(engine: Engine) -> list[str]:
    home, attacks = engine.home, []
    for name in engine.obeyed:
        path = f"{home}/{name}"
        attacks += [
            f"echo '# agent' >> {path}",
            f"printf '{{}}' > {path}",
            f"rm -f {path} && test ! -e {path}",
            f"mv {path} {path}.moved",
            f"ln -sf /tmp/evil {path}.tmp && mv -f {path}.tmp {path}",
        ]
    for name in engine.plantable:
        attacks.append(f"printf 'planted' > {home}/{name} && test -s {home}/{name}")
    if engine.home != "/state":
        attacks += [f"mv {home} /state/moved-home", f"mv {home} /workspace/moved-home"]
    return attacks


async def _open(engine: Engine, handle: DockerSandboxHandle):  # noqa: ANN202
    release = _release(engine.kind)
    adapter = engine.adapter()
    profile = EngineProfile(engine=release.version, mode=EngineMode.BUILD, access=ACCESS)
    endpoints = CONFIG.endpoints(*PLACEHOLDERS, next(iter(adapter.capabilities().model_apis)))
    launch = adapter.render_launch(profile, endpoints, SKILLS_DIR)
    return launch, await adapter.open(handle, launch, None)


async def test_ec1_to_ec3_the_sandbox_user_cannot_change_what_the_engine_obeys(
    engine: Engine, broker: DockerSandboxBroker, stack: RealStack
) -> None:
    async with LateGateway().serving(stack.host_ip) as base:
        await _attacked(engine, broker, await _sandbox(broker, engine, stack, base))


async def _attacked(engine: Engine, broker: DockerSandboxBroker, handle: DockerSandboxHandle) -> None:
    session = None
    try:
        launch, session = await _open(engine, handle)
        obeyed = {f"{engine.home}/{name}" for name in engine.obeyed} | set(launch.files)
        before = {path: (await _sh(handle, f"sha256sum {path}"))[1] for path in sorted(obeyed)}
        succeeded = []
        for attack in _attacks(engine):
            code, _ = await _sh(handle, attack)
            if code == 0:
                succeeded.append(attack)
        after = {path: (await _sh(handle, f"sha256sum {path}"))[1] for path in sorted(obeyed)}
        assert succeeded == [], f"the sandbox user changed the engine's configuration: {succeeded}"
        assert after == before
        # EC-4: the engine is still running and writing its own state beside its configuration.
        assert (await session.health()).alive
        code, listing = await _sh(handle, f"ls -A {engine.home}")
        assert code == 0 and listing.strip()
        if engine.kind == "opencode":
            code, _ = await _sh(handle, "mkdir -p /opt/oc-xdg-config/opencode && echo '{}' > /opt/oc-xdg-config/opencode/opencode.json")
            assert code != 0
    finally:
        if session is not None:
            await session.close()
        await broker.release(handle)
