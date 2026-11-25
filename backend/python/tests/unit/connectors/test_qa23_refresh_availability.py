"""QA-23 (CG-23): a credential stays available through a refresh whose write-back fails.

Providers that rotate refresh tokens (the MCP fake here; Slack's rotating tokens the same) honour the old refresh
token once. If the renewed tokens are not stored, the binding's only refresh token is spent: the next cycle is
refused ``invalid_grant`` and the user must connect again. The fixtures are ``test_binding_token_refresher.py``'s:
the provider at the wire, Node's binder semantics, the real vault and broker.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.connectors.core.base.token_service.token_refresher import (
    McpOAuthStrategy,
    TokenRefresher,
)
from app.control_plane.runtime.bindings import binding_version_digest
from app.control_plane.testing.contracts import pinned_binding
from app.core.org_context import spawn_detached
from app.harness.credentials.binding_writer import BindingWriteFailed, Material
from harness_core.credentials import CredentialPurpose
from tests.unit.connectors.test_binding_token_refresher import (  # noqa: F401 - fixture
    ORG,
    FakeMcpTokenEndpoint,
    World,
    _broker,
    mcp_endpoint,
    mcp_record,
)
from tests.unit.harness.credentials.binding_support import REFRESH_CLAIM_MS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

PURPOSE = CredentialPurpose(ORG, "mcp:mcp-1", "run-1", "attempt-1")


def _store_down_for(w: World, binding: str, times: int) -> list[str]:
    """Node's binder unreachable for ``binding``'s ``refreshed`` write, ``times`` times."""
    failures: list[str] = []
    real = w.writer.refreshed

    async def refreshed(org_id: str, binding_id: str, expected_revision: int, material: Material) -> Any:  # noqa: ANN401
        if binding_id == binding and len(failures) < times:
            failures.append(binding_id)
            raise BindingWriteFailed(f"POST /{binding_id}/refreshed: 503")
        return await real(org_id, binding_id, expected_revision, material)

    w.writer.refreshed = refreshed  # type: ignore[method-assign]
    return failures


async def test_qa23_1_a_failed_write_back_keeps_the_rotated_tokens_and_the_cycle_goes_on(
    mcp_endpoint: FakeMcpTokenEndpoint,  # noqa: F811
) -> None:
    w = World()
    first = await w.bind("mcp", "mcp-1", mcp_record(), expires_in_s=60)
    w.config.values["/services/mcp/dcr-clients/mcp-2"] = {"clientId": "dcr-client-1"}
    second = await w.bind("mcp", "mcp-2", mcp_record("mcp-refresh-b"), expires_in_s=120)
    _ = _store_down_for(w, first, times=1)

    cycle = await w.refresher.run_once()
    assert (cycle.failed, cycle.refreshed) == (1, 1), "one binding's store failure does not stop the others"
    assert w.writer.rows[second]["revision"] == 2
    assert len(mcp_endpoint.requests) == 2

    cycle = await w.refresher.run_once()
    assert cycle.refreshed == 1
    assert len(mcp_endpoint.requests) == 2, "the held tokens are written, the spent refresh token is not sent again"
    row = w.writer.rows[first]
    assert (row["status"], row["lastError"]) == ("active", None)
    record = await w.current(first)
    assert record["oauthTokens"]["accessToken"] == "mcp-access-2"
    assert record["oauthTokens"]["refreshToken"] in mcp_endpoint.live

    pin = pinned_binding(ref=first, version_digest=binding_version_digest(first, 1)).model_copy(update={"owner": "user:u1"})
    broker = _broker(w)
    lease = await broker.lease(pin, PURPOSE, 300)
    assert (await broker.resolve(lease, PURPOSE)).material["oauthTokens"]["accessToken"] == "mcp-access-2"  # type: ignore[index]


async def test_qa23_2_a_failure_record_that_cannot_be_written_does_not_stop_the_cycle(
    mcp_endpoint: FakeMcpTokenEndpoint,  # noqa: F811
) -> None:
    w = World()
    first = await w.bind("mcp", "mcp-1", mcp_record("mcp-refresh-spent"), expires_in_s=60)
    w.config.values["/services/mcp/dcr-clients/mcp-2"] = {"clientId": "dcr-client-1"}
    second = await w.bind("mcp", "mcp-2", mcp_record("mcp-refresh-b"), expires_in_s=120)
    real = w.writer.refresh_failed

    async def refresh_failed(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        raise BindingWriteFailed("POST refresh-failed: 503")

    w.writer.refresh_failed = refresh_failed  # type: ignore[method-assign]
    cycle = await w.refresher.run_once()
    assert cycle.refreshed == 1 and w.writer.rows[second]["revision"] == 2
    assert w.writer.rows[first]["status"] == "active"
    w.writer.refresh_failed = real  # type: ignore[method-assign]


def _replica(w: World, holder: str, **kwargs: Any) -> TokenRefresher:  # noqa: ANN401
    """Another connectors replica: its own refresher (and per-process locks) over the same Node and vault."""
    return TokenRefresher(w.writer, w.documents, [McpOAuthStrategy(w.config, w.now_ms)], w.clock, holder=holder, **kwargs)


def _while_on_the_wire(w: World, then: Callable[[], Awaitable[None]]) -> None:
    """Runs ``then`` while replica A's renewed tokens are on their way to Node, before they land."""
    real = w.writer.refreshed

    async def refreshed(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        w.writer.refreshed = real  # type: ignore[method-assign]
        await then()
        return await real(*args, **kwargs)

    w.writer.refreshed = refreshed  # type: ignore[method-assign]


async def test_qa23_3_two_replicas_racing_a_rotating_refresh_token_keep_the_binding(
    mcp_endpoint: FakeMcpTokenEndpoint,  # noqa: F811
) -> None:
    w = World()
    binding = await w.bind("mcp", "mcp-1", mcp_record(), expires_in_s=60)
    other = _replica(w, "replica-b")
    seen: list[Any] = []

    async def b_scans() -> None:
        # Replica B is another process: its scan shares nothing of A's bound org (spawn_detached's empty context).
        seen.append(await spawn_detached(other.run_once()))

    _while_on_the_wire(w, b_scans)
    assert (await w.refresher.run_once()).refreshed == 1
    assert (seen[0].refreshed, seen[0].refused, seen[0].failed) == (0, 0, 0), "B is given nothing A holds"
    assert len(mcp_endpoint.requests) == 1, "the refresh token is posted once"
    row = w.writer.rows[binding]
    assert (row["status"], row["lastError"]) == ("active", None)
    assert (await w.current(binding))["oauthTokens"]["accessToken"] == "mcp-access-2"


async def test_qa23_3b_an_on_demand_refresh_on_another_replica_waits_for_the_holder_and_does_not_renew(
    mcp_endpoint: FakeMcpTokenEndpoint,  # noqa: F811
) -> None:
    w = World()
    _ = await w.bind("mcp", "mcp-1", mcp_record(), expires_in_s=60)
    on_the_wire, go, a_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        on_the_wire.set()
        _ = await go.wait()

    async def pause(_s: float) -> None:
        go.set()
        _ = await a_finished.wait()

    _while_on_the_wire(w, hold)

    async def a_cycle() -> None:
        _ = await w.refresher.run_once()
        a_finished.set()

    a = asyncio.create_task(a_cycle())
    _ = await on_the_wire.wait()
    # B looks while A's write is held; B's wait lets A's write land.
    looked = asyncio.create_task(_replica(w, "replica-b", pause=pause).refresh_owner(ORG, "mcp-1", "user:u1"))
    await a
    record = await looked
    assert record is not None and record["oauthTokens"]["accessToken"] == "mcp-access-2"
    assert len(mcp_endpoint.requests) == 1


async def test_qa23_3c_a_holder_whose_write_failed_keeps_the_binding_until_it_stores_its_tokens(
    mcp_endpoint: FakeMcpTokenEndpoint,  # noqa: F811
) -> None:
    w = World()
    binding = await w.bind("mcp", "mcp-1", mcp_record(), expires_in_s=60)
    _ = _store_down_for(w, binding, times=1)
    assert (await w.refresher.run_once()).failed == 1
    assert (await _replica(w, "replica-b").run_once()).refreshed == 0, "B never posts the spent refresh token"
    w.clock.advance(300)
    assert (await w.refresher.run_once()).refreshed == 1
    assert len(mcp_endpoint.requests) == 1
    assert (await w.current(binding))["oauthTokens"]["accessToken"] == "mcp-access-2"


async def test_qa23_3d_a_lapsed_claim_is_taken_over(
    mcp_endpoint: FakeMcpTokenEndpoint,  # noqa: F811
) -> None:
    w = World()
    binding = await w.bind("mcp", "mcp-1", mcp_record(), expires_in_s=60)
    assert [d.id for d in await w.writer.due(2**53, 10, "replica-dead")] == [binding]
    other = _replica(w, "replica-b")
    assert (await other.run_once()).refreshed == 0
    w.clock.advance(REFRESH_CLAIM_MS / 1000)
    assert (await other.run_once()).refreshed == 1
    assert w.writer.rows[binding]["status"] == "active"
