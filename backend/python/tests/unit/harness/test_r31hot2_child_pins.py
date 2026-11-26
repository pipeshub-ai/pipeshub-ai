"""R31HOT2, ADR-OWD-07 D-LAZY-5: a parent's first-use MCP pins ride its child request, and the child is pinned to them.

The owner's default chat holds Atlassian Rovo whole; once it has listed Rovo, the job it hands work to used to list
Rovo again at its own first use (a second round trip on the job's critical path, against tools the parent never saw).
The kernel now carries the run's pins (``DurableKernel.mcp_pins``) into every spawn its journal accepts them on
(``child_mcp_pins``), and the reference journal pins the child as Node does (``contracts/harness/vectors/
child-mcp.json`` CM-9..CM-15): narrowed to what the child holds, never wider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal.children import ChildGrantRequest, ChildRequest
from harness_core.journal.types import APPROVAL_REASONS, CHILD_LABELS, CHILD_MCP_PINS
from harness_core.kernel.durable.delegation import (
    DelegatingTool,
    NamedDelegation,
    goal_tool,
)
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    import pytest
    from pydantic import JsonValue

ROVO = "mcp-rovo"
CREATE = "sha256:" + "1" * 64
GET = "sha256:" + "2" * 64
PINS = {ROVO: {"createJiraIssue": CREATE, "getJiraIssue": GET}}
EXT: dict[str, JsonValue] = {
    "knowledge": {"apps": [], "kb": [], "strictScope": False},
    "mcp": [{"instanceId": ROVO, "manifestDigest": "sha256:" + "a" * 64, "tools": "at_first_use"}],
    "skills": [],
    "mounts": [],
    "via": "session",
}


def _world(**journal: object) -> World:
    world = World(**journal)  # type: ignore[arg-type]
    spec = minimal_run_spec(max_children=2).model_copy(update={"extensions": {"x.pipeshub": EXT}})
    world.admit(spec=validate_contract(RunSpec, spec.model_dump(mode="json", by_alias=True, exclude_none=True)))
    return world


async def _child_mcp(world: World, grants: ChildGrantRequest, pins: object) -> list[dict[str, JsonValue]]:
    _, port = scripted(reply("", ("delegate", {"goal": "file the ticket"})), reply("done"))
    delegate = DelegatingTool(goal_tool("delegate", "Hand a task over"), grants)
    kernel = world.kernel(
        port, children=world.journal, delegation=NamedDelegation({"delegate": delegate}), mcp_pins=pins
    )
    assert (await world.run(kernel)).reason == "join"
    (child,) = world.journal.children_of(RUN)
    pinned = next(e for e in world.journal.events(child) if e.type == "run.spec_pinned")
    spec = cast("dict[str, JsonValue]", pinned.data["spec"])
    return cast("list[dict[str, JsonValue]]", spec["extensions"]["x.pipeshub"]["mcp"])  # type: ignore[index, call-overload]


async def test_hot2_p1_a_job_of_a_parent_that_listed_the_server_is_pinned_to_that_listing() -> None:
    mcp = await _child_mcp(_world(), ChildGrantRequest(), lambda: PINS)
    assert mcp == [{**EXT["mcp"][0], "pins": PINS[ROVO]}]  # type: ignore[index, dict-item]


async def test_hot2_p2_a_child_narrowed_to_one_tool_keeps_only_that_tools_pin() -> None:
    mcp = await _child_mcp(_world(), ChildGrantRequest(tools=(f"mcp@{ROVO}/getJiraIssue",)), lambda: PINS)
    assert [m.get("pins") for m in mcp] == [{"getJiraIssue": GET}]


async def test_hot2_p3_a_parent_that_has_not_listed_the_server_carries_nothing() -> None:
    mcp = await _child_mcp(_world(), ChildGrantRequest(), lambda: {})
    assert mcp == [EXT["mcp"][0]]  # type: ignore[index]


async def test_hot2_p4_an_older_node_gets_no_pins_and_the_child_lists_as_before(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    world = _world(journal_accepts=frozenset({APPROVAL_REASONS, CHILD_LABELS}))
    mcp = await _child_mcp(world, ChildGrantRequest(), lambda: PINS)
    assert mcp == [EXT["mcp"][0]]  # type: ignore[index]
    assert "child.mcp_pins_withheld" in caplog.text


async def test_hot2_p5_a_tool_outside_the_parents_pin_is_refused_to_the_child() -> None:
    world = _world()
    _, port = scripted(reply("", ("delegate", {"goal": "delete it"})), reply("could not"))
    delegate = DelegatingTool(
        goal_tool("delegate", "Hand a task over"), ChildGrantRequest(tools=(f"mcp@{ROVO}/deleteJiraIssue",))
    )
    kernel = world.kernel(
        port, children=world.journal, delegation=NamedDelegation({"delegate": delegate}), mcp_pins=lambda: PINS
    )
    _ = await world.run(kernel)
    assert world.journal.children_of(RUN) == []


def test_hot2_p6_the_request_carries_pins_as_context_not_identity() -> None:
    assert CHILD_MCP_PINS == "child_mcp_pins"
    plain = ChildRequest(invocation_id="t1.0", goal="g")
    assert plain.mcp_pins is None
