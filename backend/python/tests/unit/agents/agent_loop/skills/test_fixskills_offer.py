"""FIX-SKILLS: a run is offered the skills its request can use, pinned in its spec, on the loop and every engine.

The default chat handed a PDF report to a Claude Code child that mounted no skill (``engine.assembly skills=0``):
the assistant's run definition granted none, and the run spec, which every engine mounts from, carried none. The
loop meanwhile read the builtins live. Here the run definition grants the offer (``skills.offer``), Node pins it as
``x.pipeshub.skills`` (``run-spec.ts`` maps the definition's skills one for one), and the engine mounts the pins.
Over the real ``GraphSkillStore``, seeder and bundle builder on the in-memory graph fake; no model is called.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.edition_config  # noqa: F401  (edition seams load before any router)
from app.agent_loop_lib.modules.providers.skills.base import SkillStatus
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY
from app.agents.agent_loop.skills.bundle_source import (
    GraphSkillSource,
    PinnedSkills,
    SkillWithdrawn,
)
from app.agents.agent_loop.skills.graph_store import GraphSkillStore, SkillVisibility
from app.agents.agent_loop.skills.manager_factory import (
    get_builtin_seeder,
    reset_builtin_reconcile_state,
)
from app.agents.run_definition import (
    ASSISTANT_AGENT_KEY,
    AgentRunDefinition,
    RunDefinitionSkill,
    pin_skill_content,
    with_offered_skills,
)
from app.harness.assembly.tool_sources import SkillsSource
from app.harness.engines.durable import _pins  # pyright: ignore[reportPrivateUsage]
from app.harness.skills.bundles import SkillBundleService
from app.harness.skills.pinned_labels import mounted_skills, mounted_skills_instructions
from harness_core.sandbox.types import LeaseRef
from harness_core.skills import TarSkillMaterializer, skills_mount
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeContractEnv, FakeSandboxBroker
from harness_core.testing.sandbox.contract import run as run_in
from harness_core.testing.sandbox.contract import with_mounts
from harness_core.tools.registry import ToolRegistry
from harness_core.workspace import ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
)
from tests.support.bound_org import bind_test_org
from tests.support.config_service import FakeConfigurationService
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG, SESSION = "org-1", "ses-1"
ME = SimpleNamespace(user_id="user-me", key="key-me")
COLLEAGUE = SimpleNamespace(user_id="user-col", key="key-col")
VIEWERS = (ME.user_id, ME.key)


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    yield from bind_test_org(ORG)


@pytest.fixture(autouse=True)
def _fresh_reconcile() -> Iterator[None]:
    reset_builtin_reconcile_state()
    yield
    reset_builtin_reconcile_state()


def _md(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\nDo the {name} steps.\n"


def _builtins() -> set[str]:
    """The packs the model may load itself; a command-only pack (``/summarize``) is the person's to invoke."""
    seeder = get_builtin_seeder()
    assert seeder is not None
    return {pack.metadata.name for pack in seeder.packs if pack.metadata.invocation_model}


async def _world() -> FakeGraphProvider:
    graph = FakeGraphProvider()
    _ = await GraphSkillStore(graph, ORG, ME.key).create_skill("my-notes", _md("my-notes", "My meeting notes"))  # type: ignore[arg-type]
    _ = await GraphSkillStore(graph, ORG, COLLEAGUE.key).create_skill("col-secret", _md("col-secret", "Not mine"))  # type: ignore[arg-type]
    return graph


async def _disable(graph: FakeGraphProvider, name: str) -> None:
    store = GraphSkillStore(  # type: ignore[arg-type]
        graph, ORG, SEED_IDENTITY, visibility=SkillVisibility.everyone(), allow_builtin_writes=True
    )
    assert await store.set_skill_status(name, SkillStatus.DISABLED, from_status=SkillStatus.ACTIVE)


def _assistant(*skills: RunDefinitionSkill) -> AgentRunDefinition:
    return AgentRunDefinition(agentKey=ASSISTANT_AGENT_KEY, definitionVersion=1, skills=skills)


async def _pinned(graph: FakeGraphProvider, definition: AgentRunDefinition) -> AgentRunDefinition:
    offered = await with_offered_skills(definition, graph, ORG, VIEWERS, enabled=True)
    return await pin_skill_content(offered, GraphSkillSource(graph), ORG)  # type: ignore[arg-type]


def _spec_skills(definition: AgentRunDefinition) -> list[dict[str, str]]:
    """``x.pipeshub.skills`` as Node pins it from the definition (``run-spec.ts``: name and bundle digest, by name)."""
    return sorted(
        ({"name": s.name, "bundleDigest": s.bundle_digest} for s in definition.skills if s.bundle_digest),
        key=lambda s: s["name"],
    )


def _claim(skills: list[dict[str, str]]) -> SimpleNamespace:
    return SimpleNamespace(
        org_id=ORG, spec=SimpleNamespace(extensions={"x.pipeshub": {"skills": skills}}), journal_accepts=lambda _f: True
    )


_LOG = SimpleNamespace(info=lambda *_a, **_k: None, debug=lambda *_a, **_k: None, warning=lambda *_a, **_k: None)


class TestTheOffer:
    async def test_a_default_chat_run_is_offered_the_builtin_packs_and_my_own_skills_by_digest(self) -> None:
        graph = await _world()

        definition = await _pinned(graph, _assistant())

        names = {s.name for s in definition.skills}
        assert names == _builtins() | {"my-notes"}
        assert {"pdf", "docx", "pptx", "xlsx"} <= names
        assert all(s.bundle_digest and s.bundle_digest.startswith("sha256:") for s in definition.skills)

    async def test_a_disabled_pack_is_not_offered(self) -> None:
        graph = await _world()
        _ = await _pinned(graph, _assistant())  # seeds the builtins
        await _disable(graph, "pdf")

        definition = await _pinned(graph, _assistant())

        assert "pdf" not in {s.name for s in definition.skills}
        assert "docx" in {s.name for s in definition.skills}

    async def test_skills_off_for_the_org_offers_none(self) -> None:
        graph = await _world()
        assigned = AgentRunDefinition(agentKey="a", definitionVersion=1, skills=(RunDefinitionSkill(name="my-notes"),))

        definition = await with_offered_skills(assigned, graph, ORG, VIEWERS, enabled=False)

        assert definition.skills == ()

    async def test_an_agent_adds_its_own_skills_to_the_builtins_and_replaces_mine(self) -> None:
        graph = await _world()
        agent = AgentRunDefinition(agentKey="a", definitionVersion=1, skills=(RunDefinitionSkill(name="col-secret"),))

        definition = await _pinned(graph, agent)

        assert {s.name for s in definition.skills} == _builtins() | {"col-secret"}
        assert definition.skills[0].name == "col-secret", "the agent's own come first, as it lists them"


class TestTheEngineMountsThePins:
    async def test_a_default_chat_engine_run_mounts_the_pdf_pack_and_is_told_of_it(self) -> None:
        graph = await _world()
        spec_skills = _spec_skills(await _pinned(graph, _assistant()))
        claim = _claim(spec_skills)

        pins = _pins(claim)  # type: ignore[arg-type]
        mounted = await mounted_skills(pins, graph, claim, _LOG)  # type: ignore[arg-type]

        assert {p.name for p in mounted.pins} == _builtins() | {"my-notes"}
        told = mounted_skills_instructions(mounted)
        assert "\n- pdf: " in told and "ph skill show <name>" in told
        assert "Do the" not in told, "a skill's body stays in its folder"

        clock = ManualClock(datetime(2026, 11, 10, tzinfo=UTC))
        objects, workspaces = InMemoryBinaryObjectStore(), InMemoryWorkspaceStore(clock=clock)
        bundles = SkillBundleService(
            source=GraphSkillSource(graph), objects=objects, workspaces=workspaces, session_id=SESSION  # type: ignore[arg-type]
        )
        scope = ObjectScope(ORG, f"ws:{SESSION}")
        resolved = [await bundles.resolve(scope, pin) for pin in mounted.pins]
        env = FakeContractEnv(clock)
        sandbox = await FakeSandboxBroker(clock=clock).acquire(
            with_mounts(env.spec(), skills_mount("/skills")), lease=LeaseRef("w", 1), idempotency_key="fixskills"
        )
        await TarSkillMaterializer(objects=objects, workspaces=workspaces).materialize(
            scope, sandbox, resolved, skills_dir="/skills"
        )
        out, _, code = await run_in(await sandbox.exec(env.argv_read_file("/skills/pdf/SKILL.md"), env={}))
        assert code == 0 and b"name: pdf" in out

    async def test_an_older_spec_without_pins_mounts_nothing_and_says_nothing(self) -> None:
        mounted = await mounted_skills(_pins(_claim([])), FakeGraphProvider(), _claim([]), _LOG)  # type: ignore[arg-type]
        assert mounted.pins == () and mounted_skills_instructions(mounted) == ""


class TestThePinsHold:
    async def test_a_builtin_and_my_own_skill_hold_for_the_assistant_run_a_colleagues_does_not(self) -> None:
        graph = await _world()
        pinned = {s.name: s.bundle_digest for s in (await _pinned(graph, _assistant())).skills}
        colleague = await GraphSkillSource(graph).content_digest(ORG, SimpleNamespace(name="col-secret", version=None))  # type: ignore[arg-type]
        reader = PinnedSkills(graph, ORG, ASSISTANT_AGENT_KEY, viewers=VIEWERS)  # type: ignore[arg-type]

        assert await reader.files("pdf", pinned["pdf"] or "")
        assert await reader.files("my-notes", pinned["my-notes"] or "")
        with pytest.raises(SkillWithdrawn):
            _ = await reader.files("col-secret", colleague or "")

    async def test_a_pack_disabled_after_admission_is_withdrawn(self) -> None:
        graph = await _world()
        pinned = {s.name: s.bundle_digest for s in (await _pinned(graph, _assistant())).skills}
        await _disable(graph, "pdf")

        with pytest.raises(SkillWithdrawn):
            _ = await PinnedSkills(graph, ORG, ASSISTANT_AGENT_KEY, viewers=VIEWERS).files("pdf", pinned["pdf"] or "")  # type: ignore[arg-type]


class TestTheLoopOffersThemToo:
    async def test_a_default_chat_loop_run_lists_and_loads_the_pinned_pdf_pack(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
        graph = await _world()
        definition = await _pinned(graph, _assistant())
        skills = tuple(s.name for s in definition.skills)
        context = AgentContext(
            org_id=ORG,
            user_id=ME.user_id,
            user_email="me@example.com",
            user_info={"userId": ME.user_id, "orgId": ORG, "_key": ME.key},
            graph_provider=graph,
            config_service=FakeConfigurationService(),
            # The durable worker sets the agent's skills from the pin (``tool_environment``).
            agent_skills=list(skills),
        )
        run = SimpleNamespace(
            org_id=ORG,
            run_id="run-1",
            session_id=SESSION,
            agent=SimpleNamespace(agent_key=ASSISTANT_AGENT_KEY),
            skills=skills,
            skill_digests={s.name: s.bundle_digest for s in definition.skills},
        )
        source = SkillsSource()

        tools = await source.load(SimpleNamespace(context=context), run, ToolRegistry())  # type: ignore[arg-type]

        assert tools
        manager: Any = source.manager
        catalog = {m.name for m in manager.catalog_snapshot()}
        assert catalog == _builtins() | {"my-notes"}
        assert source.missing(run) == []  # type: ignore[arg-type]
        assert (await manager.activate_skill("pdf")).metadata.name == "pdf"


class TestTheRoute:
    async def test_the_assistant_definition_grants_the_offer_by_digest(self) -> None:
        from app.api.routes.agent import get_agent_run_definition

        graph = await _world()
        request = MagicMock()
        request.state.user = {"orgId": ORG, "userId": ME.user_id, "scopes": ["agent:execute"]}
        services = {"graph_provider": graph, "config_service": AsyncMock(), "logger": MagicMock()}
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"orgId": ORG, "userId": ME.user_id, "isServiceAccount": False}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": ME.key}), \
             patch("app.api.routes.agent.get_assistant_agent", new_callable=AsyncMock, return_value=({}, None)), \
             patch("app.api.routes.agent.resolve_web_search", new_callable=AsyncMock, return_value=None), \
             patch("app.api.routes.agent._skills_on", new_callable=AsyncMock, return_value=True), \
             patch("app.services.featureflag.platform_settings.is_actions_enabled", new_callable=AsyncMock, return_value=True), \
             patch("app.agents.mcp.service.is_mcp_enabled", new_callable=AsyncMock, return_value=False):
            response = await get_agent_run_definition(request, ASSISTANT_AGENT_KEY)

        skills = json.loads(response.body)["definition"]["skills"]
        assert {s["name"] for s in skills} == _builtins() | {"my-notes"}
        assert all(s["bundleDigest"] for s in skills)
