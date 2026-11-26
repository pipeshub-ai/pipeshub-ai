"""SPENDCONT: an engine's MCP client reads a bounded tool result; ``ph`` gets the whole of it for a file.

A Codex child in a manual test kept 54 Jira searches of 51 KB (median) to 214 KB in its conversation and resent them with every
model call. The gateway now cuts a result past ``ENGINE_RESULT_CAP_CHARS`` for the engine's conversation, keeping
its head and tail and the ``ph call … > file`` that fetches it whole; the journal keeps the whole result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.gateway import McpResult
from harness_core.gateway.result_cap import (
    ENGINE_RESULT_CAP_CHARS,
    FULL_RESULT_META,
    cap_engine_result,
    wants_full_result,
)
from harness_core.testing.contracts.tool_endpoint import build_endpoint, claims_for
from harness_core.workspace.sync.storage_classes import TOOL_RESULTS_DIR
from harness_engines.pipeshub import ph
from tests.unit.harness.gateway.support import GatewayServer, served
from tests.unit.harness.gateway.test_ph_contract import ph_served

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LONG = "x" * 60_000


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


def _result(text: str, *, error: bool = False) -> McpResult:
    return McpResult(text=text, is_error=error, disposition="executed", retryable=error)


def test_a_long_result_keeps_its_ends_and_says_how_to_get_it_whole() -> None:
    text = "HEAD" + "y" * 100_000 + "TAIL"
    capped = cap_engine_result(_result(text), tool="searchJiraIssuesUsingJql", arguments={"jql": "project = PP"})
    assert len(capped.text) < ENGINE_RESULT_CAP_CHARS + 800
    assert capped.text.startswith("HEAD") and "TAIL" in capped.text
    # WP98-FU-4: ph saves the whole result itself, under the one harness-owned folder, and prints its path.
    assert "ph call searchJiraIssuesUsingJql --json '{\"jql\":\"project = PP\"}'" in capped.text
    assert f"saves the whole result under {TOOL_RESULTS_DIR}/" in capped.text
    assert "> notes/" not in capped.text
    assert "100008 characters" in capped.text


def test_short_results_and_errors_pass_whole() -> None:
    short = _result("PP-1: SSRF")
    assert cap_engine_result(short, tool="t", arguments={}) is short
    error = _result("e" * 50_000, error=True)
    assert cap_engine_result(error, tool="t", arguments={}) is error


def test_only_ph_s_marker_asks_for_the_whole_result() -> None:
    assert wants_full_result({FULL_RESULT_META: True, "progressToken": 3})
    assert not wants_full_result({"progressToken": 3}) and not wants_full_result(None)
    assert ph.FULL_RESULT_META == FULL_RESULT_META


async def test_over_the_gateway_the_engine_reads_it_cut_and_ph_reads_it_whole(server: GatewayServer) -> None:
    world = build_endpoint()
    claims = claims_for(world.endpoint.binding)
    async with served(server, world.endpoint) as mcp:
        through_mcp = await mcp.call_tool(claims, "lookup", {"q": LONG})
    async with ph_served(server, world.endpoint) as through_ph:
        whole = await through_ph.call_tool(claims, "lookup", {"q": LONG})
    assert len(through_mcp.text) < ENGINE_RESULT_CAP_CHARS + 800 and "ph call lookup" in through_mcp.text
    assert whole.text == f"lookup:{LONG}"
