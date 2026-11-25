"""BC, TC and RC contracts on every adapter of the egress proxy's ports."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    StaticResolver,
    SystemResolver,
)
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.sandbox.egress_contracts import (
    EgressBindingsContract,
    EgressRevocationsContract,
    EgressStateStoreContract,
    HostResolverContract,
    TokenSourceContract,
    contract_binding,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


class TestInMemoryEgressBindings(EgressBindingsContract, EgressRevocationsContract):
    @pytest.fixture
    def egress_bindings(self) -> InMemoryEgressBindings:
        return InMemoryEgressBindings()


class TestDurableEgressBindings(EgressBindingsContract, EgressRevocationsContract):
    @pytest.fixture
    def egress_bindings(self) -> DurableEgressBindings:
        clock = ManualClock()
        return DurableEgressBindings(InMemoryEgressStateStore(clock), StateSealer(b"contract-key"), clock)


class TestInMemoryEgressStateStore(EgressStateStoreContract):
    @pytest.fixture
    def state_clock(self) -> ManualClock:
        return ManualClock()

    @pytest.fixture
    def state_store(self, state_clock: ManualClock) -> InMemoryEgressStateStore:
        return InMemoryEgressStateStore(state_clock)

    @pytest.fixture
    def state_elapse(self, state_clock: ManualClock) -> Callable[[float], Awaitable[None]]:
        async def elapse(seconds: float) -> None:
            _ = state_clock.advance(seconds)

        return elapse


class TestRemoteEgressBindings(EgressBindingsContract, EgressRevocationsContract):
    @pytest.fixture
    async def egress_bindings(self) -> AsyncIterator[RemoteEgressBindings]:
        clock = ManualClock()
        proxy = EgressProxy(
            bindings=InMemoryEgressBindings(),
            tokens=InMemoryTokenSource(b"k", clock),
            resolver=StaticResolver(),
            upstreams={},
            clock=clock,
            ids=SeededIds(),
        )
        server = EgressServer(proxy, StaticBearerAuth("c"), instance="t")
        _, control_port = await server.start(host="127.0.0.1", data_port=0, control_port=0)
        client = RemoteEgressBindings(f"http://127.0.0.1:{control_port}", StaticBearerAuth("c"))
        yield client
        await client.aclose()
        await server.stop()


class TestInMemoryTokenSource(TokenSourceContract):
    @pytest.fixture
    def token_clock(self) -> ManualClock:
        return ManualClock()

    @pytest.fixture
    def token_source(self, token_clock: ManualClock) -> InMemoryTokenSource:
        return InMemoryTokenSource(b"contract-secret", token_clock)

    async def test_tc1b_tokens_also_differ_per_credential_ref(self, token_source: InMemoryTokenSource) -> None:
        binding = contract_binding("sbx-1", 1)
        model, tools = binding.policy.token_routes
        assert (await token_source.issue(model, binding)).value != (await token_source.issue(tools, binding)).value


class TestRoutedTokenSource(TokenSourceContract):
    """WP-06: the proxy's source; run-bound routes go to the authority unchanged, sealed ones open in place."""

    @pytest.fixture
    def token_clock(self) -> ManualClock:
        return ManualClock()

    @pytest.fixture
    def token_source(self, token_clock: ManualClock) -> RoutedTokenSource:
        sealed = SealedTokenSource(CredentialSealer(b"contract-sealing"), token_clock)
        return RoutedTokenSource(InMemoryTokenSource(b"contract-secret", token_clock), sealed)


class TestStaticResolver(HostResolverContract):
    @pytest.fixture
    def host_resolver(self) -> StaticResolver:
        return StaticResolver({"gateway.internal": ["10.0.0.1"]})

    @pytest.fixture
    def resolvable_host(self) -> str:
        return "gateway.internal"


class TestSystemResolver(HostResolverContract):
    @pytest.fixture
    def host_resolver(self) -> SystemResolver:
        return SystemResolver()

    @pytest.fixture
    def resolvable_host(self) -> str:
        return "localhost"
