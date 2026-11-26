"""CPD-7 slice 2: the production item runtimes (03-interfaces §3.2).

IR-1..IR-4: CP-SKEL's ``ItemRuntimeContract`` on ``ToolsetItemRuntime``, ``McpItemRuntime`` and
``SkillsItemRuntime``, each over a connector that serves live digests and counts the sessions it opens. IR-5:
``ItemRuntimeRegistryContract`` on ``ItemRuntimes``. Then each production connector against fakes of the
stores it reads, and SKL-G6: a skill loads only while its pin holds.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.control_plane.runtime.types import RunContext
from app.control_plane.testing.contracts import (
    BINDING,
    MATERIAL,
    PURPOSE,
    ItemRuntimeContract,
    ItemRuntimeRegistryContract,
    effective_item,
    pinned_binding,
    tool_digest,
)
from app.control_plane.testing.memory import InMemoryCredentialBroker
from app.harness.assembly.items.base import LiveTool, OpenedItem, PinnedItemRuntime
from app.harness.assembly.items.credentials import PinnedBindingLeases
from app.harness.assembly.items.mcp import McpConnector, McpItemRuntime
from app.harness.assembly.items.registry import ItemRuntimes
from app.harness.assembly.items.run import PinnedCredentialsUnavailable
from app.harness.assembly.items.skills import GraphSkillConnector, SkillsItemRuntime
from app.harness.assembly.items.toolsets import RunToolsetConnector, ToolsetItemRuntime
from harness_core.credentials.errors import CredentialUnavailable
from harness_core.driver import CapabilityLost
from harness_core.mcp_hub.errors import ManifestUnavailable
from harness_core.mcp_hub.manifest import ApprovedManifest
from harness_core.testing.clock import ManualClock

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.control_plane.contracts.generated.effective_set import EffectiveItem

CTX = RunContext("org-1", "run-1", "attempt-1")


def _broker() -> InMemoryCredentialBroker:
    counter = itertools.count(1)
    return InMemoryCredentialBroker(ManualClock(), ids=lambda: f"{next(counter):08d}")


class SessionConnector:
    """Serves every pinned tool at its contract digest; one session per opened item, closed by its closer."""

    def __init__(self) -> None:
        self.open_sessions = 0

    async def open(self, item: EffectiveItem, ctx: RunContext, credentials: object) -> OpenedItem:
        self.open_sessions += 1

        async def close() -> None:
            self.open_sessions -= 1

        live = {t.tool_id: LiveTool(tool_digest(t.tool_id.rsplit("/", 1)[-1]), handle=t.tool_id) for t in item.tools}
        return OpenedItem(live=live, close=close)


@dataclass
class _Harness:
    make: Callable[[SessionConnector], PinnedItemRuntime]
    connector: SessionConnector = field(default_factory=SessionConnector)
    credentials: InMemoryCredentialBroker = field(default_factory=_broker)

    @property
    def runtime(self) -> PinnedItemRuntime:
        return self.make(self.connector)

    def open_sessions(self) -> int:
        return self.connector.open_sessions


class TestToolsetItemRuntimeContract(ItemRuntimeContract):
    @pytest.fixture
    def item_runtime(self) -> _Harness:
        return _Harness(ToolsetItemRuntime)


class TestMcpItemRuntimeContract(ItemRuntimeContract):
    @pytest.fixture
    def item_runtime(self) -> _Harness:
        return _Harness(McpItemRuntime)


class TestSkillsItemRuntimeContract(ItemRuntimeContract):
    @pytest.fixture
    def item_runtime(self) -> _Harness:
        return _Harness(SkillsItemRuntime)


class TestItemRuntimesContract(ItemRuntimeRegistryContract):
    @pytest.fixture
    def registry_factory(self) -> Callable[[], ItemRuntimes]:
        return ItemRuntimes


async def test_a_changed_tool_is_refused_and_nothing_opened_stays_open() -> None:
    connector = SessionConnector()
    runtime = ToolsetItemRuntime(connector)
    item = effective_item("toolset", "inst-a", ["list"])
    stale = item.model_copy(update={"tools": [item.tools[0].model_copy(update={"descriptor_digest": effective_item("toolset", "x", ["other"]).tools[0].descriptor_digest})]})
    with pytest.raises(CapabilityLost) as lost:
        await runtime.load([stale], CTX, _broker())
    assert lost.value.changed == ("tool:toolset@inst-a/list",)
    assert connector.open_sessions == 0


async def test_the_registry_names_every_lost_capability_across_kinds_and_closes_what_loaded() -> None:
    toolsets, mcp = SessionConnector(), SessionConnector()

    class Unreadable(SessionConnector):
        async def open(self, item: EffectiveItem, ctx: RunContext, credentials: object) -> OpenedItem:
            opened = await super().open(item, ctx, credentials)
            return OpenedItem(live={}, close=opened.close, unreadable=True)

    mcp = Unreadable()
    registry = ItemRuntimes([ToolsetItemRuntime(toolsets), McpItemRuntime(mcp)])
    items = [effective_item("toolset", "inst-a", ["list"]), effective_item("mcp", "inst-m", ["search"])]
    with pytest.raises(CapabilityLost) as lost:
        await registry.load(items, CTX, _broker())
    assert lost.value.missing == ("tool:mcp@inst-m/search",)
    assert toolsets.open_sessions == 0 and mcp.open_sessions == 0
    stale = effective_item("toolset", "inst-b", ["list"])
    stale = stale.model_copy(update={"tools": [stale.tools[0].model_copy(update={"descriptor_digest": effective_item("toolset", "x", ["other"]).tools[0].descriptor_digest})]})
    with pytest.raises(CapabilityLost) as both:
        await registry.load([stale, items[1]], CTX, _broker())
    assert (both.value.missing, both.value.changed) == (("tool:mcp@inst-m/search",), ("tool:toolset@inst-b/list",))
    with pytest.raises(CapabilityLost) as unknown:
        await ItemRuntimes([ToolsetItemRuntime(toolsets)]).load(items, CTX, _broker())
    assert unknown.value.missing == ("mcp",)
    assert toolsets.open_sessions == 0


class Descriptors:
    def __init__(self, toolset: dict[tuple[str, str], str] | None = None, approved: ApprovedManifest | None = None,
                 *, unreadable: bool = False) -> None:
        self.toolset, self.approved, self.unreadable = toolset or {}, approved, unreadable

    def toolset_tool(self, toolset: str, tool: str) -> str | None:
        return self.toolset.get((toolset, tool))

    async def approved_mcp_tools(self, instance_id: str) -> ApprovedManifest | None:
        if self.unreadable:
            raise ManifestUnavailable("down")
        return self.approved


async def test_toolset_connector_serves_the_declared_digest_and_the_registry_name() -> None:
    item = effective_item("toolset", "inst-a", ["list"]).model_copy()
    tool_id = item.tools[0].tool_id
    declared = Descriptors({("toolset", "list"): tool_digest("list")})
    loaded = await ToolsetItemRuntime(RunToolsetConnector(declared, {tool_id: "jira.list"})).load([item], CTX, _broker())
    assert [(t.tool_id, t.handle) for t in loaded.tools] == [(tool_id, "jira.list")]
    with pytest.raises(CapabilityLost) as lost:
        await ToolsetItemRuntime(RunToolsetConnector(Descriptors(), {})).load([item], CTX, _broker())
    assert lost.value.changed == (f"tool:{tool_id}",)


async def test_mcp_connector_checks_tools_and_the_granted_manifest_and_closes_its_session() -> None:
    item = effective_item("mcp", "inst-m", ["search"])
    approved = ApprovedManifest({"search": tool_digest("search")}, "admin", 1)
    granted = approved.granted_digest(["search"]) or ""
    closed: list[str] = []

    async def close(instance: str) -> None:
        closed.append(instance)

    loaded = await McpItemRuntime(McpConnector(Descriptors(approved=approved), {}, close), {"inst-m": granted}).load([item], CTX, _broker())
    assert [t.tool_id for t in loaded.tools] == ["mcp@inst-m/search"]
    await loaded.close()
    assert closed == ["inst-m"]

    moved = ApprovedManifest({"search": tool_digest("search-v2")}, "admin", 2)
    with pytest.raises(CapabilityLost) as lost:
        await McpItemRuntime(McpConnector(Descriptors(approved=moved), {}, close), {"inst-m": granted}).load([item], CTX, _broker())
    assert set(lost.value.changed) == {"tool:mcp@inst-m/search", "mcp:inst-m"}
    with pytest.raises(CapabilityLost) as unreadable:
        await McpItemRuntime(McpConnector(Descriptors(unreadable=True), {}, close)).load([item], CTX, _broker())
    assert unreadable.value.missing == ("tool:mcp@inst-m/search",) and unreadable.value.changed == ()
    assert closed == ["inst-m", "inst-m", "inst-m"]


def _skill_item(name: str, digest: str) -> EffectiveItem:
    item = effective_item("skill", f"skill_{name}", [name])
    return item.model_copy(update={"tools": [item.tools[0].model_copy(update={"tool_id": f"{name}@skill_{name}/{name}", "descriptor_digest": type(item.tools[0].descriptor_digest)(digest)})]})


async def test_skl_g6_a_skill_loads_only_while_its_pin_holds() -> None:
    holding = {("notes", tool_digest("notes-v1"))}

    async def holds(org: str, name: str, digest: str) -> bool:
        return (name, digest) in holding

    runtime = SkillsItemRuntime(GraphSkillConnector(holds))
    loaded = await runtime.load([_skill_item("notes", tool_digest("notes-v1"))], CTX, _broker())
    assert [t.handle for t in loaded.tools] == ["notes"]
    with pytest.raises(CapabilityLost) as lost:
        await runtime.load([_skill_item("notes", tool_digest("notes-v0"))], CTX, _broker())
    assert lost.value.changed == ("skill:notes",)


async def test_skl_10_an_engine_run_s_item_load_holds_its_pinned_skills_as_the_loop_s_does() -> None:
    """FB-11 replaces FB-10's split: a pin means one thing on every executor, so the engine's item load checks its
    pinned skills too, through the same reader."""
    from types import SimpleNamespace

    from app.harness.assembly.items.pins import pinned_items
    from app.harness.assembly.items.run import load_pinned_items

    async def withdrawn(org: str, name: str, digest: str) -> bool:
        return False

    run = SimpleNamespace(org_id="org-1", run_id="run-1", resolved=None, descriptor_pins=(), skill_digests={"notes": tool_digest("notes-v1")})
    runtimes = ItemRuntimes([SkillsItemRuntime(GraphSkillConnector(withdrawn))])
    assert [item.installation.root for item in pinned_items(run)] == ["skill_notes"]  # type: ignore[arg-type]
    with pytest.raises(CapabilityLost) as lost:
        await load_pinned_items(run, runtimes, "att-1")  # type: ignore[arg-type]
    assert lost.value.changed == ("skill:notes",)


class TestAssembledSkillPins:
    """SKL-G6 through the real assembler: a pinned skill is held to its pin, whichever loop runs it."""

    @pytest.fixture(autouse=True)
    def _deployment(self) -> Iterator[None]:
        from tests.unit.harness.assembly import world as w

        with w.deployment():
            yield

    async def _build(self, monkeypatch: pytest.MonkeyPatch, holding: bool) -> object:
        from app.harness.assembly.assembler import PipesHubRuntimeAssembler
        from app.harness.assembly.items.run import SkillReader
        from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
        from harness_core.testing.durable import ScriptedModelPort
        from tests.unit.harness.assembly import world as w

        async def holds(_self: SkillReader, _org: str, _name: str, _digest: str) -> bool:
            return holding

        monkeypatch.setattr(SkillReader, "holds", holds)
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
        # No skills source: the skills feature's own loading is not what this case is about.
        assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port), sources=lambda: [])
        return await assembler.build(w.claim(w.pinned_spec(tool_ids=[], skills=["xlsx"])))  # type: ignore[arg-type]

    async def test_the_pinned_bundle_builds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runtime = await self._build(monkeypatch, holding=True)
        await runtime.close()  # type: ignore[attr-defined]

    async def test_a_skill_whose_pin_no_longer_holds_is_capability_lost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(CapabilityLost) as lost:
            await self._build(monkeypatch, holding=False)
        assert lost.value.changed == ("skill:xlsx",)


async def test_ir_cred_the_broker_of_a_run_without_a_pin_refuses_every_lease() -> None:
    broker = PinnedCredentialsUnavailable()
    with pytest.raises(CredentialUnavailable):
        _ = await broker.lease(pinned_binding(), PURPOSE, 300)
    assert await broker.close() == 0


@pytest.mark.parametrize("kind", ["toolset", "mcp"])
async def test_ir_cred_a_bound_item_is_leased_from_the_broker_its_load_was_given(kind: str) -> None:
    """CPD-10: a pinned run's items name bindings, and the production connectors lease them as they load."""
    broker = _broker()
    version = broker.store(BINDING, MATERIAL)
    leases = PinnedBindingLeases(broker)  # type: ignore[arg-type]

    class _Approved:
        def toolset_tool(self, toolset: str, tool: str) -> str | None:
            return tool_digest(tool)

        async def approved_mcp_tools(self, instance_id: str) -> ApprovedManifest | None:
            return ApprovedManifest(tools={"list": tool_digest("list")}, approved_by="u-admin", approved_at=1)

    runtime = (
        ToolsetItemRuntime(RunToolsetConnector(_Approved(), {}, leases))
        if kind == "toolset"
        else McpItemRuntime(McpConnector(_Approved(), {}, None, leases))
    )
    bound = effective_item(kind, "inst-a", ["list"]).model_copy(update={"binding": pinned_binding(version_digest=version)})

    loaded = await runtime.load([bound], CTX, broker)

    assert [tool.tool_id for tool in loaded.tools] == [f"{kind}@inst-a/list"]
    assert await leases.material("inst-a") == MATERIAL

    broker.tombstone(BINDING)
    with pytest.raises(CapabilityLost) as lost:
        _ = await ToolsetItemRuntime(RunToolsetConnector(_Approved(), {}, PinnedBindingLeases(broker))).load(  # type: ignore[arg-type]
            [effective_item("toolset", "inst-a", ["list"]).model_copy(update={"binding": pinned_binding(version_digest=version)})],
            CTX,
            broker,
        )
    assert lost.value.changed == ("credential:inst-a:credential_revoked",)
