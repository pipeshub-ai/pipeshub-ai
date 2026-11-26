"""TG-05 and TG-08 through the MCP transport, over real HTTP with the MCP SDK's own client: what an engine reads.

A validation refusal and a rate-limited read keep their structured fields (``errors``, ``errorCode``,
``retryAfterS``) and their text across the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from tests.unit.harness.gateway.support import GatewayServer, served
from tests.unit.harness_core.gateway.test_tg05_argument_validation import (
    validating_endpoint,
)
from tests.unit.harness_core.gateway.test_tg08_rate_limited_reads import (
    LimitedSaas,
    limited_endpoint,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


async def test_tg05_invalid_arguments_arrive_as_field_errors_over_mcp(server: GatewayServer) -> None:
    endpoint, runs, _ = validating_endpoint()
    async with served(server, endpoint) as client:
        result = await client.call_tool(claims_for(BINDING), "search", {"query": "q", "filters": {"project": "ops"}})
    assert (result.is_error, result.reason_code, result.retryable) == (True, "invalid_arguments", True)
    assert [(e.field, e.fix) for e in result.field_errors] == [("filters.project", "send a value matching ^[A-Z]+$")]
    assert "`filters.project`" in result.text and runs == []


async def test_tg08_a_rate_limited_read_arrives_with_its_retry_after_over_mcp(server: GatewayServer) -> None:
    async with served(server, limited_endpoint(LimitedSaas(limited=100))) as client:
        result = await client.call_tool(claims_for(BINDING), "crm__search", {"q": "acme"})
    assert (result.is_error, result.error_code, result.retry_after_s) == (True, "rate_limited", 0.01)
