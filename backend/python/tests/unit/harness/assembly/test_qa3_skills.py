"""QA-3 SKL-15 (SKL-G4) and SKL-G6: what a run may do with the skills it pinned.

``allowed-tools`` is advisory in this release on every path (today's loop, the durable loop, engine runs): an engine's
gateway cannot see a skill being activated, and the loop must not enforce what engines cannot. It is parsed, kept
and shown; it narrows nothing.

The production assembler builds the run (``world``), with the org's skills in the in-memory graph the skill store
reads and the pin Node would take for them (``GraphSkillSource.content_digest``); calls go through the run's own
tool port, as the kernel makes them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.config.constants.arangodb import CollectionNames
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import CapabilityLost
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.skills.types import SkillRef
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.support.config_service import RIG_PLATFORM_FLAGS
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver import AssembledRuntime

JIRA_SEARCH = f"jira@{w.JIRA}/search_issues"
JIRA_CREATE = f"jira@{w.JIRA}/create_issue"
MCP_PRS = f"mcp@{w.MCP}/list_prs"
SKILL = "triage"
AUTHOR = "agent-editor"
SKILL_COLLECTIONS = frozenset({CollectionNames.AGENT_SKILLS.value, CollectionNames.AGENT_SKILL_VERSIONS.value})
_CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "k")


def skill_md(name: str, body: str) -> str:
    return (
        f"---\nname: {name}\ndescription: Use when triaging issues.\n"
        f"allowed-tools: jira__search_issues mcp_github_list_prs\n---\n\n{body}\n"
    )


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with w.deployment():
        # After the world's own environment, which turns skills off.
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
        yield


@dataclass
class Rig:
    """The world with skills on, the org's skills in an in-memory graph, and one run that pins ``triage``."""

    services: w.Services
    skills: FakeGraphProvider
    spec: RunSpec
    created: list[str]

    @classmethod
    async def admitted(cls, monkeypatch: pytest.MonkeyPatch) -> Rig:
        created: list[str] = []

        async def create_issue(self: object, title: str) -> str:
            created.append(title)
            return '{"id": "J-1"}'

        w.replace_tool(monkeypatch, w.FakeJira, "create_issue", create_issue)
        services, skills = w.Services(), FakeGraphProvider()
        services.config.put(
            PLATFORM_SETTINGS_KEY, {"featureFlags": {**RIG_PLATFORM_FLAGS, "ENABLE_MCP": True, "ENABLE_SKILLS": True}}
        )
        elsewhere = services.graph.get_document

        async def get_document(
            key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False,
        ) -> dict[str, Any] | None:
            source = skills.get_document if collection in SKILL_COLLECTIONS else elsewhere
            return await source(key, collection)

        services.graph.get_document = get_document
        for method in (
            "get_nodes_by_filters", "batch_upsert_nodes", "update_node", "update_node_if_match", "delete_nodes",
            "batch_create_edges", "delete_edges_from", "get_edges_to_node", "get_edges_from_node", "get_edge",
            "batch_delete_edges",
        ):
            setattr(services.graph, method, getattr(skills, method))
        rig = cls(services, skills, w.pinned_spec(tool_ids=[]), created)
        _ = await rig.author().create_skill(SKILL, skill_md(SKILL, "Search, never file."))
        _ = await rig.author().create_skill("unpinned", skill_md("unpinned", "Not this run's."))
        # Assigning the skill to the agent is what grants it (and shares it) to the agent's runs.
        _ = await skills.batch_create_edges(
            [{"from_id": w.AGENT, "to_id": f"{w.ORG}_{SKILL}"}], CollectionNames.AGENT_HAS_SKILL.value
        )
        rig.spec = await rig._pin()
        return rig

    async def narrow(self, how: str) -> None:
        """What stops the skill for runs in flight: deleting it, disabling it, or unassigning it from the agent."""
        if how == "delete":
            assert await self.author().delete_skill(SKILL)
        elif how == "disable":
            from app.agent_loop_lib.modules.providers.skills.base import SkillStatus

            _ = await self.author().set_skill_status(SKILL, SkillStatus.DISABLED)
        else:
            _ = await self.skills.batch_delete_edges(
                [{"from_id": w.AGENT, "to_id": f"{w.ORG}_{SKILL}"}], CollectionNames.AGENT_HAS_SKILL.value
            )

    def author(self) -> GraphSkillStore:
        """The agent's editor, who shares the skill by assigning it: the person running the agent is someone else,
        so unassigning withdraws it (their own skill an agent stops assigning is offered to them again, ADR-SKL-01)."""
        return GraphSkillStore(self.skills, w.ORG, AUTHOR)  # type: ignore[arg-type]

    async def _pin(self) -> RunSpec:
        digest = await GraphSkillSource(self.skills).content_digest(w.ORG, SkillRef(SKILL))  # type: ignore[arg-type]
        raw = dump_contract(w.pinned_spec(tool_ids=[JIRA_SEARCH, JIRA_CREATE, MCP_PRS], mcp=[w.MCP], skills=[SKILL]))
        raw["extensions"]["x.pipeshub"]["skills"] = [{"name": SKILL, "bundleDigest": digest}]
        return validate_contract(RunSpec, raw)

    async def claim(self) -> AssembledRuntime:
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="qa3-skills", turns=[Turn(reply=Reply(text="ok"))])))
        assembler = PipesHubRuntimeAssembler(self.services, model_factory=w.model_factory(port))
        return await assembler.build(w.claim(self.spec))  # type: ignore[arg-type]


async def _call(runtime: AssembledRuntime, tool: str, **arguments: object) -> str:
    result = await runtime.tools.execute(ToolCall(id=f"c-{tool}", name=tool, arguments=arguments), _CTX)
    return str(result.content)


async def test_skl15_without_the_skill_loaded_the_run_calls_what_it_was_granted(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = await Rig.admitted(monkeypatch)
    runtime = await rig.claim()

    _ = await _call(runtime, "jira__create_issue", title="before")

    assert rig.created == ["before"]


ALLOWED = ["jira__search_issues", "mcp_github_list_prs"]


async def test_skl15_the_field_is_parsed_kept_and_shown_with_the_skill_s_details(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent_loop_lib.modules.providers.skills.base import SkillMetadata
    from app.api.routes.skills import (
        _metadata_to_dict,  # pyright: ignore[reportPrivateUsage]
    )

    rig = await Rig.admitted(monkeypatch)

    stored = await rig.author().get_skill(SKILL)

    assert stored is not None and stored.metadata.allowed_tools == ALLOWED
    assert SkillMetadata.from_raw(stored.metadata.to_frontmatter_dict()).allowed_tools == ALLOWED
    assert _metadata_to_dict(stored.metadata)["allowedTools"] == ALLOWED
    shown = json.loads(await _call(await rig.claim(), "load_skill", name=SKILL))
    assert shown["allowed_tools"] == ALLOWED


async def test_skl15_on_the_durable_loop_a_loaded_skill_narrows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = await Rig.admitted(monkeypatch)
    runtime = await rig.claim()
    assert "Search, never file." in await _call(runtime, "load_skill", name=SKILL)

    _ = await _call(runtime, "jira__create_issue", title="filed")

    assert rig.created == ["filed"]


async def test_skl15_on_today_s_loop_a_loaded_skill_narrows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Today's loop as its factory wires it: the skill tools, the toolset's tools and the factory's hooks."""
    from app.agent_loop_lib.transport.registry import TransportRegistry
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.factory import PipesHubAgentFactory
    from app.agents.agent_loop.skills_wiring import (
        build_skill_manager,
        register_skill_tools,
    )
    from harness_core.tools.executor import ToolExecutor
    from harness_core.tools.registry import ToolRegistry
    from harness_core.tools.toolset import ToolsetBuilder

    rig = await Rig.admitted(monkeypatch)
    context = AgentContext(
        org_id=w.ORG, user_id=w.ACTOR, user_email="user@corp.example", user_info={"userId": w.ACTOR, "orgId": w.ORG},
        graph_provider=rig.skills, config_service=rig.services.config, agent_skills=[SKILL],  # type: ignore[arg-type]
    )
    manager = await build_skill_manager(context, TransportRegistry())
    assert manager is not None
    registry = ToolRegistry()
    register_skill_tools(registry, manager)
    ToolsetBuilder(w.FakeJira(None), name="jira", description="Jira", path_prefix="/tools/jira").register_into(registry)
    executor = ToolExecutor(registry, PipesHubAgentFactory._build_hooks(context, tool_registry=registry))  # pyright: ignore[reportPrivateUsage]
    loaded = await executor.execute(ToolCall(id="c1", name="load_skill", arguments={"name": SKILL}))
    assert "Search, never file." in str(loaded.content)

    _ = await executor.execute(ToolCall(id="c2", name="jira__create_issue", arguments={"title": "filed"}))

    assert rig.created == ["filed"]


async def test_skl15_an_engine_run_that_pins_the_skill_is_offered_and_may_call_what_one_without_it_may(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.harness.pinning.support import EngineRig

    rig = await Rig.admitted(monkeypatch)
    without = dump_contract(rig.spec)
    without["extensions"]["x.pipeshub"]["skills"] = []
    pinned = await EngineRig(rig.services, rig.spec).attempt()
    plain = await EngineRig(rig.services, validate_contract(RunSpec, without)).attempt()

    offered = await pinned.offered()

    assert {"jira__search_issues", "jira__create_issue"} <= set(offered)
    # ADR-OWD-07 D-LAZY-4: the MCP server's tool waits behind the gateway's search until the run first uses it.
    assert "search_tools" in offered and "mcp_github_list_prs" not in offered
    assert offered == await plain.offered()
    assert await pinned.call("jira__create_issue", title="x") == await plain.call("jira__create_issue", title="x")


async def test_sklg6_a_run_loads_the_skill_it_pinned_and_no_other_of_its_user_s(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = await Rig.admitted(monkeypatch)
    runtime = await rig.claim()

    assert "Search, never file." in await _call(runtime, "load_skill", name=SKILL)
    assert "Not this run's." not in await _call(runtime, "load_skill", name="unpinned")
    assert "unpinned" not in await _call(runtime, "skills_list")


async def test_sklg6_a_skill_edited_between_admission_and_the_first_claim_runs_as_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = await Rig.admitted(monkeypatch)
    _ = await rig.author().update_skill(SKILL, skill_md(SKILL, "File everything, to everyone."))

    runtime = await rig.claim()

    loaded = await _call(runtime, "load_skill", name=SKILL)
    assert "Search, never file." in loaded and "File everything" not in loaded


# SKP1 (FB-11): one meaning of a skill pin on every executor.


async def test_skp1_loop_an_edit_mid_run_never_reaches_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = await Rig.admitted(monkeypatch)
    runtime = await rig.claim()
    _ = await rig.author().update_skill(SKILL, skill_md(SKILL, "File everything, to everyone."))

    loaded = await _call(runtime, "load_skill", name=SKILL)

    assert "Search, never file." in loaded and "File everything" not in loaded


@pytest.mark.parametrize("narrowing", ["delete", "disable", "unassign"])
async def test_skp1_loop_a_narrowing_mid_run_stops_the_skill_at_its_next_read(
    monkeypatch: pytest.MonkeyPatch, narrowing: str
) -> None:
    rig = await Rig.admitted(monkeypatch)
    runtime = await rig.claim()
    assert "Search, never file." in await _call(runtime, "load_skill", name=SKILL)

    await rig.narrow(narrowing)

    stopped = await _call(runtime, "load_skill", name=SKILL)
    assert "Search, never file." not in stopped and '"error"' in stopped
    with pytest.raises(CapabilityLost) as lost:
        _ = await rig.claim()
    assert f"skill:{SKILL}" in (*lost.value.changed, *lost.value.missing)


async def test_skp1_a_pin_no_stored_version_matches_is_capability_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    # A resource edit keeps the version and leaves no snapshot, so the admitted content is in no stored version.
    rig = await Rig.admitted(monkeypatch)
    assert await rig.author().write_resource(SKILL, "notes.md", "changed")

    with pytest.raises(CapabilityLost) as lost:
        _ = await rig.claim()
    assert lost.value.changed == (f"skill:{SKILL}",)


async def test_skp1_engine_an_edit_keeps_the_admitted_skill_and_a_deletion_stops_it_at_the_next_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.harness.pinning.support import EngineRig

    rig = await Rig.admitted(monkeypatch)
    _ = await rig.author().update_skill(SKILL, skill_md(SKILL, "File everything, to everyone."))
    _ = await EngineRig(rig.services, rig.spec).attempt()

    assert await rig.author().delete_skill(SKILL)

    with pytest.raises(CapabilityLost) as lost:
        _ = await EngineRig(rig.services, rig.spec).attempt()
    assert lost.value.changed == (f"skill:{SKILL}",)


async def test_skp1_engine_a_withdrawn_grant_stops_the_skill_at_the_next_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.harness.pinning.support import EngineRig

    rig = await Rig.admitted(monkeypatch)
    await rig.narrow("unassign")

    with pytest.raises(CapabilityLost) as lost:
        _ = await EngineRig(rig.services, rig.spec).attempt()
    assert lost.value.changed == (f"skill:{SKILL}",)
