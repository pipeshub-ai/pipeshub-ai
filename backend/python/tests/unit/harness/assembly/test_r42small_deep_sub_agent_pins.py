"""R38IDENT-FU-13 (ADR-OWD-07 D-LAZY-5 amended): a deep run's sub-agent holds the listing its coordinator took at the
claim, and does not list the server again.

Gate line ``J-agent.deep-seeded-hub`` failed once: the deep coordinator lists its MCP servers at the claim (a deep
run's model cannot search for a tool, ``mcp_at_first_use``), but only a run that listed at first use carried its pins
to a child (``first_use_pins``). Its sub-agent, which lists at first use, therefore listed the STDIO hub server a second
time, on its own critical path and while the coordinator's runner session was still open; when that second listing
did not answer, every call of the server's tool in the sub-agent was ``tool.recovery choice=not_available`` and the
server was never called. A run that listed at the claim now carries that listing to its children as their pin, as a
run that listed at first use does: the sub-agent builds the tools from the stored catalog the coordinator's listing
wrote, so its first use asks the server nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.mcp_hub.discovery import tool_infos
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.mcp_hub.manifest import ToolDescriptor
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver.run_driver import AssembledRuntime

CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "k")
SCHEMA = {"type": "object", "properties": {"repo": {"type": "string"}}}
SERVED = [
    ToolDescriptor("list_prs", "List pull requests.", SCHEMA),
    ToolDescriptor("merge_pr", "Merge a pull request.", SCHEMA),
]
TOOL = "mcp_github_list_prs"
OTHER = "mcp_github_merge_pr"


@pytest.fixture
def listings() -> Iterator[list[str]]:
    """The hub's listings of the server (MCP_HUB on, as for every harness run), counted."""
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


def _spec(*, sub_agent: dict[str, Any] | None = None, pins: dict[str, str] | None = None) -> RunSpec:
    """A deep root granted the server whole, or its sub-agent pinned to the one tool it was handed."""
    if sub_agent is None:
        raw = w.pinned_spec(tool_ids=[], whole=[w.MCP]).model_dump(mode="json", by_alias=True, exclude_none=True)
        raw["budgets"]["maxChildren"] = 16
    else:
        raw = w.pinned_spec(tool_ids=[f"mcp@{w.MCP}/list_prs"], mcp=[w.MCP]).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        raw["extensions"]["x.pipeshub"]["subAgent"] = sub_agent
        if pins is not None:
            raw["extensions"]["x.pipeshub"]["mcp"][0]["pins"] = pins
    raw["extensions"]["x.pipeshub"]["agent"]["mode"] = "deep"
    return validate_contract(RunSpec, raw)


async def _build(services: w.Services, spec: RunSpec) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
    return await assembler.build(w.claim(spec, chat_mode="deep"))  # type: ignore[arg-type]


async def test_fu13_1_a_deep_root_lists_at_the_claim_and_carries_that_listing_to_its_sub_agents(
    listings: list[str],
) -> None:
    services = w.Services()
    root = await _build(services, _spec())
    try:
        assert root.delegation is not None, "a deep root that delegates"
        assert root.prepare_calls is None, "a deep run lists at the claim, not at first use"
        assert listings == [w.MCP]
        assert root.mcp_pins is not None, "the claim's listing is what a sub-agent inherits"
        pins = root.mcp_pins()
        assert set(pins) == {w.MCP} and set(pins[w.MCP]) == {"list_prs", "merge_pr"}
        assert all(d.startswith("sha256:") for d in pins[w.MCP].values())
    finally:
        await root.close()


async def test_fu13_2_the_sub_agent_calls_the_tool_it_was_handed_with_no_second_listing(listings: list[str]) -> None:
    services = w.Services()
    root = await _build(services, _spec())
    assert root.mcp_pins is not None and root.delegation is not None
    pool = list(root.delegation._pool)  # pyright: ignore[reportAttributeAccessIssue]
    assert TOOL in pool
    carried = dict(root.mcp_pins()[w.MCP])
    await root.close()
    # What Node pins the child: the parent's pin, narrowed to the one tool the child holds (D-LAZY-8, CM-9..CM-15).
    child = await _build(
        services, _spec(sub_agent={"role": "researcher", "toolNames": pool}, pins={"list_prs": carried["list_prs"]})
    )
    try:
        assert child.prepare_calls is not None, "a sub-agent lists at first use"
        await child.prepare_calls([TOOL])
        assert listings == [w.MCP], "the sub-agent built its parent's listing: the server was asked nothing more"
        built = child.tools.describe(TOOL)
        assert built is not None and built.tool_id == f"mcp@{w.MCP}/list_prs"
        assert child.tools.describe(OTHER) is None, "a tool the child was not handed stays out"
    finally:
        await child.close()


async def test_fu13_3_a_tool_the_root_no_longer_holds_is_not_carried(listings: list[str]) -> None:
    """The pin is what the run holds when it spawns: a tool withheld since the claim (unregistered) is not carried."""
    services = w.Services()
    root = await _build(services, _spec())
    try:
        assert root.mcp_pins is not None
        registry = root.tools._registry  # pyright: ignore[reportAttributeAccessIssue]
        _ = registry.withdraw(OTHER)
        assert set(root.mcp_pins()[w.MCP]) == {"list_prs"}
        ran = await root.tools.execute(ToolCall(id="c1", name=TOOL, arguments={"repo": "a/b"}), CTX)
        assert ran is not None
    finally:
        await root.close()

