"""FOLLOWUPS1-1: a skill an agent wrote carries the label of the run that wrote it, and loading it brings that label.

FLOWASK counted the skill reads as carrying no record content, since a skill is the agent's configuration a person put
in the catalog. A skill a run's model wrote (``skill_manage``, post-run learning, an approved candidate) can summarise
what that run read, and with ``PIPESHUB_SKILLS_AUTO_APPROVE`` on nobody reviews it. So it carries the writing run's
label, as a memory does (P7.3), through edits, versions, export and import, the bundle an engine mounts, and is
erased with the conversation it came from.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    SkillCandidate,
    SkillProvenance,
    SkillSource,
)
from app.agent_loop_lib.modules.providers.skills.in_memory_tracker import (
    InMemoryUsageTracker,
)
from app.agent_loop_lib.modules.providers.skills.loader import (
    parse_skill_md,
    render_skill_md,
)
from app.agent_loop_lib.modules.providers.skills.manager import (
    SkillManager,
    SkillManagerConfig,
)
from app.agent_loop_lib.tools.builtin.data.skills import (
    LoadSkillResourceTool,
    LoadSkillTool,
    SkillManageTool,
)
from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
from app.agents.agent_loop.skills.graph_store import (
    GraphSkillStore,
    erase_session_learned_skills,
)
from app.agents.agent_loop.skills.labelled_manager import (
    SKILL_LABELS_STATE,
    LabelledSkillManager,
)
from app.agents.agent_loop.skills.provenance import RunSkillProvenance, content_label
from app.agents.run_definition import (
    AgentRunDefinition,
    RunDefinitionSkill,
    pin_skill_content,
)
from app.config.constants.arangodb import CollectionNames
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.citations import CitationJournal
from app.harness.flow_destination import pipeshub_destination
from app.harness.labels import (
    SessionSourceLedger,
    citation_sources,
    label_from_wire,
    own_work_label,
)
from app.harness.skills.pinned_labels import mounted_skills
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import GrantingLedger
from harness_core.effects.types import ToolEffect
from harness_core.journal.types import SKILL_LABELS, NewEvent
from harness_core.kernel.durable import ToolExecution
from harness_core.logs import HarnessLog
from harness_core.skills import SkillPin
from tests.unit.agents.adapter.test_skills_graph_store import (
    FakeGraphProvider,
    _NullIndex,
)
from tests.unit.harness_core.durable.support import RUN, SESSION, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import DurableKernel, ToolContext

LOG = HarnessLog.named("tests.followups1")
ORG = "org-1"
OUT = SinkDescriptor(egress="external", args_leave_boundary=True)
INSIDE = SinkDescriptor(egress="none", args_leave_boundary=False)
SEARCH = "dynamic__web_search"
SEARCH_EFFECT = ToolEffect("web@duckduckgo/web_search", "read", True, OUT)
PRIVATE = {"complete": True, "sources": [{"kind": "indexed", "recordId": "rec-1"}]}
HUMAN_MD = "---\nname: {name}\ndescription: Use when deploying\n---\n\nStep 1. Build.\n"


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


class _Labels:
    """A run's source ledger, as the provenance reads it."""

    def __init__(self, label: dict[str, Any] | None) -> None:
        self.label = label

    def context_label(self, run_id: str) -> dict[str, Any] | None:
        return self.label


def _provenance(label: dict[str, Any] | None, session: str = "conv-1") -> RunSkillProvenance:
    provenance = RunSkillProvenance("run-w", session)
    provenance.bind(_Labels(label))  # type: ignore[arg-type]
    return provenance


def _manager(graph: FakeGraphProvider, provenance: Any = None, user: str = "user-1") -> SkillManager:  # noqa: ANN401
    store = GraphSkillStore(graph, ORG, user, provenance=provenance)  # type: ignore[arg-type]
    return SkillManager(
        store=store, index=_NullIndex(), tracker=InMemoryUsageTracker(), config=SkillManagerConfig(),  # type: ignore[arg-type]
    )


async def _learned(graph: FakeGraphProvider, label: dict[str, Any] | None = PRIVATE, name: str = "q3-summary") -> None:
    """What the skill writer (or the run's own model) does: ``skill_manage create`` on the run's store."""
    tool = SkillManageTool(_manager(graph, _provenance(label)))
    out = await tool.execute(action="create", name=name, description="Use for Q3 numbers", body="Revenue was 4.2M.")
    assert out.success, out.error


def _stored(graph: FakeGraphProvider, name: str = "q3-summary") -> dict[str, Any]:
    return graph._col(CollectionNames.AGENT_SKILLS.value)[f"{ORG}_{name}"]


def _label_of(graph: FakeGraphProvider, name: str = "q3-summary") -> dict[str, Any] | None:
    return content_label(parse_skill_md(_stored(graph, name)["content"]).metadata)


# Writing: the run's label goes on the content, with the conversation.


async def test_a_skill_a_run_writes_carries_the_runs_label_and_conversation() -> None:
    graph = FakeGraphProvider()
    await _learned(graph)
    assert _label_of(graph) == PRIVATE
    assert _stored(graph)["sourceSessionIds"] == ["conv-1"]
    assert _stored(graph)["source"] == SkillSource.AGENT_CREATED.value


async def test_a_run_without_a_ledger_writes_unknown() -> None:
    graph = FakeGraphProvider()
    tool = SkillManageTool(_manager(graph, RunSkillProvenance("run-w", "conv-1")))
    assert (await tool.execute(action="create", name="q3", description="Use it", body="Steps.")).success
    assert _label_of(graph, "q3") == {"complete": False, "sources": []}


async def test_a_person_s_catalog_skill_carries_no_label() -> None:
    graph = FakeGraphProvider()
    await _manager(graph).create("deploy", HUMAN_MD.format(name="deploy"))
    assert _label_of(graph, "deploy") is None
    assert "sourceSessionIds" not in _stored(graph, "deploy")


async def test_labels_only_rise_through_edits_resources_and_rollback() -> None:
    graph = FakeGraphProvider()
    await _learned(graph)
    # A person's edit through the management API renders no label: the stored one stays.
    person = _manager(graph)
    await person.update("q3-summary", HUMAN_MD.format(name="q3-summary"))
    assert _label_of(graph) == PRIVATE
    # A second run that read another record writes a resource: both records now.
    other = {"complete": True, "sources": [{"kind": "indexed", "recordId": "rec-2"}]}
    assert await _manager(graph, _provenance(other, "conv-2")).write_resource("q3-summary", "references/a.md", "x")
    both = {"complete": True, "sources": [{"kind": "indexed", "recordId": "rec-1"}, {"kind": "indexed", "recordId": "rec-2"}]}
    assert _label_of(graph) == both
    assert _stored(graph)["sourceSessionIds"] == ["conv-1", "conv-2"]
    # Rolling back to the first version restores its text, never sheds what a run put in since.
    first = (await person.list_versions("q3-summary"))[-1].version
    await person.rollback("q3-summary", first)
    assert _label_of(graph) == both
    # Every version kept in the log carries the label its text was written under.
    versions = graph._col(CollectionNames.AGENT_SKILL_VERSIONS.value).values()
    assert all(content_label(parse_skill_md(v["content"]).metadata) is not None for v in versions)


async def test_a_queued_candidate_keeps_the_learning_runs_label_and_approval_carries_it() -> None:
    graph = FakeGraphProvider()
    learning = _manager(graph, _provenance(PRIVATE))
    candidate = SkillCandidate(
        candidate_id="c1", name="q3-summary", description="Use for Q3", body="Revenue was 4.2M.", created_at="now",
    )
    await learning.queue_candidate(candidate)
    # A person approves it through the management API: its text is still the run's.
    reviewer = _manager(graph)
    [pending] = await reviewer.get_pending_candidates()
    assert pending.label == PRIVATE and pending.source_session_id == "conv-1"
    await reviewer.approve_candidate("c1")
    assert _label_of(graph) == PRIVATE
    assert _stored(graph)["sourceSessionIds"] == ["conv-1"]


# Loading: the label comes back into the loading run.


def _context(labels: bool = True) -> SimpleNamespace:
    return SimpleNamespace(tool_state={SKILL_LABELS_STATE: []} if labels else {})


async def test_loading_a_learned_skill_journals_its_label_and_a_person_s_journals_nothing() -> None:
    graph = FakeGraphProvider()
    await _learned(graph)
    await _manager(graph).create("deploy", HUMAN_MD.format(name="deploy"))
    context = _context()
    manager = LabelledSkillManager(_manager(graph), context.tool_state)
    journal = CitationJournal(context)

    assert (await LoadSkillTool(manager).execute(name="deploy")).data["body"]
    assert journal.delta("c0") is None

    assert (await LoadSkillTool(manager).execute(name="q3-summary")).data["body"]
    delta = journal.delta("c1")
    assert delta is not None and delta["skillLabels"] == [{"skill": "q3-summary", "label": PRIVATE}]
    assert label_from_wire(PRIVATE) == citation_sources(delta)


async def test_a_learned_skills_resource_brings_its_label_too() -> None:
    graph = FakeGraphProvider()
    await _learned(graph)
    assert await _manager(graph, _provenance(PRIVATE)).write_resource("q3-summary", "references/q3.md", "4.2M")
    context = _context()
    out = await LoadSkillResourceTool(LabelledSkillManager(_manager(graph), context.tool_state)).execute(
        name="q3-summary", path="references/q3.md"
    )
    assert out.data["content"] == "4.2M"
    assert [e["label"] for e in context.tool_state[SKILL_LABELS_STATE]] == [PRIVATE]


async def test_a_learned_skill_without_a_label_loads_as_unknown() -> None:
    graph = FakeGraphProvider()
    content = HUMAN_MD.format(name="old").replace("description: Use when deploying", (
        "description: Use when deploying\nmetadata:\n  agent-loop:\n    source: agent_created"
    ))
    await _manager(graph).create("old", content)
    context = _context()
    _ = await LabelledSkillManager(_manager(graph), context.tool_state).activate_skill("old")
    [entry] = context.tool_state[SKILL_LABELS_STATE]
    assert entry["label"] == {"complete": False, "sources": []}
    unknown = citation_sources({"skillLabels": [entry]})
    assert (unknown.complete, unknown.sources) == (False, frozenset())
    # An unreadable label is unknown too, never none.
    assert citation_sources({"skillLabels": [{"skill": "x", "label": {"complete": "yes"}}]}).complete is False


async def test_a_journal_that_cannot_record_the_label_gets_no_learned_skill() -> None:
    graph = FakeGraphProvider()
    await _learned(graph)
    await _manager(graph).create("deploy", HUMAN_MD.format(name="deploy"))
    manager = LabelledSkillManager(_manager(graph), _context(labels=False).tool_state)
    assert (await manager.activate_skill("deploy")).name == "deploy"
    with pytest.raises(RegistryError, match="upgraded"):
        await manager.activate_skill("q3-summary")


# The flow rule: a learned skill from a private read makes a later web search ask, naming the source.


async def _load_tool(graph: FakeGraphProvider) -> Any:  # noqa: ANN401
    context = _context()
    tool = LoadSkillTool(LabelledSkillManager(_manager(graph), context.tool_state))
    journal = CitationJournal(context)

    async def load_skill(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        out = await tool.execute(name=str(args["name"]))
        delta = journal.delta(str(args["name"]))
        events = (NewEvent(CITATIONS_EVENT, delta, critical=False),) if delta is not None else ()
        return ToolExecution(status="ok", content=cast("JsonValue", out.data), events=events)

    return load_skill


async def _web(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content={"results": []})


async def _world(graph: FakeGraphProvider) -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})
    world.tools.add("load_skill", await _load_tool(graph), effect=ToolEffect("pipeshub@skills/load_skill", "read", True, INSIDE))
    world.tools.add(SEARCH, _web, effect=SEARCH_EFFECT)
    world.admit()
    return world


def _kernel(world: World, *replies: object) -> DurableKernel:
    _, port = scripted(*replies)  # type: ignore[arg-type]
    ledger = GrantingLedger(SessionSourceLedger())
    approval = flow_guarded(
        lambda call, effect: "allow", ledger=ledger, run_id=RUN, log=LOG, rules=flow_rules(LOG),
        destination=pipeshub_destination, grants=ledger.grants,
    )
    return world.kernel(port, approval=approval, ledger=ledger)


def _load(name: str) -> object:
    return reply("", ("load_skill", {"name": name}))


SEARCH_CALL = reply("", (SEARCH, {"query": "q3 revenue benchmarks"}))


async def test_a_skill_learned_from_a_private_read_makes_a_later_web_search_ask_naming_the_source() -> None:
    graph = FakeGraphProvider()
    await _learned(graph)
    world = await _world(graph)
    done = await world.run(_kernel(world, _load("q3-summary"), SEARCH_CALL))
    assert done.status == "suspended"
    [explained] = world.events("interaction.explained")
    assert explained.data["sensitivity"] == "private"
    assert explained.data["sources"] == [{"recordId": "rec-1"}]
    label = own_work_label(world.events(), RUN)
    assert (label.complete, {s.record_id for s in label.sources}) == (True, {"rec-1"})


async def test_a_person_s_catalog_skill_then_a_web_search_never_asks() -> None:
    graph = FakeGraphProvider()
    await _manager(graph).create("deploy", HUMAN_MD.format(name="deploy"))
    world = await _world(graph)
    done = await world.run(_kernel(world, _load("deploy"), SEARCH_CALL, reply("ok")))
    assert done.status == "completed"
    assert world.events("interaction.explained") == []


async def test_the_run_that_learns_stamps_what_its_ledger_had_read() -> None:
    """The provenance reads the run's own source ledger: a private read before the write is on the skill."""
    graph = FakeGraphProvider()
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})
    ledger = GrantingLedger(SessionSourceLedger())
    provenance = RunSkillProvenance(RUN, SESSION)
    provenance.bind(ledger)
    manage = SkillManageTool(_manager(graph, provenance))

    async def read(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}}}
        return ToolExecution(status="ok", content="Q3 plan", events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))

    async def skill_manage(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        out = await manage.execute(**cast("dict[str, Any]", args))
        return ToolExecution(status="ok", content=cast("JsonValue", out.data))

    world.tools.add(FETCH_RECORD_TOOL_NAME, read, tags=[("category", "read")])
    world.tools.add("skill_manage", skill_manage, effect=ToolEffect("pipeshub@skills/skill_manage", "write", False, INSIDE))
    world.admit()
    write = {"action": "create", "name": "q3-summary", "description": "Use for Q3", "body": "Revenue was 4.2M."}
    _, port = scripted(reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"})), reply("", ("skill_manage", write)), reply("ok"))  # type: ignore[arg-type]
    done = await world.run(world.kernel(port, approval=lambda call, effect: "allow", ledger=ledger))
    assert done.status == "completed"
    assert _label_of(graph) == PRIVATE
    assert _stored(graph)["sourceSessionIds"] == [SESSION]


# An engine mounts its pinned bundles: the mount journals the learned ones' labels before the engine runs.


async def _pin(graph: FakeGraphProvider, name: str) -> SkillPin:
    definition = AgentRunDefinition(agentKey="a", definitionVersion=1, skills=(RunDefinitionSkill(name=name),))
    pinned = (await pin_skill_content(definition, GraphSkillSource(graph), ORG)).skills[0]  # type: ignore[arg-type]
    assert pinned.bundle_digest is not None
    return SkillPin(name, pinned.bundle_digest)


def _claim(accepts: frozenset[str] | None) -> Any:  # noqa: ANN401
    return SimpleNamespace(
        org_id=ORG, journal_accepts=lambda feature: accepts is None or feature in accepts,
    )


async def test_an_engine_mount_journals_the_learned_bundles_label_and_the_child_run_counts_it() -> None:
    graph = FakeGraphProvider()
    await _learned(graph)
    await _manager(graph).create("deploy", HUMAN_MD.format(name="deploy"))
    pins = (await _pin(graph, "q3-summary"), await _pin(graph, "deploy"))
    mounted = await mounted_skills(pins, graph, _claim(None), LOG)
    assert mounted.pins == pins
    assert mounted.labels is not None
    assert mounted.labels.data == {"skillLabels": [{"skill": "q3-summary", "label": PRIVATE}]}
    # The engine run's own-work label joins it, as the parent's joins the child's.
    assert citation_sources(mounted.labels.data) == label_from_wire(PRIVATE)
    # Only a person's skill: nothing to journal.
    human = await mounted_skills(pins[1:], graph, _claim(None), LOG)
    assert (human.pins, human.labels) == (pins[1:], None)


async def test_an_engine_whose_journal_cannot_record_the_label_does_not_mount_the_learned_bundle() -> None:
    graph = FakeGraphProvider()
    await _learned(graph)
    await _manager(graph).create("deploy", HUMAN_MD.format(name="deploy"))
    pins = (await _pin(graph, "q3-summary"), await _pin(graph, "deploy"))
    mounted = await mounted_skills(pins, graph, _claim(frozenset({"child_labels"})), LOG)
    assert (mounted.pins, mounted.labels) == (pins[1:], None)
    assert SKILL_LABELS == "skill_labels"


async def test_an_unreadable_pinned_bundle_mounts_as_unknown() -> None:
    graph = FakeGraphProvider()
    pin = SkillPin("gone", "sha256:" + "0" * 64)
    mounted = await mounted_skills((pin,), graph, _claim(None), LOG)
    assert mounted.labels is not None
    assert mounted.labels.data["skillLabels"] == [{"skill": "gone", "label": {"complete": False, "sources": []}}]


# Export and import: the label travels in SKILL.md.


async def test_an_exported_learned_skill_imports_with_its_label() -> None:
    graph = FakeGraphProvider()
    await _learned(graph)
    exported = render_skill_md(await _manager(graph).get_skill("q3-summary"))
    other_org = FakeGraphProvider()
    await _manager(other_org).create("q3-summary", exported)
    assert _label_of(other_org) == PRIVATE
    context = _context()
    _ = await LabelledSkillManager(_manager(other_org), context.tool_state).activate_skill("q3-summary")
    assert context.tool_state[SKILL_LABELS_STATE] == [{"skill": "q3-summary", "label": PRIVATE}]


# Erasure: as a memory derived from a conversation goes with it, so does a skill its runs wrote.


async def test_erasing_the_conversation_erases_the_skills_its_runs_wrote_and_nothing_else() -> None:
    graph = FakeGraphProvider()
    await _learned(graph, name="from-conv-1")
    tool = SkillManageTool(_manager(graph, _provenance(PRIVATE, "conv-2")))
    assert (await tool.execute(action="create", name="from-conv-2", description="Use it", body="Steps.")).success
    await _manager(graph).create("deploy", HUMAN_MD.format(name="deploy"))
    # A later edit keeps the skill tied to its first conversation, and its version log goes with it.
    assert (await SkillManageTool(_manager(graph, _provenance(PRIVATE, "conv-3"))).execute(
        action="patch", name="from-conv-1", old_string="4.2M", new_string="4.3M",
    )).success
    assert await erase_session_learned_skills(graph, ORG, "conv-1", page=1) == (1, False)  # type: ignore[arg-type]
    skills = graph._col(CollectionNames.AGENT_SKILLS.value)
    assert set(skills) == {f"{ORG}_from-conv-2", f"{ORG}_deploy"}
    assert not [v for v in graph._col(CollectionNames.AGENT_SKILL_VERSIONS.value).values() if v["name"] == "from-conv-1"]
    assert await erase_session_learned_skills(graph, ORG, "conv-1") == (0, False)  # type: ignore[arg-type]


async def test_the_store_writes_its_provenance_on_a_plain_create_too() -> None:
    graph = FakeGraphProvider()
    store = GraphSkillStore(graph, ORG, "user-1")  # type: ignore[arg-type]
    await store.create_skill(
        "q3", HUMAN_MD.format(name="q3"), provenance=SkillProvenance(label=PRIVATE, session_id="conv-9"),
    )
    assert _label_of(graph, "q3") == PRIVATE
    assert _stored(graph, "q3")["sourceSessionIds"] == ["conv-9"]
