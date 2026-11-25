"""CA-1..CA-3 (E8): what the Codex adapter declares, and a grant it cannot carry refused before anything starts."""

from __future__ import annotations

import dataclasses

import pytest

from harness_core.access.types import ModelApi
from harness_core.engines.errors import GrantTooLarge, ToolNameTooLong
from harness_core.engines.policy import check_grant_size
from harness_core.engines.types import GatewayEndpoints
from harness_core.testing.engines.endpoint_gateway import EndpointConformanceGateway
from harness_core.testing.engines.gateway import GrantedTool
from harness_engines.codex.adapter import (
    MAX_MCP_TOOLS,
    MAX_TOOL_NAME,
    CodexAdapter,
    reported_version,
)
from harness_engines.codex.launch import exposed_tool_name
from harness_engines.release import release_allowlist
from tests.unit.harness_core.engines.test_controller import (  # pyright: ignore[reportPrivateUsage]
    IMAGE,
    World,
    _refused,
)

CODEX = next(r.version for r in release_allowlist() if r.version.kind == "codex")


def test_ca1_capabilities() -> None:
    capabilities = CodexAdapter().capabilities()
    assert (capabilities.can_steer, capabilities.native_skills, capabilities.sub_agents) == (True, True, True)
    assert capabilities.model_apis == frozenset({ModelApi.RESPONSES})
    assert (capabilities.max_tools, capabilities.drive) == (2048, "stdio")


def test_ca2_the_grant_size_codex_carries_and_the_one_it_drops() -> None:
    capabilities = CodexAdapter().capabilities()
    check_grant_size(capabilities, MAX_MCP_TOOLS)
    with pytest.raises(GrantTooLarge) as refused:
        check_grant_size(capabilities, MAX_MCP_TOOLS + 1)
    assert (refused.value.tools, refused.value.max_tools, refused.value.reason_code) == (2049, 2048, "grant_too_large")


async def test_ca2_the_controller_refuses_it_before_acquiring(caplog: pytest.LogCaptureFixture) -> None:
    world = World(adapter=CodexAdapter(), images={CODEX: IMAGE}, versions=(CODEX,))  # type: ignore[arg-type]
    run = world.run(grant_size=MAX_MCP_TOOLS + 1)
    run = dataclasses.replace(run, profile=dataclasses.replace(run.profile, engine=CODEX))
    await _refused(world, run, GrantTooLarge, caplog)


@pytest.mark.parametrize(
    ("user_agent", "version"),
    [
        ("harness/0.160.0 (Ubuntu 24.4.0; x86_64) unknown (harness; 1)", "0.160.0"),
        ("harness/0.162.0-alpha.10 (Alpine)", "0.162.0-alpha.10"),
        ("no version here", ""),
    ],
)
def test_the_binary_reports_its_version_in_the_user_agent(user_agent: str, version: str) -> None:
    assert reported_version(user_agent) == version


def test_ca3_a_granted_tool_name_codex_cannot_carry_is_refused_at_the_grant() -> None:
    """FB-14: E3b refuses a flat name over 64 characters and Codex names gateway tools ``mcp__tools__<tool>``."""
    capabilities = CodexAdapter().capabilities()
    assert capabilities.max_tool_name == MAX_TOOL_NAME == 52
    assert len(exposed_tool_name("t" * 52)) == 64
    endpoints = GatewayEndpoints("http://m/v1", ModelApi.RESPONSES, "http://t/mcp", "p" * 16)
    EndpointConformanceGateway(endpoints=endpoints, grant=(GrantedTool("t" * 52),), capabilities=capabilities)
    with pytest.raises(ToolNameTooLong) as refused:
        EndpointConformanceGateway(endpoints=endpoints, grant=(GrantedTool("t" * 53),), capabilities=capabilities)
    assert (refused.value.reason_code, refused.value.longest, refused.value.max_length) == ("tool_name_too_long", 53, 52)


def test_h1_eg5_the_end_of_the_app_servers_stream_is_a_crash() -> None:
    from harness_core.engines.errors import EngineCrashed
    from harness_engines.codex.rpc import CodexStreamEnded

    assert issubclass(CodexStreamEnded, EngineCrashed)


async def test_h1_eg5_an_app_server_that_never_answers_initialize_is_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from harness_core.access.types import AccessRouteRef
    from harness_core.engines.errors import EngineUnhealthy
    from harness_core.engines.types import EngineMode, EngineProfile
    from harness_core.sandbox import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
    from harness_core.testing.clock import ManualClock
    from harness_core.testing.engines.binary import sha256sum_program
    from harness_core.testing.sandbox.fake import (
        FAKE_PROGRAMS,
        FakeProcessContext,
        FakeSandboxBroker,
    )
    from harness_engines.codex import adapter as codex_adapter

    monkeypatch.setattr(codex_adapter, "INITIALIZE_TIMEOUT_S", 0.05)

    async def silent(context: FakeProcessContext) -> int:
        await asyncio.Event().wait()
        return 0

    broker = FakeSandboxBroker(clock=ManualClock(), modes=frozenset(RuntimeMode), programs=dict(FAKE_PROGRAMS))
    spec = SandboxSpec(
        image_digest="codex@sha256:" + "0" * 64,
        mode=RuntimeMode.DOCKER_HARDENED,
        mounts=(MountSpec("/workspace"), MountSpec("/state")),
    )
    sandbox = await broker.acquire(spec, lease=LeaseRef(holder="h1", fence=1), idempotency_key="h1")
    endpoints = GatewayEndpoints("http://m/v1", ModelApi.RESPONSES, "http://t/mcp", "p" * 16)
    profile = EngineProfile(engine=CODEX, mode=EngineMode.BUILD, access=AccessRouteRef("route-1", "d1"))
    launch = CodexAdapter().render_launch(profile, endpoints, "/skills")
    sandbox.install("sha256sum", sha256sum_program(lambda _path: CODEX.binary_digest))
    sandbox.install(launch.argv[0], silent)
    with pytest.raises(EngineUnhealthy) as gone:
        await CodexAdapter().open(sandbox, launch, None)
    assert gone.value.reason_code == "engine_unhealthy"
    assert isinstance(gone.value.__cause__, TimeoutError)
