"""WP100-FU-7: a host a sandbox reaches only as one repository (its git rule) is inspected where the proxy ends TLS.

git clones through ``HTTPS_PROXY`` with a CONNECT. The proxy intercepted a CONNECT only for an allowed website or a
credentialed host, so a host reached only through a repository's git rule was refused, or, with opaque tunnels on,
got one no repository rule can hold. Now such a host is always inspected, never an opaque tunnel.
"""

from __future__ import annotations

import pytest

from harness_core.sandbox.egress import (
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    ProxyRequest,
)
from harness_core.sandbox.egress.resolver import StaticResolver
from harness_core.sandbox.egress.types import EgressBinding
from harness_core.sandbox.types import EgressDestination, EgressPolicy, GitAccess
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds


async def _git_ruled_proxy(*, intercepts: bool, tunnels: bool) -> tuple[EgressProxy, EgressBinding]:
    """The proxy as a sandbox whose only way to github.com is one repository's git rule meets it."""
    policy = EgressPolicy(destinations=(EgressDestination("github.com", (443,), git=GitAccess("acme/widgets")),))
    binding = EgressBinding("sbx-git", "10.213.0.2", "10.213.0.3", policy, {})
    bindings = InMemoryEgressBindings()
    await bindings.register(binding)
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=bindings, tokens=InMemoryTokenSource(b"fu7", clock, ttl_s=600),
        resolver=StaticResolver({"github.com": ["140.82.112.3"]}), upstreams={}, clock=clock, ids=SeededIds(),
        limits=ProxyLimits(intercepts=intercepts, connect_tunnels=tunnels),
    )
    return proxy, binding


async def test_wp100_fu7_a_git_ruled_hosts_connect_is_inspected_so_its_rule_holds() -> None:
    proxy, binding = await _git_ruled_proxy(intercepts=True, tunnels=False)
    tunnel = await proxy.authorize_tunnel(
        ProxyRequest("CONNECT", "github.com:443", (("Host", "github.com:443"),)),
        local=binding.proxy_address, peer=binding.sandbox_address,
    )
    assert tunnel.intercept

    def inside(path: str) -> ProxyRequest:
        return ProxyRequest("GET", path, (("Host", "github.com"),), b"")

    forward = await proxy.authorize_intercepted(
        inside("/acme/widgets.git/info/refs?service=git-upload-pack"), host="github.com", port=443,
        local=binding.proxy_address, peer=binding.sandbox_address,
    )
    assert forward is not None
    with pytest.raises(EgressRefused) as refused:
        _ = await proxy.authorize_intercepted(
            inside("/acme/other.git/info/refs?service=git-upload-pack"), host="github.com", port=443,
            local=binding.proxy_address, peer=binding.sandbox_address,
        )
    assert refused.value.reason is not EgressRefusalReason.TUNNEL_REFUSED


async def test_wp100_fu7_a_git_ruled_host_is_never_an_opaque_tunnel() -> None:
    """Opaque bytes cannot be held to one repository: without TLS ended here, the git host is refused even where the
    deployment offers tunnels."""
    proxy, binding = await _git_ruled_proxy(intercepts=False, tunnels=True)
    with pytest.raises(EgressRefused) as refused:
        _ = await proxy.authorize_tunnel(
            ProxyRequest("CONNECT", "github.com:443", (("Host", "github.com:443"),)),
            local=binding.proxy_address, peer=binding.sandbox_address,
        )
    assert refused.value.reason is EgressRefusalReason.TUNNEL_REFUSED
