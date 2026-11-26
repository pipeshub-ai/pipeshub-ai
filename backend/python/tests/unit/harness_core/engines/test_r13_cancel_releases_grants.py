"""R13-SBX: a cancelled engine turn leaves nothing of WP-63's behind (WP-65 cancel x WP-63 sites, pushes, repositories).

WP-63 gives an engine's sandbox allowed websites (the proxy's authority and ``HTTPS_PROXY``), a read-only repository
folder, and grants the egress proxy holds per sandbox: a one-push grant and a released upload site. WP-65 ends a
turn that a cancel catches before its engine opens. Whatever wait the cancel catches, the sandbox goes, and with its
egress binding every grant the proxy held for it; a sandbox bound again for another turn starts with none.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    StaticResolver,
)
from harness_core.sandbox.egress.sealed import CredentialSealer, new_placeholder
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    GitAccess,
    SiteAccess,
    SiteMode,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from tests.unit.harness_core.engines.test_controller import task
from tests.unit.harness_core.engines.test_controller_signin import (
    OWN,
    FakeRelay,
    _adapter,
    _world,
)

if TYPE_CHECKING:
    import pytest

    from harness_core.sandbox.ports import SandboxHandle

SITE = EgressDestination("uploads.example.com", (443,), site=SiteAccess(SiteMode.READ_WRITE, held=True))
REPOS = ("/pipeshub/repos", 256)


async def test_r13_sbx_1_a_turn_cancelled_in_its_sign_in_wait_releases_its_sites_and_repository_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted: list[str] = []

    async def trust(sandbox: SandboxHandle) -> None:
        # The fake sandbox runs no scripts; the Docker lane installs the real bundle (WP-63, test_wp63_sites_docker).
        trusted.append(sandbox.id)

    monkeypatch.setattr("harness_core.engines.controller.install_site_trust", trust)
    relay = FakeRelay()
    world = _world(relay=relay, repos_mount=REPOS)
    adapter = _adapter(world)
    adapter.sign_in_waits = asyncio.Event()
    running = asyncio.ensure_future(world.controller.run_turn(world.run(own_login=OWN, sites=(SITE,)), task(0, "say done")))
    await asyncio.wait_for(adapter.reached["sign_in"].wait(), 5)
    [(spec, _)] = world.broker.acquired
    assert SITE in spec.egress.destinations
    assert any(m.target == REPOS[0] and m.read_only and m.size_mb == REPOS[1] for m in spec.mounts)
    [sandbox_id] = list(world.broker.sandboxes)
    assert trusted == [sandbox_id]
    assert world.proxy.bindings.get(sandbox_id) is not None

    assert await world.controller.cancel(world.run(), reason="cancel_requested") is True
    outcome = await asyncio.wait_for(running, 5)
    assert outcome.cancelled is True
    assert world.broker.sandboxes == {} and world.broker.released == [sandbox_id]
    assert world.proxy.bindings.get(sandbox_id) is None, "the binding, and with it every grant, is gone"
    assert relay.kept == {}, "a cancelled turn's signed-in sandbox is never kept"


GIT_HOST, REPO = "git.example.test", "acme/widgets"
POLICY = EgressPolicy(destinations=(EgressDestination(GIT_HOST, (443,), git=GitAccess(REPO)), SITE))
LABELS = {"orgId": "o1", "sessionId": "s1", "runId": "r1", "attemptId": "a1"}


async def _granted() -> tuple[EgressProxy, InMemoryEgressBindings, EgressBinding, str]:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    bound = EgressBinding("sbx-r13", "10.213.0.2", "10.213.0.3", POLICY, LABELS)
    await bindings.register(bound)
    proxy = EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(b"r13", clock, ttl_s=600),
        resolver=StaticResolver({}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(intercepts=True),
    )
    placeholder = new_placeholder()
    _ = proxy.push_grants.issue(
        bound, placeholder=placeholder, host=GIT_HOST, repository=REPO, branch="pipeshub/session-s1",
        credential_ref=CredentialSealer(b"r13").seal(placeholder, "u:push"),
    )
    proxy.site_grants.issue(bound, host=SITE.host, run_id="r1")
    assert proxy.push_grants.is_grant(placeholder) and proxy.site_grants.released(bound, SITE.host)
    return proxy, bindings, bound, placeholder


async def test_r13_sbx_2_a_released_sandbox_takes_its_push_and_upload_grants_with_it() -> None:
    proxy, bindings, bound, placeholder = await _granted()
    await bindings.unregister(bound.sandbox_id)
    assert not proxy.push_grants.is_grant(placeholder)
    assert not proxy.site_grants.released(bound, SITE.host)


async def test_r13_sbx_3_a_sandbox_bound_again_for_another_turn_starts_without_them() -> None:
    proxy, bindings, bound, placeholder = await _granted()
    await bindings.register(EgressBinding(bound.sandbox_id, bound.proxy_address, bound.sandbox_address, POLICY, {**LABELS, "runId": "r2"}))
    assert not proxy.push_grants.is_grant(placeholder)
    assert not proxy.site_grants.released(bound, SITE.host)
