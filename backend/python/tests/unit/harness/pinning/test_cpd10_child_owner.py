"""CPD10-61 (assigned item 4): the child of CPD10-60 on the worker, from the bytes Node produced.

``contracts/control-plane/vectors/child-owner-pin.json`` holds the child's spec, pin included, exactly as Node's
admission and ``ChildRunService.spawn`` wrote it for a migrated org whose agent's owner shared a skill through
the agent (``cpd10-child-owner.test.ts`` writes and compares it). Here those bytes are admitted on the pinning
rig and run by the production assembler: the child of the owner's agent loads the owner's skill, and the child
of an agent without an owner is offered no skill.

Real: Node's spec and pin bytes, the worker's contract validation and pin digest check, the production
assembler building the child's attempt (its pinned policy read from Node's pin, its authority rule, the skills
source, the skill item runtime at the pinned bundle digest) and the ``load_skill`` tool of the built runtime, over
a skill store holding the skill as its owner authored it. Faked: the graph provider and the configuration store
(in memory) and the model (scripted, never called). Not run here: the kernel's turn loop and the journal's child
commit, which the in-memory journal only accepts for a child it spawned itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.config.constants.arangodb import CollectionNames
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from app.harness.assembly.items.pins import offer, set_is_authority
from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import CapabilityLost
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.skills.types import SkillRef
from harness_core.testing import ManualClock, ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.support.config_service import RIG_PLATFORM_FLAGS
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider
from tests.unit.harness.assembly import world as w

from .support import ControlPlane, run_policies

if TYPE_CHECKING:
    from harness_core.driver import AssembledRuntime

VECTOR: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts" / "control-plane" / "vectors" / "child-owner-pin.json").read_text(
        encoding="utf-8"
    )
)
SKILL: dict[str, str] = VECTOR["skill"]
CASE = {case["id"]: case for case in VECTOR["cases"]}
OWNER = "u1"
CHILD = "run-child"
SKILL_COLLECTIONS = frozenset({CollectionNames.AGENT_SKILLS.value, CollectionNames.AGENT_SKILL_VERSIONS.value})
_CTX = ToolContext(CHILD, w.ORG, "t1.0", "att-1", "k")


def skill_md() -> str:
    return f"---\nname: {SKILL['name']}\ndescription: Use when writing up a meeting.\n---\n\n{SKILL['body']}\n"


@pytest.fixture(autouse=True)
def _skills_on(monkeypatch: pytest.MonkeyPatch, assembly_world: None) -> None:
    # After the world's own environment, which turns skills off.
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")


@dataclass
class ChildRig:
    """The pinning rig's worker (its services, control plane and policies) with the org's skills in an in-memory
    graph, holding the skill as its owner authored it."""

    services: w.Services
    skills: FakeGraphProvider
    assembler: PipesHubRuntimeAssembler

    @classmethod
    async def with_the_owner_s_skill(cls) -> ChildRig:
        services, skills = w.Services(), FakeGraphProvider()
        control = ControlPlane(services, ManualClock())
        services.config.put(PLATFORM_SETTINGS_KEY, {"featureFlags": {**RIG_PLATFORM_FLAGS, "ENABLE_SKILLS": True}})
        elsewhere = services.graph.get_document

        async def get_document(
            key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False,
        ) -> dict[str, Any] | None:
            source = skills.get_document if collection in SKILL_COLLECTIONS else elsewhere
            return await source(key, collection)

        services.graph.get_document = get_document
        for method in (
            "get_nodes_by_filters", "batch_upsert_nodes", "update_node", "update_node_if_match", "delete_nodes",
            "batch_create_edges", "delete_edges_from", "get_edges_to_node",
        ):
            setattr(services.graph, method, getattr(skills, method))
        _ = await GraphSkillStore(skills, w.ORG, OWNER).create_skill(SKILL["name"], skill_md())  # type: ignore[arg-type]
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="cpd10-61", turns=[Turn(reply=Reply(text="ok"))])))
        assembler = PipesHubRuntimeAssembler(
            services, model_factory=w.model_factory(port), policies=run_policies(services, control)
        )
        return cls(services, skills, assembler)

    async def build(self, case: str) -> AssembledRuntime:
        """The child's attempt, built from the spec Node wrote."""
        return await self.assembler.build(w.claim(child_spec(case), run_id=CHILD))  # type: ignore[arg-type]


def child_spec(case: str) -> RunSpec:
    return validate_contract(RunSpec, CASE[case]["spec"])


async def load_skill(runtime: AssembledRuntime) -> str:
    result = await runtime.tools.execute(ToolCall(id="c1", name="load_skill", arguments={"name": SKILL["name"]}), _CTX)
    return str(result.content)


async def test_cpd10_61_the_vector_pins_the_skill_at_the_digest_the_worker_s_store_computes() -> None:
    rig = await ChildRig.with_the_owner_s_skill()
    digest = await GraphSkillSource(rig.skills).content_digest(w.ORG, SkillRef(SKILL["name"]))  # type: ignore[arg-type]
    assert digest == SKILL["bundleDigest"]


def test_cpd10_61_the_child_s_pin_is_read_as_node_wrote_it() -> None:
    run = RunContext.from_claim(w.claim(child_spec("owner"), run_id=CHILD))
    assert run.resolved is not None
    assert run.resolved.scope.agent_owner == OWNER
    assert set_is_authority(run)
    offered = offer(run)
    assert offered is not None
    assert offered.skills == (SKILL["name"],)
    assert [(item.installation.root, item.state) for item in offered.items] == [(f"skill_{SKILL['name']}", "ready")]


async def test_cpd10_61_the_owner_shared_skill_loads_for_the_child_on_the_production_assembler() -> None:
    rig = await ChildRig.with_the_owner_s_skill()

    runtime = await rig.build("owner")

    try:
        assert SKILL["body"] in await load_skill(runtime)
    finally:
        await runtime.close()


async def test_cpd10_61_a_skill_edited_since_the_child_was_pinned_is_served_at_its_pinned_content() -> None:
    # SKP1 (FB-11): an edit since admission keeps the skill; the run reads the admitted content from history.
    rig = await ChildRig.with_the_owner_s_skill()
    _ = await GraphSkillStore(rig.skills, w.ORG, OWNER).update_skill(  # type: ignore[arg-type]
        SKILL["name"], skill_md().replace(SKILL["body"], "Write everything down.")
    )

    runtime = await rig.build("owner")

    try:
        served = await load_skill(runtime)
        assert SKILL["body"] in served
        assert "Write everything down." not in served
    finally:
        await runtime.close()


async def test_cpd10_61_the_child_of_an_agent_without_an_owner_is_offered_no_skill() -> None:
    run = RunContext.from_claim(w.claim(child_spec("no-owner"), run_id=CHILD))
    assert run.resolved is not None
    assert run.resolved.scope.agent_owner is None
    assert [(item.state, item.reason) for item in run.resolved.effective_set.items] == [("unavailable", "not_visible")]
    offered = offer(run)
    assert offered is not None
    assert (offered.skills, offered.items) == ((), ())
    rig = await ChildRig.with_the_owner_s_skill()

    runtime = await rig.build("no-owner")

    try:
        assert SKILL["body"] not in await load_skill(runtime)
    finally:
        await runtime.close()


async def test_cpd10_61_the_owner_s_child_spec_under_the_no_owner_pin_never_starts() -> None:
    """The spec still names the skill, the pin holds it not visible: the worker trusts the pin (CPD10-50)."""
    spec = cast("dict[str, Any]", dump_contract(child_spec("owner")))
    spec["extensions"]["x.pipeshub"]["resolved"] = CASE["no-owner"]["spec"]["extensions"]["x.pipeshub"]["resolved"]
    rig = await ChildRig.with_the_owner_s_skill()

    with pytest.raises(CapabilityLost) as lost:
        _ = await rig.assembler.build(w.claim(validate_contract(RunSpec, spec), run_id=CHILD))  # type: ignore[arg-type]

    assert lost.value.missing == (f"item:skill_{SKILL['name']}:not_visible",)
