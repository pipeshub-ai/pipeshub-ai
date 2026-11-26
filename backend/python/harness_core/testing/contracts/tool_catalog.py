"""Contract suite every ``ToolCatalog`` adapter must pass (TC-1..TC-8).

Subclass :class:`ToolCatalogContract` in a test module and provide a
``harness`` fixture returning a :class:`ToolCatalogHarness`. The harness builds
a catalog over the offered tools, granting those whose ``toolId`` is in the
grant. An offered tool called with ``{"q": v}`` appends its name to
``harness.runs`` and answers ``ok`` with ``"<name>:<v>"``.
``InMemoryToolCatalog`` and ``RegistryToolPort`` run it in
``tests/unit/harness_core/invoker``; another host runs it against its own catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.types import EffectKind, ToolContext, ToolEffect, tool_id_for
from harness_core.kernel.core.messages import ToolCall

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from harness_core.invoker.ports import ToolCatalog


@dataclass(frozen=True)
class OfferedTool:
    name: str
    effect: ToolEffect


class ToolCatalogHarness(Protocol):
    @property
    def runs(self) -> list[str]: ...

    def build(self, offered: Sequence[OfferedTool], grant: Collection[str]) -> ToolCatalog: ...


def offered(name: str, kind: EffectKind = "read", *, instance: str = "inst-1") -> OfferedTool:
    """A tool as ``classify_tool_effect`` classifies one tagged only with its effect: a read asks its own system
    (``self``), a workspace call stays in the run, anything else is ``external``."""
    egress = {"read": "self", "workspace": "none"}.get(kind, "external")
    return OfferedTool(
        name,
        ToolEffect(
            tool_id=tool_id_for(name, source="crm", instance=instance),
            effect=kind,
            idempotent=kind in ("read", "workspace"),
            sink=SinkDescriptor(egress=egress, args_leave_boundary=egress == "external"),  # pyright: ignore[reportArgumentType]
        ),
    )


LOOKUP = offered("lookup")
CREATE = offered("create", "write")
DELETE = offered("delete", "write")
TOOLS = (LOOKUP, CREATE, DELETE)
GRANT = (LOOKUP.effect.tool_id, CREATE.effect.tool_id)

CTX = ToolContext("run-1", "scope-1", "inv-1", "attempt-1", "scope-1:run-1:inv-1")


def call(name: str, q: str = "x") -> ToolCall:
    return ToolCall(id=f"call-{name}", name=name, arguments={"q": q})


class ToolCatalogContract:
    @pytest.fixture
    def harness(self) -> ToolCatalogHarness:
        raise NotImplementedError("provide a harness fixture")

    def test_tc1_schemas_name_exactly_the_grant(self, harness: ToolCatalogHarness) -> None:
        catalog = harness.build(TOOLS, GRANT)
        assert sorted(s.name for s in catalog.schemas() or []) == ["create", "lookup"]

    def test_tc2_a_granted_call_is_described(self, harness: ToolCatalogHarness) -> None:
        catalog = harness.build(TOOLS, GRANT)
        lookup, create = catalog.describe_call(call("lookup")), catalog.describe_call(call("create"))
        assert lookup is not None and create is not None
        assert (lookup.tool_id, lookup.effect) == (LOOKUP.effect.tool_id, "read")
        assert (create.tool_id, create.effect) == (CREATE.effect.tool_id, "write")

    def test_tc3_a_withheld_tool_is_not_described(self, harness: ToolCatalogHarness) -> None:
        assert harness.build(TOOLS, GRANT).describe_call(call("delete")) is None

    def test_tc4_an_unknown_name_is_not_described(self, harness: ToolCatalogHarness) -> None:
        assert harness.build(TOOLS, GRANT).describe_call(call("ghost")) is None

    async def test_tc5_a_granted_tool_runs_once(self, harness: ToolCatalogHarness) -> None:
        catalog = harness.build(TOOLS, GRANT)
        execution = await catalog.execute(call("create", "a"), CTX)
        assert (execution.status, execution.content) == ("ok", "create:a")
        assert harness.runs == ["create"]

    async def test_tc6_a_withheld_tool_never_runs(self, harness: ToolCatalogHarness) -> None:
        catalog = harness.build(TOOLS, GRANT)
        execution = await catalog.execute(call("delete"), CTX)
        assert execution.status == "error"
        assert harness.runs == []

    async def test_tc7_an_unknown_name_is_an_error(self, harness: ToolCatalogHarness) -> None:
        execution = await harness.build(TOOLS, GRANT).execute(call("ghost"), CTX)
        assert execution.status == "error"
        assert harness.runs == []

    async def test_tc8_an_empty_grant_offers_nothing(self, harness: ToolCatalogHarness) -> None:
        catalog = harness.build(TOOLS, ())
        assert not (catalog.schemas() or [])
        assert all(catalog.describe_call(call(t.name)) is None for t in TOOLS)
        assert (await catalog.execute(call("lookup"), CTX)).status == "error"
        assert harness.runs == []


__all__ = [
    "CREATE",
    "CTX",
    "DELETE",
    "GRANT",
    "LOOKUP",
    "TOOLS",
    "OfferedTool",
    "ToolCatalogContract",
    "ToolCatalogHarness",
    "call",
    "offered",
]
