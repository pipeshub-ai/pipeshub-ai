"""R36COVER-FU-7 (ADR-OWD-07 D-LAZY-5, D-LAZY-8): a child that lists its servers at the claim builds a server its parent
carried pins for from the stored catalog, as a child that lists at first use does, and never lists it again.

On the hub lane (``MCP_HUB_SCHEMA_TTL_S`` 1 s) a job's claim listed a server its parent carried pins for: a claim that
lists (lazy disclosure off for the run, as an engine whose policy lists every tool chooses) went straight to the
hub's pooled discovery, which answered from its schema cache only while that was fresh. The carried pin is the
trust (D-LAZY-5), so the claim now reads the stored catalog the parent's listing wrote, held to the pin, and lists only
when that catalog no longer matches it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.mcp_hub.discovery import tool_infos
from harness_core.mcp_hub.manifest import ToolDescriptor

from . import world as w
from .test_r31hot2_carried_pins import _build, _parent_pin, _spec

if TYPE_CHECKING:
    from collections.abc import Iterator

SCHEMA = {"type": "object", "properties": {"repo": {"type": "string"}}}
SERVED = [
    ToolDescriptor("list_prs", "List pull requests.", SCHEMA),
    ToolDescriptor("merge_pr", "Merge a pull request.", SCHEMA),
]


@pytest.fixture
def listings() -> Iterator[list[str]]:
    seen: list[str] = []

    async def listed(config: Any, credentials: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        seen.append(str(config.id))
        return tool_infos(config, SERVED, tenant=kwargs["tenant"]), {d.name: d for d in SERVED}

    with (
        w.deployment(),
        patch.dict("os.environ", {"MCP_HUB": "true"}),
        patch("app.agents.agent_loop.mcp_tool_loader.discover_listing_for", listed),
    ):
        yield seen


async def test_cp1_a_child_that_lists_at_the_claim_builds_its_parents_pinned_listing_without_listing(
    listings: list[str],
) -> None:
    services = w.Services()
    pin = await _parent_pin(services)
    assert listings == [w.MCP]
    with w.claim_listing():
        child = await _build(services, _spec(pin))
    assert listings == [w.MCP], "the claim made no second listing call"
    built = child.tools.describe("mcp_github_list_prs")
    assert built is not None and built.tool_id == f"mcp@{w.MCP}/list_prs"


async def test_cp2_a_stored_catalog_that_moved_from_the_pin_is_listed_and_held_to_the_pin(
    listings: list[str],
) -> None:
    services = w.Services()
    pin = await _parent_pin(services)
    moved = {**pin, "list_prs": "sha256:" + "f" * 64}
    with w.claim_listing():
        child = await _build(services, _spec(moved))
    assert listings == [w.MCP, w.MCP], "a pin the stored catalog does not match is listed"
    assert child.tools.describe("mcp_github_list_prs") is None, "and the listed tool at another digest is held"

