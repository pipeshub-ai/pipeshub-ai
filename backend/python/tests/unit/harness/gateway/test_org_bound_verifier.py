"""WP-118: a gateway request acts for the org its verified run token names (the Python half of ADR-SEC-02).

The Tool Gateway and the Model Gateway serve each request in a task of its own, outside the run's task, so nothing
bound there reaches them: the org is bound from the token once it verifies, and never from a refused one."""

from __future__ import annotations

import asyncio

import pytest

from app.core import org_context
from app.harness.gateway.org_binding import OrgBoundGatewayTokenVerifier
from harness_core.gateway import TokenRefused
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.tool_endpoint import BINDING
from harness_core.testing.gateway import TokenMint


def _verifier() -> tuple[OrgBoundGatewayTokenVerifier, TokenMint]:
    clock = ManualClock()
    mint = TokenMint(clock)
    return OrgBoundGatewayTokenVerifier(mint.source, clock), mint


async def test_a_verified_token_binds_its_run_org_for_the_rest_of_the_request() -> None:
    verifier, mint = _verifier()

    async def request() -> org_context.OrgContext:
        _ = await verifier.verify(mint.mint(BINDING))
        return org_context.current()

    bound = await asyncio.get_running_loop().create_task(request(), context=org_context.contextvars.Context())
    assert (bound.org_id, bound.kind, bound.source, bound.request_id) == (
        BINDING.tenant,
        "service",
        "gateway_token",
        BINDING.run_id,
    )
    assert org_context.bound_org_id() is None, "the binding stays in the request's task"


async def test_a_refused_token_binds_nothing() -> None:
    verifier, _ = _verifier()

    async def request() -> str | None:
        with pytest.raises(TokenRefused):
            _ = await verifier.verify("not-a-token")
        return org_context.bound_org_id()

    assert await org_context.spawn_detached(request()) is None


def test_both_gateways_verify_with_the_org_binding_verifier() -> None:
    """A gateway built on the bare verifier would serve its requests unbound."""
    import re
    from pathlib import Path

    app = Path(__file__).resolve().parents[4] / "app"
    bare = [
        p.relative_to(app.parent).as_posix()
        for p in app.rglob("*.py")
        if re.search(r"(?<!OrgBound)GatewayTokenVerifier\(", p.read_text(encoding="utf-8"))
    ]
    assert bare == []
    for entry in ("harness/engines/worker.py", "model_gateway_main.py"):
        assert "OrgBoundGatewayTokenVerifier(" in (app / entry).read_text(encoding="utf-8")


def test_the_docker_lanes_build_their_gateways_as_the_worker_does() -> None:
    """The real-engine lanes stand in for the worker and the model gateway; on the bare verifier their tool calls ran
    with no org bound, so every org-scoped read (knowledge search, storage) found nothing (WP-120)."""
    import re
    from pathlib import Path

    engines = Path(__file__).resolve().parents[1] / "engines"
    lanes = ("real_stack.py", "durable_stack.py", "matrix/worker.py")
    bare = [lane for lane in lanes if re.search(r"(?<!OrgBound)GatewayTokenVerifier\(", (engines / lane).read_text())]
    assert bare == []
