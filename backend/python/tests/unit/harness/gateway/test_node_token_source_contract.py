"""QA19-4 (MGW-G2): the egress proxy's production ``TokenSource``, ``NodeGatewayTokenSource``, runs the
``TokenSourceContract`` against a live Node: the real internal router, gateway-token authority and lease check
(``journal-http-server.ts``). Each sandbox is bound to a run attempt Node really leased.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from app.harness.gateway.tokens import NodeGatewayTokens, NodeGatewayTokenSource
from app.harness.sandbox.binding_proof import BindingAttester
from harness_core.clock import SystemClock
from harness_core.sandbox.egress.errors import TokenUnavailable
from harness_core.testing.sandbox.egress_contracts import (
    TokenSourceContract,
    contract_binding,
)
from tests.unit.harness.gateway.binding_keys import TEST_ATTESTER
from tests.unit.harness.test_node_journal_contract import (
    NodeHarness,
    _Credential,  # pyright: ignore[reportPrivateUsage]
    node_server,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.sandbox.egress.types import EgressBinding
    from tests.unit.harness.test_node_journal_contract import (
        _NodeServer,  # pyright: ignore[reportPrivateUsage]
    )

ORG = "org-contract"


class _LeasedSandboxes:
    """Sandboxes bound to run attempts that hold Node's lease, as the broker labels them."""

    def __init__(self, server: _NodeServer) -> None:
        self.harness = NodeHarness(server)
        self.tokens = NodeGatewayTokens(base_url=server.base_url, credentials=_Credential(server.proxy_credential))
        self._scope = uuid.uuid4().hex[:8]
        self._attempts: dict[str, tuple[str, str]] = {}

    async def lease(self, sandbox_id: str) -> None:
        run_id = f"run-{self._scope}-{sandbox_id}"
        self.harness.admit(run_id, session_id=f"session-{run_id}")
        claim = await self.harness.journal.claim(run_id, worker_id="worker-contract")
        assert claim is not None
        self._attempts[sandbox_id] = (run_id, claim.attempt_id)

    def binding(self, sandbox_id: str, index: int) -> EgressBinding:
        run_id, attempt_id = self._attempts[sandbox_id]
        unbound = contract_binding(sandbox_id, index)
        labels = {**unbound.labels, "orgId": ORG, "runId": run_id, "attemptId": attempt_id}
        # As the broker registers it: signed for the attempt its labels name (R38IDENT-FU-7).
        return TEST_ATTESTER.attest(replace(unbound, labels=labels))


@pytest.fixture
async def sandboxes(node_server: _NodeServer) -> _LeasedSandboxes:
    leased = _LeasedSandboxes(node_server)
    for sandbox_id in ("sbx-1", "sbx-2"):
        await leased.lease(sandbox_id)
    return leased


class TestNodeGatewayTokenSource(TokenSourceContract):
    @pytest.fixture
    def token_clock(self) -> SystemClock:
        # The route authority signs on wall time (C-13's gateways verify it so); the contract brackets the call.
        return SystemClock()

    @pytest.fixture
    def token_source(self, sandboxes: _LeasedSandboxes) -> NodeGatewayTokenSource:
        return NodeGatewayTokenSource(sandboxes.tokens)

    @pytest.fixture
    def token_binding(self, sandboxes: _LeasedSandboxes) -> Callable[[str, int], EgressBinding]:
        return sandboxes.binding


async def test_qa19_4_an_attempt_that_lost_its_lease_gets_no_token(sandboxes: _LeasedSandboxes) -> None:
    source = NodeGatewayTokenSource(sandboxes.tokens)
    binding = sandboxes.binding("sbx-1", 1)
    sandboxes.harness.advance(sandboxes.harness.lease_s + 1)
    with pytest.raises(TokenUnavailable):
        await source.issue(binding.policy.token_routes[0], binding)


async def test_fu7_a_binding_the_broker_did_not_sign_gets_no_token(sandboxes: _LeasedSandboxes) -> None:
    """The proxy's credential names no run by itself: unsigned, signed under another key, or signed for another
    attempt of a leased run, Node refuses."""
    source = NodeGatewayTokenSource(sandboxes.tokens)
    genuine = sandboxes.binding("sbx-1", 1)
    route = genuine.policy.token_routes[0]
    other = sandboxes.binding("sbx-2", 2)
    for forged in (
        replace(genuine, attestation=None),
        BindingAttester(bytes([9]) * 32).attest(genuine),
        # sbx-2's proof, presented for sbx-1's attempt: both attempts hold Node's lease.
        replace(genuine, attestation=other.attestation),
    ):
        with pytest.raises(TokenUnavailable):
            await source.issue(route, forged)
    assert (await source.issue(route, genuine)).value


__all__ = ["node_server"]
