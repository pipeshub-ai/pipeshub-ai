"""Creating a skill never replaces an existing one, and only the right caller changes it.

A skill is stored under `{org}_{name}`. A create that finds the key taken must be
refused whoever holds it, including a co-worker whose skill the caller cannot see.
The refusal comes from one conditional insert, so two creators of one name cannot
both win. Content edits need the creator; availability changes and delete need the
creator or an administrator the calling route has vetted; only the seeder stores a
builtin; a name that is created again starts a history of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
from typing import Any

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    SkillAlreadyExistsError,
    SkillConflictError,
    SkillNameReservedError,
    SkillPermissionError,
    SkillSource,
    SkillStatus,
)
from app.agents.agent_loop.skills.graph_store import GraphSkillStore, SharedSkillRights
from tests.support.skill_stores import skill_store
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

_SEED = "system:builtin-seeder"

_OWNER_MD = """---
name: deploy-service
description: Use when deploying a service to the cluster
metadata:
  agent-loop:
    requires:
      - base-skill
    related:
      - other-skill
---

Owner step 1. Build the image.
Owner step 2. Push it.
"""

_OTHER_MD = """---
name: deploy-service
description: Use when deploying a service, with different steps
---

Other step 1. Do something else entirely.
"""

_BASE_MD = """---
name: base-skill
description: Use when preparing the base image
---

Base body.
"""

_BUILTIN_CLAIM_MD = """---
name: deploy-service
description: Use when deploying a service
metadata:
  agent-loop:
    source: builtin
    pack_name: deploy-service
    pack_version: 9.9.9
---

Claimed body. Push it twice.
"""

_NAME = "deploy-service"
_KEY = "org-1_deploy-service"


def _snapshot(graph: FakeGraphProvider) -> dict[str, Any]:
    return copy.deepcopy({"nodes": graph._collections, "edges": graph._edges})


async def _seed_owner_skill(graph: FakeGraphProvider) -> GraphSkillStore:
    owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
    await owner.create_skill("base-skill", _BASE_MD)
    await owner.create_skill(_NAME, _OWNER_MD, resources={"notes.md": "owner notes"})
    await owner.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Push it twice."))
    return owner


def _other(graph: FakeGraphProvider, *, scoped: bool, admin: bool = False) -> GraphSkillStore:
    return skill_store(
        graph, "org-1", "user-b", visibility_scope="user-b" if scoped else None, is_admin=admin,
    )


async def _seeded_builtin(graph: FakeGraphProvider, name: str = _NAME) -> None:
    seeder = skill_store(graph, "org-1", _SEED)
    await seeder.create_skill(name, _BUILTIN_CLAIM_MD.replace("deploy-service", name))


class TestCrossUserCreateIsRefused:
    @pytest.mark.parametrize("scoped", [True, False], ids=["management-view", "runtime-view"])
    async def test_same_name_create_is_refused_and_the_existing_skill_is_untouched(self, scoped: bool) -> None:
        graph = FakeGraphProvider()
        await _seed_owner_skill(graph)
        before = _snapshot(graph)

        with pytest.raises(SkillAlreadyExistsError):
            await _other(graph, scoped=scoped).create_skill(_NAME, _OTHER_MD)

        assert _snapshot(graph) == before
        doc = graph._col("agentSkills")[_KEY]
        assert doc["createdBy"] == "user-a"
        assert "Owner step 1" in doc["content"]
        assert doc["version"] == "1.0.1"

    @pytest.mark.parametrize("scoped", [True, False], ids=["management-view", "runtime-view"])
    async def test_refusal_does_not_reveal_the_other_skill(self, scoped: bool) -> None:
        graph = FakeGraphProvider()
        await _seed_owner_skill(graph)

        with pytest.raises(SkillAlreadyExistsError) as exc:
            await _other(graph, scoped=scoped).create_skill(_NAME, _OTHER_MD)

        message = str(exc.value)
        for hidden in ("user-a", "Owner step", "owner notes", "base-skill", "other-skill"):
            assert hidden not in message

    async def test_versions_and_edges_survive_a_refused_create(self) -> None:
        graph = FakeGraphProvider()
        owner = await _seed_owner_skill(graph)
        edges_before = copy.deepcopy(graph._edges)

        with pytest.raises(SkillAlreadyExistsError):
            await _other(graph, scoped=True).create_skill(
                _NAME, _OTHER_MD.replace("---\n\nOther", "metadata:\n  agent-loop:\n    requires: [elsewhere]\n---\n\nOther"),
            )

        assert graph._edges == edges_before
        assert [v.version for v in await owner.list_versions(_NAME)] == ["1.0.0"]
        owners = [e for e in graph._edges["permission"] if e["to_id"] == _KEY]
        assert [e["from_id"] for e in owners] == ["user-a"]
        relation_targets = {e["to_id"] for e in graph._edges["agentSkillRelation"] if e["from_id"] == _KEY}
        assert relation_targets == {"org-1_base-skill", "org-1_other-skill"}

    async def test_same_user_second_create_is_still_refused(self) -> None:
        graph = FakeGraphProvider()
        owner = await _seed_owner_skill(graph)
        with pytest.raises(SkillAlreadyExistsError):
            await owner.create_skill(_NAME, _OTHER_MD)

    async def test_other_org_may_use_the_same_name(self) -> None:
        graph = FakeGraphProvider()
        await _seed_owner_skill(graph)
        await skill_store(graph, "org-2", "user-c").create_skill(_NAME, _OTHER_MD)
        assert "Owner step 1" in graph._col("agentSkills")[_KEY]["content"]


class _RacingGraph(FakeGraphProvider):
    """Both creators read the key as free before either writes."""

    def __init__(self) -> None:
        super().__init__()
        self.arrived = 0
        self.both_read = asyncio.Event()

    async def get_document(self, document_key: str, collection: str, transaction: str | None = None) -> dict | None:
        doc = await super().get_document(document_key, collection, transaction)
        if collection == "agentSkills" and doc is None:
            self.arrived += 1
            if self.arrived >= 2:
                self.both_read.set()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.both_read.wait(), timeout=0.5)
        return doc


class TestConcurrentCreates:
    async def test_exactly_one_of_two_concurrent_creates_wins(self) -> None:
        graph = _RacingGraph()
        results = await asyncio.gather(
            skill_store(graph, "org-1", "user-a").create_skill(_NAME, _OWNER_MD),
            skill_store(graph, "org-1", "user-b").create_skill(_NAME, _OTHER_MD),
            return_exceptions=True,
        )

        winners = [r for r in results if not isinstance(r, BaseException)]
        losers = [r for r in results if isinstance(r, SkillAlreadyExistsError)]
        assert len(winners) == 1 and len(losers) == 1, results

        doc = graph._col("agentSkills")[_KEY]
        winner_id = "user-a" if "Owner step" in doc["content"] else "user-b"
        assert doc["createdBy"] == winner_id
        owners = [e["from_id"] for e in graph._edges["permission"] if e["to_id"] == _KEY]
        assert owners == [winner_id]


_NON_OWNER_CONTENT_WRITES = {
    "update": lambda s: s.update_skill(_NAME, _OTHER_MD),
    "patch": lambda s: s.patch_skill(_NAME, "Push it twice.", "Push it differently."),
    "rollback": lambda s: s.rollback(_NAME, "1.0.0"),
    "deprecate": lambda s: s.deprecate_skill(_NAME, "gone"),
    "write_resource": lambda s: s.write_resource(_NAME, "notes.md", "replaced"),
    "remove_resource": lambda s: s.remove_resource(_NAME, "notes.md"),
}
_NON_OWNER_MODERATION = {
    "delete": lambda s: s.delete_skill(_NAME),
    "disable": lambda s: s.set_skill_status(_NAME, SkillStatus.DISABLED),
    # `SkillManager.delete(detach=True)`: detach writes nothing, the delete is refused.
    "detach_and_delete": lambda s: _detach_then_delete(s),
}


class TestOwnershipRule:
    @pytest.mark.parametrize("action", sorted(_NON_OWNER_CONTENT_WRITES))
    async def test_a_non_owner_cannot_edit_content_on_the_runtime_view(self, action: str) -> None:
        graph = FakeGraphProvider()
        await _seed_owner_skill(graph)
        before = _snapshot(graph)

        with pytest.raises(SkillPermissionError):
            await _NON_OWNER_CONTENT_WRITES[action](_other(graph, scoped=False))
        assert _snapshot(graph) == before

    @pytest.mark.parametrize("action", sorted(_NON_OWNER_CONTENT_WRITES))
    async def test_an_admin_cannot_edit_a_members_content(self, action: str) -> None:
        graph = FakeGraphProvider()
        await _seed_owner_skill(graph)
        before = _snapshot(graph)

        with pytest.raises(SkillPermissionError):
            await _NON_OWNER_CONTENT_WRITES[action](_other(graph, scoped=False, admin=True))
        assert _snapshot(graph) == before

    @pytest.mark.parametrize("action", sorted(_NON_OWNER_MODERATION))
    async def test_a_non_owner_cannot_change_availability_or_delete(self, action: str) -> None:
        graph = FakeGraphProvider()
        await _seed_owner_skill(graph)
        before = _snapshot(graph)

        with pytest.raises(SkillPermissionError):
            await _NON_OWNER_MODERATION[action](_other(graph, scoped=False))
        assert _snapshot(graph) == before

    async def test_an_admin_the_route_vetted_can_disable_and_delete_any_skill(self) -> None:
        graph = FakeGraphProvider()
        await _seed_owner_skill(graph)
        admin = _other(graph, scoped=False, admin=True)

        assert await admin.set_skill_status(_NAME, SkillStatus.DISABLED) is True
        assert graph._col("agentSkills")[_KEY]["status"] == "disabled"
        assert await admin.delete_skill(_NAME) is True
        assert _KEY not in graph._col("agentSkills")

    async def test_the_owner_can_update_roll_back_and_delete(self) -> None:
        graph = FakeGraphProvider()
        owner = await _seed_owner_skill(graph)

        await owner.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Push it thrice."))
        await owner.rollback(_NAME, "1.0.0")
        assert await owner.write_resource(_NAME, "extra.md", "more") is True
        assert await owner.set_skill_status(_NAME, SkillStatus.DISABLED) is True
        assert await owner.delete_skill(_NAME) is True
        assert _KEY not in graph._col("agentSkills")

    async def test_the_owner_on_the_runtime_view_can_still_write(self) -> None:
        graph = FakeGraphProvider()
        await _seed_owner_skill(graph)
        runtime_owner = skill_store(graph, "org-1", "user-a")
        await runtime_owner.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Push it again."))
        assert await runtime_owner.delete_skill(_NAME) is True

    async def test_a_skill_that_says_it_is_builtin_is_still_owned_by_its_creator(self) -> None:
        """`source` comes from the SKILL.md, so it grants nothing."""
        graph = FakeGraphProvider()
        await _seed_owner_skill(graph)
        graph._col("agentSkills")[_KEY]["source"] = "builtin"
        before = _snapshot(graph)

        with pytest.raises(SkillPermissionError):
            await _other(graph, scoped=False).update_skill(_NAME, _OTHER_MD)
        assert _snapshot(graph) == before


class TestSeededBuiltinsBelongToTheSeeder:
    @pytest.mark.parametrize("action", sorted(_NON_OWNER_CONTENT_WRITES) + sorted(_NON_OWNER_MODERATION))
    async def test_an_agent_run_as_a_member_cannot_change_a_builtin(self, action: str) -> None:
        graph = FakeGraphProvider()
        await _seeded_builtin(graph)
        before = _snapshot(graph)
        actions = {**_NON_OWNER_CONTENT_WRITES, **_NON_OWNER_MODERATION}

        with pytest.raises(SkillPermissionError):
            await actions[action](_other(graph, scoped=False))
        assert _snapshot(graph) == before

    async def test_a_vetted_admin_can_mute_one_and_only_the_seeder_edits_it(self) -> None:
        graph = FakeGraphProvider()
        await _seeded_builtin(graph)

        admin = _other(graph, scoped=True, admin=True)
        assert await admin.set_skill_status(_NAME, SkillStatus.DISABLED) is True
        await skill_store(graph, "org-1", _SEED).update_skill(_NAME, _BUILTIN_CLAIM_MD.replace("Claimed", "Upgraded"))
        doc = graph._col("agentSkills")[_KEY]
        assert doc["source"] == "builtin" and doc["packVersion"] == "9.9.9"
        with pytest.raises(SkillPermissionError):
            await admin.update_skill(_NAME, _BUILTIN_CLAIM_MD.replace("Claimed", "Edited by an admin"))

    async def test_a_builtin_stays_visible_to_a_scoped_member(self) -> None:
        graph = FakeGraphProvider()
        await _seeded_builtin(graph)
        assert await _other(graph, scoped=True).get_skill(_NAME) is not None


class TestOnlyTheSeederStoresABuiltin:
    @pytest.mark.parametrize("scoped", [True, False], ids=["management-view", "runtime-view"])
    async def test_create_refuses_the_builtin_claim_and_drops_the_pack(self, scoped: bool) -> None:
        graph = FakeGraphProvider()
        with pytest.raises(RegistryError):
            await _other(graph, scoped=scoped).create_skill(_NAME, _BUILTIN_CLAIM_MD)
        assert graph._col("agentSkills") == {}

        await _other(graph, scoped=scoped).create_skill(_NAME, _BUILTIN_CLAIM_MD.replace("source: builtin", "source: manual"))
        doc = graph._col("agentSkills")[_KEY]
        assert doc["packName"] is None and doc["packVersion"] is None
        assert "9.9.9" not in doc["content"]

    async def test_a_claim_stored_before_this_is_an_ordinary_skill_of_its_creator(self) -> None:
        graph = FakeGraphProvider()
        owner = await _seed_owner_skill(graph)
        graph._col("agentSkills")[_KEY].update(source="builtin", packName=_NAME)
        skill = await owner.get_skill(_NAME)
        assert skill is not None and skill.metadata.source == SkillSource.MANUAL
        assert await _other(graph, scoped=True).get_skill(_NAME) is None
        await owner.update_skill(_NAME, _OTHER_MD)
        assert graph._col("agentSkills")[_KEY]["source"] == "manual"

    async def test_a_claimed_builtin_is_not_shown_to_a_co_worker(self) -> None:
        graph = FakeGraphProvider()
        await _other(graph, scoped=True).create_skill(_NAME, _OTHER_MD)
        graph._col("agentSkills")[_KEY]["source"] = "builtin"
        assert await skill_store(graph, "org-1", "user-c", visibility_scope="user-c").list_skills() == []

    async def test_update_and_rollback_drop_the_claim(self) -> None:
        graph = FakeGraphProvider()
        owner = skill_store(graph, "org-1", "user-a")
        await owner.create_skill(_NAME, _OWNER_MD)
        with pytest.raises(RegistryError):
            await owner.update_skill(_NAME, _BUILTIN_CLAIM_MD)
        await owner.update_skill(_NAME, _BUILTIN_CLAIM_MD.replace("source: builtin", "source: manual"))
        assert graph._col("agentSkills")[_KEY]["source"] == "manual"
        assert graph._col("agentSkills")[_KEY]["packVersion"] is None

        graph._col("agentSkills")[_KEY]["source"] = "builtin"
        await owner.rollback(_NAME, "1.0.0")
        assert graph._col("agentSkills")[_KEY]["source"] == "manual"

    async def test_the_seeder_still_stores_one(self) -> None:
        graph = FakeGraphProvider()
        await _seeded_builtin(graph)
        doc = graph._col("agentSkills")[_KEY]
        assert doc["source"] == "builtin" and doc["packName"] == "deploy-service"

    async def test_a_pack_name_is_reserved_for_the_seeder(self) -> None:
        graph = FakeGraphProvider()
        reserved = frozenset({_NAME})
        member = skill_store(graph, "org-1", "user-a", reserved_names=lambda: reserved)
        with pytest.raises(SkillNameReservedError):
            await member.create_skill(_NAME, _OWNER_MD)
        assert graph._col("agentSkills") == {}

        await skill_store(graph, "org-1", _SEED, reserved_names=lambda: reserved).create_skill(_NAME, _BUILTIN_CLAIM_MD)
        assert _KEY in graph._col("agentSkills")


class _Clock:
    """A settable clock for the store, so a test controls every timestamp it writes."""

    def __init__(self) -> None:
        self.now = 1_000_000

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    import app.agents.agent_loop.skills.graph_store as graph_store_module

    fake = _Clock()
    monkeypatch.setattr(graph_store_module, "get_epoch_timestamp_in_ms", fake)
    return fake


def _strip_generations(graph: FakeGraphProvider) -> None:
    """Make the stored skills and revisions look as they did before generations existed."""
    for collection in ("agentSkills", "agentSkillVersions"):
        for doc in graph._col(collection).values():
            doc.pop("generation", None)


class TestANameCreatedAgainStartsANewHistory:
    @pytest.mark.parametrize("skew_ms", [0, 1, 250, 2_000, 1_000_000], ids=lambda ms: f"clock-{ms}ms-behind")
    async def test_the_new_owner_does_not_see_or_restore_the_old_revisions(self, clock: _Clock, skew_ms: int) -> None:
        graph = FakeGraphProvider()
        old = skill_store(graph, "org-1", "user-a")
        await old.create_skill(_NAME, _OWNER_MD)
        clock.now += 5
        await old.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Old private text."))
        clock.now += 5
        assert await old.delete_skill(_NAME) is True
        archived = copy.deepcopy(graph._col("agentSkillVersions"))
        assert archived

        # The new owner is served by a replica whose clock runs behind the old owner's.
        clock.now -= skew_ms
        new = skill_store(graph, "org-1", "user-b")
        await new.create_skill(_NAME, _OTHER_MD)

        assert await new.list_versions(_NAME) == []
        assert await new.get_skill(_NAME) is not None
        assert await new.get_version(_NAME, "1.0.0") is None
        with pytest.raises(RegistryError, match="not found"):
            await new.rollback(_NAME, "1.0.0")
        assert graph._col("agentSkillVersions") == archived

    @pytest.mark.parametrize("skew_ms", [0, 250], ids=lambda ms: f"clock-{ms}ms-behind")
    async def test_the_new_owners_revisions_do_not_overwrite_the_old_archive(self, clock: _Clock, skew_ms: int) -> None:
        graph = FakeGraphProvider()
        old = skill_store(graph, "org-1", "user-a")
        await old.create_skill(_NAME, _OWNER_MD)
        await old.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Old private text."))
        await old.delete_skill(_NAME)
        archived = copy.deepcopy(graph._col("agentSkillVersions"))

        clock.now -= skew_ms
        new = skill_store(graph, "org-1", "user-b")
        await new.create_skill(_NAME, _OTHER_MD)
        await new.update_skill(_NAME, _OTHER_MD.replace("entirely", "completely"))

        after = graph._col("agentSkillVersions")
        for key, doc in archived.items():
            assert after[key] == doc
        assert len(after) == len(archived) + 1
        assert [v.version for v in await new.list_versions(_NAME)] == ["1.0.0"]

    async def test_every_creation_draws_its_own_generation(self, clock: _Clock) -> None:
        graph = FakeGraphProvider()
        owner = skill_store(graph, "org-1", "user-a")
        await owner.create_skill(_NAME, _OWNER_MD)
        first = graph._col("agentSkills")[_KEY]["generation"]
        await owner.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Edited."))
        await owner.rollback(_NAME, "1.0.0")
        assert graph._col("agentSkills")[_KEY]["generation"] == first
        assert {v["generation"] for v in graph._col("agentSkillVersions").values()} == {first}

        await owner.delete_skill(_NAME)
        await owner.create_skill(_NAME, _OWNER_MD)  # same user, same millisecond
        assert graph._col("agentSkills")[_KEY]["generation"] != first
        assert await owner.list_versions(_NAME) == []


class TestHistoryWrittenBeforeGenerationsExisted:
    async def test_the_creator_still_reads_and_restores_their_own_history(self) -> None:
        graph = FakeGraphProvider()
        owner = skill_store(graph, "org-1", "user-a")
        await owner.create_skill(_NAME, _OWNER_MD)
        await owner.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Edited."))
        _strip_generations(graph)

        assert [v.version for v in await owner.list_versions(_NAME)] == ["1.0.0"]
        assert await owner.get_version(_NAME, "1.0.0") is not None
        await owner.rollback(_NAME, "1.0.0")
        await owner.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Edited again."))
        assert {v.version for v in await owner.list_versions(_NAME)} == {"1.0.0", "1.0.1", "1.0.2"}

    async def test_a_new_owner_never_gets_them(self) -> None:
        graph = FakeGraphProvider()
        old = skill_store(graph, "org-1", "user-a")
        await old.create_skill(_NAME, _OWNER_MD)
        await old.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Old private text."))
        await old.delete_skill(_NAME)
        _strip_generations(graph)

        new = skill_store(graph, "org-1", "user-b")
        await new.create_skill(_NAME, _OTHER_MD)
        assert await new.list_versions(_NAME) == []
        assert await new.get_version(_NAME, "1.0.0") is None

    async def test_another_creators_revisions_are_hidden_from_a_skill_without_a_generation(self) -> None:
        graph = FakeGraphProvider()
        old = skill_store(graph, "org-1", "user-a")
        await old.create_skill(_NAME, _OWNER_MD)
        await old.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Old private text."))
        await old.delete_skill(_NAME)
        new = skill_store(graph, "org-1", "user-b")
        await new.create_skill(_NAME, _OTHER_MD)
        _strip_generations(graph)  # a re-creation made before generations existed

        assert await new.list_versions(_NAME) == []
        assert await new.get_version(_NAME, "1.0.0") is None


class _ReplacedAfterTheRead(FakeGraphProvider):
    """Right after the store reads the skill, someone else deletes it and creates it again."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = False

    async def get_document(self, document_key: str, collection: str, transaction: str | None = None) -> dict | None:
        doc = await super().get_document(document_key, collection, transaction)
        if self.armed and collection == "agentSkills" and doc is not None:
            self.armed = False
            fresh = dict(doc, createdBy="user-b", content="---\nname: deploy-service\ndescription: Use when new\n---\n\nNew owner body.\n",
                         updatedAtTimestamp=int(doc["updatedAtTimestamp"]) + 5, createdAtTimestamp=int(doc["updatedAtTimestamp"]) + 5)
            self._col(collection)[document_key] = fresh
        return doc


class TestWritesAreConditional:
    @pytest.mark.parametrize("action", ["update", "rollback"])
    async def test_a_stale_write_cannot_replace_a_skill_created_in_between(self, action: str) -> None:
        graph = _ReplacedAfterTheRead()
        owner = await _seed_owner_skill(graph)
        graph.armed = True

        with pytest.raises((SkillConflictError, RegistryError)):
            if action == "update":
                await owner.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Push it later."))
            else:
                await owner.rollback(_NAME, "1.0.0")

        doc = graph._col("agentSkills")[_KEY]
        assert doc["createdBy"] == "user-b" and "New owner body" in doc["content"]


class _ReCreatedBeforeTheWrite(FakeGraphProvider):
    """Between the store's ownership read and its write, the skill is deleted and the
    name created again by user-b: a new generation, user-b's body and resources, user-b's
    agent, and (the worst case) the same `updatedAtTimestamp` as the doc that was read."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = False

    def _re_create(self, key: str) -> None:
        if not self.armed:
            return
        self.armed = False
        old = self._col("agentSkills")[key]
        self._col("agentSkills")[key] = dict(
            old, generation="b-generation", createdBy="user-b", updatedBy=None, version="1.0.0",
            content="---\nname: deploy-service\ndescription: Use when new\n---\n\nNew owner body.\n",
            resourcePaths=["b.md"], resourceContents=["from b"], status="active",
        )
        self._edges["agentHasSkill"] = [{
            "from_id": "agent-b", "from_collection": "agentInstances", "to_id": key, "to_collection": "agentSkills",
        }]

    async def update_node_if_match(self, key: str, collection: str, *args: object, **kwargs: object) -> bool:
        self._re_create(key)
        return await super().update_node_if_match(key, collection, *args, **kwargs)

    async def update_node_fields_if_match(self, key: str, collection: str, *args: object, **kwargs: object) -> bool:
        self._re_create(key)
        return await super().update_node_fields_if_match(key, collection, *args, **kwargs)

    async def delete_node_if_match(self, key: str, collection: str, *args: object, **kwargs: object) -> bool:
        self._re_create(key)
        return await super().delete_node_if_match(key, collection, *args, **kwargs)


async def _detach_then_delete(store: GraphSkillStore) -> bool:
    await store.detach_from_agents(_NAME)
    return await store.delete_skill(_NAME)


class TestEveryWriteIsBoundToTheDocItRead:
    """The real-store version is tests/integration/graph_db/test_skill_writes_bound_to_creation_real_backends.py."""

    @pytest.mark.parametrize(
        ("actor", "write", "outcome"),
        [
            pytest.param("owner", lambda s: s.write_resource(_NAME, "notes.md", "from a"), SkillConflictError, id="write-resource"),
            pytest.param("owner", lambda s: s.remove_resource(_NAME, "a.md"), SkillConflictError, id="remove-resource"),
            pytest.param("owner", lambda s: s.delete_skill(_NAME), SkillConflictError, id="delete"),
            pytest.param("admin", _detach_then_delete, SkillConflictError, id="admin-detach-and-delete"),
            pytest.param("admin", lambda s: s.set_skill_status(_NAME, SkillStatus.DISABLED), SkillConflictError, id="status"),
            pytest.param(
                "admin", lambda s: s.set_skill_status(_NAME, SkillStatus.DISABLED, from_status=SkillStatus.ACTIVE), False,
                id="disable-compare-and-swap",
            ),
            pytest.param("owner", lambda s: s.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Push it later.")),
                         SkillConflictError, id="update"),
            pytest.param("owner", lambda s: s.rollback(_NAME, "1.0.0"), SkillConflictError, id="rollback"),
        ],
    )
    async def test_a_write_after_the_check_never_lands_on_a_skill_created_again(self, actor, write, outcome) -> None:
        graph = _ReCreatedBeforeTheWrite()
        owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        await owner.create_skill("base-skill", _BASE_MD)
        await owner.create_skill(_NAME, _OWNER_MD, resources={"a.md": "from a"})
        await owner.update_skill(_NAME, _OWNER_MD.replace("Push it.", "Push it now."))
        store = owner if actor == "owner" else skill_store(graph, "org-1", "user-c", is_admin=True)
        graph.armed = True

        if outcome is False:
            assert await write(store) is False
        else:
            with pytest.raises(outcome) as exc:
                await write(store)
            # Nothing about the skill that holds the name now.
            assert exc.value.current_updated_at == 0 and exc.value.current_version == ""

        assert not graph.armed, "the write never reached the provider"
        doc = graph._col("agentSkills")[_KEY]
        assert doc["generation"] == "b-generation" and doc["createdBy"] == "user-b"
        assert "New owner body" in doc["content"] and doc["status"] == "active"
        assert doc["resourcePaths"] == ["b.md"] and doc["resourceContents"] == ["from b"]
        assert graph._edges["agentHasSkill"] == [{
            "from_id": "agent-b", "from_collection": "agentInstances", "to_id": _KEY, "to_collection": "agentSkills",
        }]

    async def test_a_concurrent_edit_of_the_same_skill_reports_its_version(self) -> None:
        graph = FakeGraphProvider()
        owner = await _seed_owner_skill(graph)
        read = copy.deepcopy(graph._col("agentSkills")[_KEY])
        graph._col("agentSkills")[_KEY].update(updatedAtTimestamp=read["updatedAtTimestamp"] + 7, version="1.0.9")

        with pytest.raises(SkillConflictError) as exc:
            await owner._write_resources(_NAME, read, {"x.md": "x"})
        assert exc.value.current_updated_at == read["updatedAtTimestamp"] + 7
        assert exc.value.current_version == "1.0.9"

    async def test_a_skill_stored_before_generations_existed_is_still_written(self) -> None:
        graph = FakeGraphProvider()
        owner = await _seed_owner_skill(graph)
        _strip_generations(graph)
        assert await owner.write_resource(_NAME, "notes.md", "n")
        assert await owner.remove_resource(_NAME, "notes.md")
        assert await owner.set_skill_status(_NAME, SkillStatus.DISABLED, from_status=SkillStatus.ACTIVE)
        await owner.update_skill(_NAME, _OWNER_MD)
        assert graph._col("agentSkills")[_KEY]["generation"]
        assert await owner.delete_skill(_NAME)
        assert _KEY not in graph._col("agentSkills")


class _NoIndex:
    async def remove_entry(self, name: str) -> None:
        return None


class TestAManagerDeleteIsBoundToItsFirstRead:
    """`SkillManager.delete` reads the binding first, then checks usage, then deletes.
    The real-store version is in test_skill_writes_bound_to_creation_real_backends.py."""

    @pytest.mark.parametrize("when", ["binding", "usage"], ids=["after-binding", "after-usage"])
    @pytest.mark.parametrize("admin", [False, True], ids=["owner", "admin"])
    @pytest.mark.parametrize("detach", [False, True], ids=["delete", "delete-detach"])
    async def test_a_name_created_again_after_the_first_read_is_not_deleted(
        self, admin: bool, detach: bool, when: str,
    ) -> None:
        """After the binding, B's re-created skill (with B's agent) must not decide an in-use
        refusal that reports B's agents: the delete is a conflict, as after the usage check."""
        from app.agent_loop_lib.modules.providers.skills.manager import SkillManager

        graph = FakeGraphProvider()
        owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        await owner.create_skill(_NAME, _OTHER_MD)
        if detach:
            graph._edges.setdefault("agentHasSkill", []).append(
                {"from_id": "agent-a", "from_collection": "agentInstances", "to_id": _KEY, "to_collection": "agentSkills"},
            )
            graph._col("agentInstances")["agent-a"] = {"id": "agent-a", "name": "A"}
        store = skill_store(graph, "org-1", "user-c", is_admin=True) if admin else owner
        replaced = False

        async def re_create(name: str) -> None:
            nonlocal replaced
            if replaced:
                return
            replaced = True
            assert await skill_store(graph, "org-1", "user-a").delete_skill(name)
            await skill_store(graph, "org-1", "user-b", visibility_scope="user-b").create_skill(name, _OTHER_MD)
            graph._edges.setdefault("agentHasSkill", []).append(
                {"from_id": "agent-b", "from_collection": "agentInstances", "to_id": _KEY, "to_collection": "agentSkills"},
            )
            graph._col("agentInstances")["agent-b"] = {"id": "agent-b", "name": "B"}

        read_binding, read_usage = store.deletion_binding, store.get_referential_usage

        async def binding_then(name: str):  # noqa: ANN202
            binding = await read_binding(name)
            if when == "binding":
                await re_create(name)
            return binding

        async def usage_then(name: str):  # noqa: ANN202
            usage = await read_usage(name)
            if when == "usage":
                await re_create(name)
            return usage

        store.deletion_binding = binding_then  # type: ignore[method-assign]
        store.get_referential_usage = usage_then  # type: ignore[method-assign]
        manager = SkillManager(store=store, index=_NoIndex(), tracker=None)  # type: ignore[arg-type]
        with pytest.raises(SkillConflictError) as exc:
            await manager.delete(_NAME, detach=detach)
        assert "agent-b" not in str(exc.value) and exc.value.current_version == ""
        assert graph._col("agentSkills")[_KEY]["createdBy"] == "user-b"
        assert [e["from_id"] for e in graph._edges["agentHasSkill"]] == ["agent-b"]

    @pytest.mark.parametrize("admin", [False, True], ids=["owner", "admin"])
    async def test_the_store_refuses_a_stale_binding_itself(self, admin: bool) -> None:
        graph = FakeGraphProvider()
        await skill_store(graph, "org-1", "user-a", visibility_scope="user-a").create_skill(_NAME, _OTHER_MD)
        caller = (skill_store(graph, "org-1", "user-c", is_admin=True) if admin
                  else skill_store(graph, "org-1", "user-a", visibility_scope="user-a"))
        binding = await caller.deletion_binding(_NAME)
        assert await skill_store(graph, "org-1", "user-a").delete_skill(_NAME)
        await skill_store(graph, "org-1", "user-b", visibility_scope="user-b").create_skill(_NAME, _OTHER_MD)
        with pytest.raises(SkillConflictError):
            await caller.delete_skill(_NAME, expected=binding)
        assert graph._col("agentSkills")[_KEY]["createdBy"] == "user-b"


class TestWhatAContentEditKeeps:
    async def test_an_edit_and_a_rollback_keep_the_approval_and_the_audit_trail(self) -> None:
        """The conditional write replaces the whole document."""
        graph = FakeGraphProvider()
        owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        await owner.create_skill(_NAME, _OTHER_MD, approved_by="admin")
        kept = {"approvedBy": "admin", "auditActions": ["created"], "auditActorIds": ["user-a"], "auditReasons": [""], "auditTimestamps": [1]}
        graph._col("agentSkills")[_KEY].update(kept)
        await owner.update_skill(_NAME, _OTHER_MD.replace("entirely", "completely"))
        await owner.rollback(_NAME, "1.0.0")
        doc = graph._col("agentSkills")[_KEY]
        assert {k: doc[k] for k in kept} == kept


class TestPinnedContentFollowsOneCreation:
    async def test_a_name_created_again_serves_no_content_of_the_deleted_skill(self) -> None:
        graph = FakeGraphProvider()
        old = skill_store(graph, "org-1", "user-a")
        await old.create_skill(_NAME, _OWNER_MD.replace("Push it.", "Old private text."))
        await old.update_skill(_NAME, _OWNER_MD)
        await old.delete_skill(_NAME)
        # Deleted: what a running pin holds still resolves.
        assert set(await old.content_versions(_NAME)) == {"1.0.0", "1.0.1"}
        await skill_store(graph, "org-1", "user-b").create_skill(_NAME, _OTHER_MD)
        reader = skill_store(graph, "org-1", "reader")
        assert await reader.content_versions(_NAME) == ["1.0.0"]
        assert await reader.content_files(_NAME, "1.0.1") is None
        files = await reader.content_files(_NAME, "1.0.0")
        assert files is not None and "Old private text" not in files["SKILL.md"]


class TestSharingIsBoundToTheDocItRead:
    @pytest.mark.parametrize("write", [
        pytest.param(lambda s: s.share(_NAME, "org", None), id="share"),
        pytest.param(lambda s: s.rename(_NAME, "Deploy"), id="rename"),
    ])
    async def test_it_never_lands_on_a_skill_created_again(self, write) -> None:
        graph = _ReCreatedBeforeTheWrite()
        owner = skill_store(
            graph, "org-1", "user-a", visibility_scope="user-a", rights=SharedSkillRights(org_admin=True),
        )
        await owner.create_skill(_NAME, _OTHER_MD)
        graph.armed = True
        with pytest.raises(SkillConflictError):
            await write(owner)
        doc = graph._col("agentSkills")[_KEY]
        assert doc["generation"] == "b-generation" and "scope" not in doc and "displayName" not in doc


async def test_a_team_skill_keeps_the_history_its_other_editors_wrote_before_generations() -> None:
    graph = FakeGraphProvider()
    owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a", rights=SharedSkillRights(org_admin=True))
    await owner.create_skill(_NAME, _OWNER_MD)
    await owner.share(_NAME, "org", None)
    manager = skill_store(graph, "org-1", "user-b", rights=SharedSkillRights(org_admin=True))
    await manager.update_skill(_NAME, _OTHER_MD)
    await manager.update_skill(_NAME, _OTHER_MD.replace("entirely", "completely"))
    _strip_generations(graph)
    assert {v.version for v in await owner.list_versions(_NAME)} == {"1.0.0", "1.0.1"}


class _RelationEdgesFailOnce(FakeGraphProvider):
    """The owner edge lands, then the relation edges fail once."""

    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    async def batch_create_edges(self, edges: list[dict[str, Any]], collection: str, transaction: str | None = None) -> bool:
        if collection == "agentSkillRelation" and self.failures:
            self.failures -= 1
            raise RuntimeError("edge write failed")
        return await super().batch_create_edges(edges, collection, transaction)


class TestACreateWhoseEdgesFailIsUndone:
    async def test_the_name_is_free_again_and_the_retry_completes_it(self) -> None:
        graph = _RelationEdgesFailOnce()
        owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        with pytest.raises(RuntimeError, match="edge write failed"):
            await owner.create_skill(_NAME, _OWNER_MD)
        assert _KEY not in graph._col("agentSkills")
        assert not [e for edges in graph._edges.values() for e in edges if e.get("to_id") == _KEY]
        await owner.create_skill(_NAME, _OWNER_MD)
        assert graph._col("agentSkills")[_KEY]["createdBy"] == "user-a"
        # The required skill is not held by anyone, so no requires edge is written for it.
        assert {e["type"] for e in graph._edges["agentSkillRelation"]} == {"related"}


class _UndoFails(_RelationEdgesFailOnce):
    """As above, and the undo of the half-made create does not land either."""

    def __init__(self, *, raises: bool) -> None:
        super().__init__()
        self.raises = raises
        self.undo_failures = 1

    async def delete_node_if_match(self, *args: object, **kwargs: object) -> bool:
        if self.undo_failures:
            self.undo_failures -= 1
            if self.raises:
                raise RuntimeError("undo failed")
            return False
        return await super().delete_node_if_match(*args, **kwargs)


class TestACreateWhoseUndoFailsIsReported:
    @pytest.mark.parametrize("raises", [True, False])
    async def test_the_create_error_stands_the_leftover_is_logged_and_an_admin_delete_frees_the_name(
        self, raises: bool, caplog: pytest.LogCaptureFixture,
    ) -> None:
        graph = _UndoFails(raises=raises)
        owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        with caplog.at_level("ERROR"), pytest.raises(RuntimeError, match="edge write failed"):
            await owner.create_skill(_NAME, _OWNER_MD)
        assert any(_NAME in r.getMessage() and "without its edges" in r.getMessage() for r in caplog.records)
        with pytest.raises(SkillAlreadyExistsError):
            await owner.create_skill(_NAME, _OWNER_MD)
        assert await skill_store(graph, "org-1", "user-c", is_admin=True).delete_skill(_NAME)
        await owner.create_skill(_NAME, _OWNER_MD)
        assert graph._col("agentSkills")[_KEY]["createdBy"] == "user-a"


class _OthersWriteBeforeTheCommit(FakeGraphProvider):
    """As the store is about to commit the edit of the version it read, `interleave` runs: other writes that land
    first."""

    def __init__(self) -> None:
        super().__init__()
        self.interleave = None

    async def update_node_if_match(self, key: str, collection: str, *args: object, **kwargs: object) -> bool:
        if collection == "agentSkills" and self.interleave is not None:
            interleave, self.interleave = self.interleave, None
            await interleave()
        return await super().update_node_if_match(key, collection, *args, **kwargs)


class TestAWriteThatLosesItsCommitArchivesNothingOverTheWinner:
    async def test_the_version_keeps_the_state_the_successful_write_superseded(self) -> None:
        graph = _OthersWriteBeforeTheCommit()
        loser = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        await loser.create_skill(_NAME, _OTHER_MD, resources={"notes.md": "X"})
        winner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")

        async def resource_write_then_an_edit() -> None:
            await winner.write_resource(_NAME, "notes.md", "Y")  # same version 1.0.0, new state
            await winner.update_skill(_NAME, _OTHER_MD.replace("entirely", "by the winner"))

        graph.interleave = resource_write_then_an_edit
        with pytest.raises(SkillConflictError):
            await loser.update_skill(_NAME, _OTHER_MD.replace("entirely", "by the loser"))

        assert (await loser.content_files(_NAME, "1.0.0"))["notes.md"] == "Y"
        assert [v.version for v in await loser.list_versions(_NAME)] == ["1.0.0"]

    async def test_a_resource_write_alone_wins_and_the_loser_archives_nothing(self) -> None:
        graph = _OthersWriteBeforeTheCommit()
        loser = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        await loser.create_skill(_NAME, _OTHER_MD, resources={"notes.md": "X"})
        winner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")

        async def resource_write() -> None:
            await winner.write_resource(_NAME, "notes.md", "Y")  # same version 1.0.0, new state; no edit lands

        graph.interleave = resource_write
        with pytest.raises(SkillConflictError):
            await loser.update_skill(_NAME, _OTHER_MD.replace("entirely", "by the loser"))

        # The loser archived the state it read before its commit failed; that version is still live, so it is not served.
        assert await loser.list_versions(_NAME) == []
        assert await loser.content_versions(_NAME) == ["1.0.0"]
        assert (await loser.content_files(_NAME, "1.0.0"))["notes.md"] == "Y"
        await winner.update_skill(_NAME, _OTHER_MD.replace("entirely", "by the winner"))
        assert (await loser.content_files(_NAME, "1.0.0"))["notes.md"] == "Y"
        await winner.rollback(_NAME, "1.0.0")
        assert (await loser.content_files(_NAME))["notes.md"] == "Y"


class _ArchiveFails(FakeGraphProvider):
    """The next archive write to the version history raises."""

    def __init__(self) -> None:
        super().__init__()
        self.archive_failures = 0

    async def batch_upsert_nodes(self, nodes: list[dict[str, Any]], collection: str, *args: object, **kwargs: object) -> bool:
        if collection == "agentSkillVersions" and self.archive_failures:
            self.archive_failures -= 1
            raise RuntimeError("archive write failed")
        return await super().batch_upsert_nodes(nodes, collection, *args, **kwargs)


class TestAFailedArchiveCommitsNothing:
    """The state a write supersedes is archived before the write: a failed archive leaves the skill as it was, and the
    retry keeps the history."""

    async def test_an_edit(self) -> None:
        graph = _ArchiveFails()
        owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        await owner.create_skill(_NAME, _OTHER_MD)
        graph.archive_failures = 1
        with pytest.raises(RuntimeError, match="archive write failed"):
            await owner.update_skill(_NAME, _OTHER_MD.replace("entirely", "by the edit"))
        assert graph._col("agentSkills")[_KEY]["version"] == "1.0.0"
        await owner.update_skill(_NAME, _OTHER_MD.replace("entirely", "by the edit"))
        assert "entirely" in (await owner.content_files(_NAME, "1.0.0"))["SKILL.md"]

    async def test_a_rollback(self) -> None:
        graph = _ArchiveFails()
        owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        await owner.create_skill(_NAME, _OTHER_MD)
        await owner.update_skill(_NAME, _OTHER_MD.replace("entirely", "by the edit"))
        graph.archive_failures = 1
        with pytest.raises(RuntimeError, match="archive write failed"):
            await owner.rollback(_NAME, "1.0.0")
        assert graph._col("agentSkills")[_KEY]["version"] == "1.0.1"
        await owner.rollback(_NAME, "1.0.0")
        assert "by the edit" in (await owner.content_files(_NAME, "1.0.1"))["SKILL.md"]

    async def test_a_delete(self) -> None:
        graph = _ArchiveFails()
        owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        await owner.create_skill(_NAME, _OTHER_MD)
        graph.archive_failures = 1
        with pytest.raises(RuntimeError, match="archive write failed"):
            await owner.delete_skill(_NAME)
        assert _KEY in graph._col("agentSkills")
        assert await owner.delete_skill(_NAME)
        assert "entirely" in (await owner.content_files(_NAME, "1.0.0"))["SKILL.md"]


class TestARequiresEdgeToALegacySkillOutlivesAClockBehindIt:
    async def test_the_writer_whose_clock_is_behind_the_target_still_counts_as_requiring_it(self) -> None:
        graph = FakeGraphProvider()
        owner = skill_store(graph, "org-1", "user-a", visibility_scope="user-a")
        await owner.create_skill("base-skill", _BASE_MD)
        base = graph._col("agentSkills")["org-1_base-skill"]
        base.pop("generation")  # stored before generations existed
        base["createdAtTimestamp"] += 3_600_000  # its creator's clock ran an hour ahead of this writer's
        await owner.create_skill(_NAME, _OWNER_MD)
        assert (await owner.get_referential_usage("base-skill")).required_by_skills == [_NAME]
