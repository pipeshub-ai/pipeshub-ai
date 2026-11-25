"""R31HOT2, ADR-OWD-07 D-LAZY-5: a child's tool environment uses the first-use pin its parent carried, and does not list
the server again.

The parent's first listing of a server it holds whole is journalled as its pin and written to the sign-in's stored
catalog (D-LAZY-1). A child pinned to that listing (``x.pipeshub.mcp[].pins``) builds its tools from the stored catalog
when every pinned tool is there at its pinned digest: the digest is the trust, so the child sees exactly what its
parent saw, and its first use costs no round trip. A tool its pin does not hold is not the child's; a stored catalog
that no longer matches the pin sends the child to the server, held to the pin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agents.agent_loop.mcp_lazy import PINS_SLOT
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


def _spec(pins: dict[str, str] | None = None, *, tool_ids: list[str] = (), whole: bool = True) -> RunSpec:  # type: ignore[assignment]
    raw = w.pinned_spec(tool_ids=list(tool_ids), whole=[w.MCP] if whole else [], mcp=[] if whole else [w.MCP])
    doc = raw.model_dump(mode="json", by_alias=True, exclude_none=True)
    if pins is not None:
        doc["extensions"]["x.pipeshub"]["mcp"][0]["pins"] = pins
    return validate_contract(RunSpec, doc)


async def _build(services: w.Services, spec: RunSpec) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
    return await assembler.build(w.claim(spec))  # type: ignore[arg-type]


async def _parent_pin(services: w.Services) -> dict[str, str]:
    """The parent's first use of the server: one listing, journalled as its pin."""
    parent = await _build(services, _spec())
    found = await parent.tools.execute(ToolCall(id="c1", name="search_tools", arguments={"query": "pull requests"}), CTX)
    pins = found.slots[PINS_SLOT]
    assert isinstance(pins, dict)
    assert parent.mcp_pins is not None and parent.mcp_pins() == pins, "what the kernel carries into a spawn"
    return dict(pins[w.MCP])  # type: ignore[arg-type]


def _names(runtime: AssembledRuntime) -> set[str]:
    return set(runtime.tools.names)  # type: ignore[attr-defined]


async def test_hot2_e1_the_child_builds_its_parents_listing_without_listing_again(listings: list[str]) -> None:
    services = w.Services()
    pin = await _parent_pin(services)
    assert listings == [w.MCP] and set(pin) == {"list_prs", "merge_pr"}
    child = await _build(services, _spec(pin))
    assert child.prepare_calls is not None
    await child.prepare_calls(["mcp_github_list_prs"])
    assert listings == [w.MCP], "the child made no second listing call"
    built = child.tools.describe("mcp_github_list_prs")
    assert built is not None and built.tool_id == f"mcp@{w.MCP}/list_prs"
    ran = await child.tools.execute(ToolCall(id="c2", name="mcp_github_list_prs", arguments={"repo": "a/b"}), CTX)
    assert PINS_SLOT not in ran.slots, "the child holds its parent's pin: nothing new to journal"


async def test_hot2_e2_a_tool_the_childs_pin_does_not_hold_is_refused(listings: list[str]) -> None:
    services = w.Services()
    pin = await _parent_pin(services)
    child = await _build(services, _spec({"list_prs": pin["list_prs"]}))
    assert "mcp_github_merge_pr" not in _names(child), "the stored catalog's other tool is not named to the child"
    assert child.prepare_calls is not None
    await child.prepare_calls(["mcp_github_list_prs", "mcp_github_merge_pr"])
    assert child.tools.describe("mcp_github_merge_pr") is None
    refused = await child.tools.execute(ToolCall(id="c2", name="mcp_github_merge_pr", arguments={}), CTX)
    assert refused.status == "error"
    assert listings == [w.MCP]


async def test_hot2_e3_a_pin_wider_than_the_childs_grant_grants_nothing_more(listings: list[str]) -> None:
    services = w.Services()
    pin = await _parent_pin(services)
    child = await _build(services, _spec(pin, tool_ids=[f"mcp@{w.MCP}/list_prs"], whole=False))
    assert child.prepare_calls is not None
    await child.prepare_calls(["mcp_github_list_prs", "mcp_github_merge_pr"])
    assert child.tools.describe("mcp_github_list_prs") is not None
    assert child.tools.describe("mcp_github_merge_pr") is None
    assert listings == [w.MCP]


async def test_hot2_e4_a_stored_catalog_that_moved_from_the_pin_is_never_used(listings: list[str]) -> None:
    services = w.Services()
    pin = await _parent_pin(services)
    moved = {**pin, "list_prs": "sha256:" + "0" * 64}
    child = await _build(services, _spec(moved))
    refused = await child.tools.execute(ToolCall(id="c2", name="mcp_github_list_prs", arguments={"repo": "a/b"}), CTX)
    assert listings == [w.MCP, w.MCP], "listed, as a resumed attempt would be"
    assert refused.status == "error" and "changed the tool list_prs" in str(refused.content)


async def test_hot2_e5_with_no_pin_carried_the_child_lists_at_its_own_first_use(listings: list[str]) -> None:
    services = w.Services()
    _ = await _parent_pin(services)
    child = await _build(services, _spec())
    assert child.prepare_calls is not None
    await child.prepare_calls(["mcp_github_list_prs"])
    assert listings == [w.MCP, w.MCP]


async def test_hot2_e6_an_engine_jobs_gateway_asks_for_its_parents_listing_and_never_a_tool_outside_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine side (code_task's job behind the Tool Gateway): the job's first call of a server its parent listed
    asks for that listing (``stored_first``), and a tool the parent's listing did not serve is not the job's."""
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.mcp_access import MCPAccessResolver
    from app.agents.agent_loop.mcp_lazy import LazyMcpServers
    from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
    from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
    from app.agents.mcp.models import MCPToolInfo
    from app.harness.engines.durable import engine_lazy_port
    from harness_core.tools.durable_port import RegistryToolPort
    from tests.unit.harness.engines.test_lazy_engine_first_use import (
        _context,
        _deferred,
    )

    context = _context(None)
    (resolved,) = await MCPAccessResolver.resolve(context)
    asked: list[bool] = []

    async def listed(self: MCPToolProvider, server: Any, ctx: Any, names: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        asked.append(bool(kwargs.get("stored_first")))
        infos = [
            MCPToolInfo(name=t, namespaced_name=f"mcp_{names['namespace']}_{t}", description=t,
                        input_schema={"type": "object"}, annotations={"readOnlyHint": True})
            for t in ("search_items", "delete_item")
        ]
        return [MCPToolAdapter(resolved, i, session_manager=None) for i in infos], None  # type: ignore[arg-type]

    monkeypatch.setattr(MCPToolProvider, "list_server", listed)
    registry = ToolRegistry()
    lazy = LazyMcpServers(context, carried={"inst-1": {"search_items": "sha256:" + "5" * 64}})
    await lazy.register(registry, [], ["inst-1"])
    (name,) = lazy.granted_names()
    assert name.endswith("search_items"), "the job is named only what its parent's listing served"
    port = RegistryToolPort(registry, {}, **engine_lazy_port(registry, _deferred(name), lazy, lambda _e: True))
    await port.execute(ToolCall(id="c1", name=name, arguments={"q": "x"}), CTX)
    assert asked == [True]
    refused = await port.execute(ToolCall(id="c2", name="delete_item", arguments={}), CTX)
    assert refused.status == "error"
    assert lazy.pins == {"inst-1": {"search_items": "sha256:" + "5" * 64}}


# --- R43MCP-FU-3: a resumed attempt's own first-use pins are a listing the run already took ----------------------------


def _saved_pins(pin: dict[str, str]) -> Any:  # noqa: ANN401 - a JournalEvent
    from harness_core.journal.types import JournalEvent

    return JournalEvent(
        org_id=w.ORG, session_id="ses-1", stream_id="run-1", seq=1, run_id="run-1", type="loop.state_saved",
        critical=True, ts="2026-11-14T00:00:00Z", writer="worker", data={"slots": {PINS_SLOT: {w.MCP: pin}}},
    )


async def test_fu3_a_resumed_attempt_builds_its_own_pinned_server_from_the_stored_catalog(listings: list[str]) -> None:
    """The owner-pins journeys at ``MCP_HUB_SCHEMA_TTL_S=1``: the parent resumed after its job (``resume_dispatch``)
    and listed the server it had pinned at its own first use again, live, once the hub's cache had expired. A claim
    builds a carried server from the stored catalog when it matches the pin (cf9e97d8da); a resumed attempt now does
    the same for the pins its run journalled."""
    services = w.Services()
    pin = await _parent_pin(services)
    resumed = await _build(services, _spec())
    assert resumed.restore is not None and resumed.prepare_calls is not None
    await resumed.restore.restore([_saved_pins(pin)])
    await resumed.prepare_calls(["mcp_github_list_prs"])
    assert listings == [w.MCP], "the resumed attempt made no second listing call"
    built = resumed.tools.describe("mcp_github_list_prs")
    assert built is not None and built.tool_id == f"mcp@{w.MCP}/list_prs"
    ran = await resumed.tools.execute(ToolCall(id="c2", name="mcp_github_list_prs", arguments={"repo": "a/b"}), CTX)
    assert PINS_SLOT not in ran.slots, "the run keeps its first pin"


async def test_fu3_a_stored_catalog_that_moved_from_the_resumed_pin_is_listed_and_held(listings: list[str]) -> None:
    services = w.Services()
    pin = await _parent_pin(services)
    resumed = await _build(services, _spec())
    assert resumed.restore is not None
    await resumed.restore.restore([_saved_pins({**pin, "list_prs": "sha256:" + "0" * 64})])
    refused = await resumed.tools.execute(ToolCall(id="c2", name="mcp_github_list_prs", arguments={"repo": "a/b"}), CTX)
    assert listings == [w.MCP, w.MCP], "listed live, held to the pin"
    assert refused.status == "error" and "changed the tool list_prs" in str(refused.content)
