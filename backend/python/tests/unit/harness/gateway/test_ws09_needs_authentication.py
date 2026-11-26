"""WS-09 (C3.38, OWD-7 "mask, don't remove"): a tool whose item is not connected stays in ``tools/list`` with
``needs_authentication``; every other tool is listed exactly as before, with no mark."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.gateway.mcp import AUTH_META
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    GRANTED,
    build_endpoint,
    claims_for,
)
from tests.unit.harness.gateway.support import GatewayServer, served

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


async def test_ws09_the_endpoint_lists_a_masked_tool_with_needs_authentication() -> None:
    world = build_endpoint(needs_authentication=frozenset({"lookup"}))
    listed = {t.name: t for t in await world.endpoint.list_tools(claims_for(BINDING))}
    assert sorted(listed) == sorted(t.name for t in GRANTED), "masked, not removed"
    assert listed["lookup"].auth == "needs_authentication"
    assert {name for name, t in listed.items() if t.auth is not None} == {"lookup"}
    assert {t.name: t.auth for t in world.endpoint.granted_tools()}["lookup"] == "needs_authentication"


async def test_ws09_tools_list_carries_the_mark_in_meta_and_nothing_for_connected_tools(server: GatewayServer) -> None:
    plain = build_endpoint()
    masked = build_endpoint(needs_authentication=frozenset({"lookup"}))
    async with served(server, plain.endpoint) as client, client.session(claims_for(BINDING)) as session:
        before = (await session.list_tools()).model_dump(by_alias=True, exclude_none=True, mode="json")
    async with served(server, masked.endpoint) as client, client.session(claims_for(BINDING)) as session:
        after = (await session.list_tools()).model_dump(by_alias=True, exclude_none=True, mode="json")
    marked = {t["name"]: t.get("_meta") for t in after["tools"]}
    assert marked["lookup"] == {AUTH_META: "needs_authentication"}
    unmarked = [t for t in after["tools"] if t["name"] != "lookup"]
    assert unmarked == [t for t in before["tools"] if t["name"] != "lookup"], "connected tools are byte-for-byte as before"
