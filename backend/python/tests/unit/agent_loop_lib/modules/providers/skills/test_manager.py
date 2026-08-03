from __future__ import annotations

import json
import os

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.core.types import AgentResult, AgentTurn, Goal
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillCandidate,
    SkillExperience,
    SkillFilter,
    SkillMatch,
    SkillMetadata,
    SkillSource,
    SkillStatus,
    SkillVersionInfo,
)
from app.agent_loop_lib.modules.providers.skills.evaluator import SkillEvaluator
from app.agent_loop_lib.modules.providers.skills.extractor import SkillExtractor
from app.agent_loop_lib.modules.providers.skills.governor import SkillGovernor
from app.agent_loop_lib.modules.providers.skills.index import SkillIndex
from app.agent_loop_lib.modules.providers.skills.manager import (
    SkillManager,
    SkillManagerConfig,
    _candidate_to_skill_md,
)
from app.agent_loop_lib.modules.providers.skills.store import (
    SkillCandidateStore,
    SkillHistoryReader,
    SkillStore,
)
from app.agent_loop_lib.modules.providers.skills.tracker import SkillUsageTracker


def _metadata(name: str, *, status: SkillStatus = SkillStatus.ACTIVE) -> SkillMetadata:
    return SkillMetadata(name=name, description=f"use {name}", status=status)


def _skill(name: str, *, status: SkillStatus = SkillStatus.ACTIVE) -> Skill:
    return Skill(metadata=_metadata(name, status=status), body=f"# {name}\n\ndo it")


def _candidate(candidate_id: str = "cand-1", name: str = "new-skill") -> SkillCandidate:
    return SkillCandidate(
        candidate_id=candidate_id,
        name=name,
        description="when to use it",
        body="do the thing",
        created_at="2026-01-01T00:00:00+00:00",
    )


class FakeStore(SkillStore):
    """In-memory `SkillStore` double tracking every call the manager makes,
    so orchestration tests assert on manager behavior without touching disk."""

    def __init__(self, skills: dict[str, Skill] | None = None) -> None:
        self._skills: dict[str, Skill] = dict(skills or {})
        self.refresh_called = False
        self.write_resource_calls: list[tuple[str, str, str]] = []
        self.remove_resource_calls: list[tuple[str, str]] = []

    def refresh(self) -> None:
        self.refresh_called = True

    async def list_skills(self, filter: SkillFilter | None = None) -> list[SkillMetadata]:
        return [s.metadata for s in self._skills.values()]

    async def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    async def get_resource(self, skill_name: str, resource_path: str) -> str | None:
        return None

    async def exists(self, name: str) -> bool:
        return name in self._skills

    async def create_skill(
        self, name: str, content: str, category: str | None = None, subcategory: str | None = None,
    ) -> SkillMetadata:
        if name in self._skills:
            raise RegistryError(f"Skill {name!r} already exists")
        metadata = SkillMetadata(name=name, description="created", category=category, subcategory=subcategory)
        self._skills[name] = Skill(metadata=metadata, body=content)
        return metadata

    async def update_skill(self, name: str, content: str) -> SkillMetadata:
        if name not in self._skills:
            raise RegistryError(f"Skill {name!r} not found")
        metadata = self._skills[name].metadata.model_copy(update={"description": "updated"})
        self._skills[name] = Skill(metadata=metadata, body=content)
        return metadata

    async def patch_skill(self, name: str, old_string: str, new_string: str) -> bool:
        skill = self._skills.get(name)
        if skill is None or old_string not in skill.body:
            return False
        self._skills[name] = skill.model_copy(update={"body": skill.body.replace(old_string, new_string, 1)})
        return True

    async def delete_skill(self, name: str) -> bool:
        return self._skills.pop(name, None) is not None

    async def write_resource(self, skill_name: str, path: str, content: str) -> bool:
        self.write_resource_calls.append((skill_name, path, content))
        return skill_name in self._skills

    async def remove_resource(self, skill_name: str, path: str) -> bool:
        self.remove_resource_calls.append((skill_name, path))
        return skill_name in self._skills

    async def deprecate_skill(self, name: str, reason: str, replaced_by: str | None = None) -> bool:
        skill = self._skills.get(name)
        if skill is None:
            return False
        updated = skill.metadata.model_copy(update={"status": SkillStatus.DEPRECATED, "deprecated_reason": reason})
        self._skills[name] = skill.model_copy(update={"metadata": updated})
        return True


class _NoRefreshStore(SkillStore):
    """A `SkillStore` that never opted into the optional `refresh()` hook —
    `SkillManager.refresh` must tolerate this via `hasattr`, unlike
    `FakeStore` above which always defines it."""

    def __init__(self, skills: dict[str, Skill] | None = None) -> None:
        self._skills: dict[str, Skill] = dict(skills or {})

    async def list_skills(self, filter: SkillFilter | None = None) -> list[SkillMetadata]:
        return [s.metadata for s in self._skills.values()]

    async def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    async def get_resource(self, skill_name: str, resource_path: str) -> str | None:
        return None

    async def exists(self, name: str) -> bool:
        return name in self._skills

    async def create_skill(self, name, content, category=None, subcategory=None) -> SkillMetadata:
        raise NotImplementedError

    async def update_skill(self, name, content) -> SkillMetadata:
        raise NotImplementedError

    async def patch_skill(self, name, old_string, new_string) -> bool:
        raise NotImplementedError

    async def delete_skill(self, name) -> bool:
        raise NotImplementedError

    async def write_resource(self, skill_name, path, content) -> bool:
        raise NotImplementedError

    async def remove_resource(self, skill_name, path) -> bool:
        raise NotImplementedError

    async def deprecate_skill(self, name, reason, replaced_by=None) -> bool:
        raise NotImplementedError


class FakeHistoryStore(FakeStore, SkillHistoryReader):
    def __init__(self, *args, versions: dict[str, list[SkillVersionInfo]] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._versions = versions or {}
        self.rollback_calls: list[tuple[str, str]] = []

    async def list_versions(self, name: str) -> list[SkillVersionInfo]:
        return self._versions.get(name, [])

    async def get_version(self, name: str, version: str) -> Skill | None:
        skill = self._skills.get(name)
        if skill is None:
            return None
        return skill.model_copy(update={"body": f"body @ {version}"})

    async def rollback(self, name: str, version: str) -> SkillMetadata:
        self.rollback_calls.append((name, version))
        if name not in self._skills:
            raise RegistryError(f"Skill {name!r} not found")
        metadata = self._skills[name].metadata.model_copy(update={"version": version})
        self._skills[name] = self._skills[name].model_copy(update={"metadata": metadata})
        return metadata


class FakeCandidateStore(FakeStore, SkillCandidateStore):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pending: dict[str, SkillCandidate] = {}

    async def queue_candidate(self, candidate: SkillCandidate) -> None:
        self._pending[candidate.candidate_id] = candidate

    async def get_pending_candidates(self) -> list[SkillCandidate]:
        return list(self._pending.values())

    async def remove_candidate(self, candidate_id: str) -> None:
        self._pending.pop(candidate_id, None)


class FakeIndex(SkillIndex):
    def __init__(self) -> None:
        self.rebuilt_with: list[SkillMetadata] | None = None
        self.added: list[SkillMetadata] = []
        self.updated: list[SkillMetadata] = []
        self.removed: list[str] = []
        self.search_calls: list[tuple[str, SkillFilter | None, int]] = []
        self.search_results: list[SkillMatch] = []

    async def rebuild(self, skills: list[SkillMetadata]) -> None:
        self.rebuilt_with = list(skills)

    async def search(self, query: str, filter: SkillFilter | None = None, limit: int = 10) -> list[SkillMatch]:
        self.search_calls.append((query, filter, limit))
        return self.search_results

    async def get_categories(self) -> dict[str, list[str]]:
        return {"devops": ["kubernetes"]}

    async def get_tags(self) -> list[str]:
        return ["a", "b"]

    async def add_entry(self, metadata: SkillMetadata) -> None:
        self.added.append(metadata)

    async def remove_entry(self, name: str) -> None:
        self.removed.append(name)

    async def update_entry(self, metadata: SkillMetadata) -> None:
        self.updated.append(metadata)


class FakeTracker(SkillUsageTracker):
    def __init__(self) -> None:
        self.activations: list[tuple[str, str]] = []
        self.outcomes: list[tuple[str, str, bool, str]] = []
        self.experiences: dict[str, SkillExperience] = {}

    async def record_activation(self, skill_name: str, session_id: str) -> None:
        self.activations.append((skill_name, session_id))

    async def record_outcome(self, skill_name: str, session_id: str, success: bool, notes: str = "") -> None:
        self.outcomes.append((skill_name, session_id, success, notes))

    async def get_experience(self, skill_name: str) -> SkillExperience:
        return self.experiences.get(skill_name, SkillExperience(skill_name=skill_name))

    async def get_underperforming(self, threshold: float = 0.5) -> list[SkillExperience]:
        return []

    async def get_unused(self, since_days: int = 30) -> list[SkillExperience]:
        return []


class FakeExtractor(SkillExtractor):
    def __init__(self, candidates: list[SkillCandidate] | None = None, *, extract: bool = True) -> None:
        self._candidates = candidates or []
        self._extract = extract
        self.extract_calls = 0

    async def should_extract(self, result: AgentResult) -> bool:
        return self._extract

    async def extract_candidates(self, result, trajectory=None, decision_trace=None, existing_catalog=None):
        self.extract_calls += 1
        return list(self._candidates)


class FakeEvaluator(SkillEvaluator):
    def __init__(self, *, reject_names: set[str] | None = None) -> None:
        self._reject_names = reject_names or set()

    async def evaluate_candidate(self, candidate: SkillCandidate) -> tuple[bool, str]:
        if candidate.name in self._reject_names:
            return False, "rejected by fake evaluator"
        return True, "ok"

    async def evaluate_existing(self, experience: SkillExperience) -> tuple[str, str]:
        return "refine", "fake says refine"


class FakeGovernor(SkillGovernor):
    def __init__(self, *, approve: bool = True) -> None:
        self._approve = approve
        self.created_calls: list[SkillMetadata] = []
        self.deprecated_calls: list[tuple[str, str]] = []

    async def should_approve(self, candidate: SkillCandidate) -> bool:
        return self._approve

    async def on_skill_created(self, metadata: SkillMetadata) -> None:
        self.created_calls.append(metadata)

    async def on_skill_deprecated(self, name: str, reason: str) -> None:
        self.deprecated_calls.append((name, reason))

    async def get_pending_candidates(self) -> list[SkillCandidate]:
        return []


def _manager(
    store: SkillStore | None = None,
    *,
    index: FakeIndex | None = None,
    tracker: FakeTracker | None = None,
    extractor: FakeExtractor | None = None,
    evaluator: FakeEvaluator | None = None,
    governor: FakeGovernor | None = None,
    config: SkillManagerConfig | None = None,
) -> SkillManager:
    return SkillManager(
        store=store or FakeStore(),
        index=index or FakeIndex(),
        tracker=tracker or FakeTracker(),
        extractor=extractor,
        evaluator=evaluator,
        governor=governor,
        config=config,
    )


def _result(*, success: bool = True) -> AgentResult:
    return AgentResult(goal=Goal(description="ship the feature"), success=success, turns=[AgentTurn()])


class TestLifecycle:
    async def test_start_populates_catalog_and_rebuilds_index(self) -> None:
        store = FakeStore({"a": _skill("a"), "b": _skill("b")})
        index = FakeIndex()
        manager = _manager(store, index=index)

        await manager.start()

        assert {m.name for m in manager.catalog_snapshot()} == {"a", "b"}
        assert {m.name for m in index.rebuilt_with} == {"a", "b"}

    async def test_refresh_calls_store_refresh_when_supported(self) -> None:
        store = FakeStore()
        manager = _manager(store)

        await manager.refresh()

        assert store.refresh_called is True

    async def test_refresh_tolerates_store_without_refresh_method(self) -> None:
        store = _NoRefreshStore({"a": _skill("a")})
        manager = _manager(store)

        await manager.refresh()  # must not raise

        assert {m.name for m in manager.catalog_snapshot()} == {"a"}

    async def test_config_property_returns_configured_instance(self) -> None:
        config = SkillManagerConfig(max_candidates=7)
        manager = _manager(config=config)

        assert manager.config is config

    async def test_catalog_snapshot_excludes_deprecated_skills(self) -> None:
        store = FakeStore({
            "active": _skill("active"),
            "gone": _skill("gone", status=SkillStatus.DEPRECATED),
        })
        manager = _manager(store)
        await manager.start()

        names = {m.name for m in manager.catalog_snapshot()}
        assert names == {"active"}


class TestActivateAndLoadResource:
    async def test_activate_skill_returns_full_skill(self) -> None:
        store = FakeStore({"a": _skill("a")})
        manager = _manager(store)

        skill = await manager.activate_skill("a")

        assert skill.name == "a"

    async def test_activate_skill_records_activation_when_session_id_given(self) -> None:
        store = FakeStore({"a": _skill("a")})
        tracker = FakeTracker()
        manager = _manager(store, tracker=tracker)

        await manager.activate_skill("a", session_id="sess-1")

        assert tracker.activations == [("a", "sess-1")]

    async def test_activate_skill_no_session_id_records_nothing(self) -> None:
        store = FakeStore({"a": _skill("a")})
        tracker = FakeTracker()
        manager = _manager(store, tracker=tracker)

        await manager.activate_skill("a")

        assert tracker.activations == []

    async def test_activate_unknown_skill_raises_registry_error(self) -> None:
        manager = _manager(FakeStore())

        try:
            await manager.activate_skill("nope")
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError")

    async def test_load_resource_returns_content(self) -> None:
        class _ResourceStore(FakeStore):
            async def get_resource(self, skill_name: str, resource_path: str) -> str | None:
                return "resource content" if skill_name == "a" and resource_path == "p.txt" else None

        manager = _manager(_ResourceStore({"a": _skill("a")}))

        content = await manager.load_resource("a", "p.txt")

        assert content == "resource content"

    async def test_load_resource_missing_raises_registry_error(self) -> None:
        manager = _manager(FakeStore({"a": _skill("a")}))

        try:
            await manager.load_resource("a", "missing.txt")
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError")


class TestSearchAndCatalogQueries:
    async def test_search_builds_filter_and_delegates_to_index(self) -> None:
        index = FakeIndex()
        index.search_results = [SkillMatch(skill=_metadata("a"), relevance=0.9)]
        manager = _manager(index=index)

        results = await manager.search("deploy", category="devops", tags=["k8s"], limit=5)

        assert results == index.search_results
        query, filt, limit = index.search_calls[0]
        assert query == "deploy"
        assert filt.category == "devops"
        assert filt.tags == ["k8s"]
        assert limit == 5

    async def test_search_empty_query_becomes_none_in_filter(self) -> None:
        index = FakeIndex()
        manager = _manager(index=index)

        await manager.search("")

        _, filt, _ = index.search_calls[0]
        assert filt.query is None

    async def test_list_skills_delegates_to_store(self) -> None:
        store = FakeStore({"a": _skill("a")})
        manager = _manager(store)

        results = await manager.list_skills()

        assert [m.name for m in results] == ["a"]

    async def test_get_categories_and_tags_delegate_to_index(self) -> None:
        manager = _manager(index=FakeIndex())

        assert await manager.get_categories() == {"devops": ["kubernetes"]}
        assert await manager.get_tags() == ["a", "b"]


class TestCrud:
    async def test_create_updates_catalog_and_index(self) -> None:
        store = FakeStore()
        index = FakeIndex()
        manager = _manager(store, index=index)

        metadata = await manager.create("new-skill", "content")

        assert metadata.name == "new-skill"
        assert manager.catalog_snapshot()[0].name == "new-skill"
        assert index.added[0].name == "new-skill"

    async def test_update_updates_catalog_and_index(self) -> None:
        store = FakeStore({"s": _skill("s")})
        index = FakeIndex()
        manager = _manager(store, index=index)

        metadata = await manager.update("s", "new content")

        assert metadata.description == "updated"
        assert manager.catalog_snapshot()[0].description == "updated"
        assert index.updated[0].description == "updated"

    async def test_patch_success_resyncs_catalog_and_index(self) -> None:
        store = FakeStore({"s": _skill("s")})
        index = FakeIndex()
        manager = _manager(store, index=index)
        await manager.start()

        ok = await manager.patch("s", "do it", "do it now")

        assert ok is True
        skill = await store.get_skill("s")
        assert skill.body == "# s\n\ndo it now"
        assert index.updated[-1].name == "s"

    async def test_patch_failure_does_not_resync(self) -> None:
        store = FakeStore({"s": _skill("s")})
        index = FakeIndex()
        manager = _manager(store, index=index)

        ok = await manager.patch("s", "not present", "x")

        assert ok is False
        assert index.updated == []

    async def test_delete_success_removes_from_catalog_and_index(self) -> None:
        store = FakeStore({"s": _skill("s")})
        index = FakeIndex()
        manager = _manager(store, index=index)
        await manager.start()

        ok = await manager.delete("s")

        assert ok is True
        assert manager.catalog_snapshot() == []
        assert index.removed == ["s"]

    async def test_delete_unknown_returns_false_and_does_not_touch_index(self) -> None:
        index = FakeIndex()
        manager = _manager(FakeStore(), index=index)

        ok = await manager.delete("nope")

        assert ok is False
        assert index.removed == []

    async def test_deprecate_success_resyncs_and_notifies_governor(self) -> None:
        store = FakeStore({"s": _skill("s")})
        governor = FakeGovernor()
        manager = _manager(store, governor=governor)
        await manager.start()

        ok = await manager.deprecate("s", "no longer needed")

        assert ok is True
        assert manager.catalog_snapshot() == []  # deprecated is excluded from snapshot
        assert governor.deprecated_calls == [("s", "no longer needed")]

    async def test_deprecate_unknown_returns_false_and_skips_governor(self) -> None:
        governor = FakeGovernor()
        manager = _manager(FakeStore(), governor=governor)

        ok = await manager.deprecate("nope", "reason")

        assert ok is False
        assert governor.deprecated_calls == []

    async def test_write_resource_and_remove_resource_delegate_to_store(self) -> None:
        store = FakeStore({"s": _skill("s")})
        manager = _manager(store)

        assert await manager.write_resource("s", "a.txt", "content") is True
        assert await manager.remove_resource("s", "a.txt") is True
        assert store.write_resource_calls == [("s", "a.txt", "content")]
        assert store.remove_resource_calls == [("s", "a.txt")]


class TestVersionHistory:
    async def test_supports_history_false_for_plain_store(self) -> None:
        manager = _manager(FakeStore())
        assert manager.supports_history is False

    async def test_supports_history_true_for_history_store(self) -> None:
        manager = _manager(FakeHistoryStore())
        assert manager.supports_history is True

    async def test_list_versions_raises_when_unsupported(self) -> None:
        manager = _manager(FakeStore())
        try:
            await manager.list_versions("s")
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError")

    async def test_get_version_raises_when_unsupported(self) -> None:
        manager = _manager(FakeStore())
        try:
            await manager.get_version("s", "1.0.0")
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError")

    async def test_rollback_raises_when_unsupported(self) -> None:
        manager = _manager(FakeStore())
        try:
            await manager.rollback("s", "1.0.0")
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError")

    async def test_list_versions_delegates_when_supported(self) -> None:
        versions = [SkillVersionInfo(version="1.0.0", created_at="2026-01-01T00:00:00+00:00")]
        store = FakeHistoryStore({"s": _skill("s")}, versions={"s": versions})
        manager = _manager(store)

        result = await manager.list_versions("s")

        assert result == versions

    async def test_get_version_delegates_when_supported(self) -> None:
        store = FakeHistoryStore({"s": _skill("s")})
        manager = _manager(store)

        skill = await manager.get_version("s", "0.9.0")

        assert skill.body == "body @ 0.9.0"

    async def test_rollback_updates_catalog_and_index(self) -> None:
        store = FakeHistoryStore({"s": _skill("s")})
        index = FakeIndex()
        manager = _manager(store, index=index)
        await manager.start()

        metadata = await manager.rollback("s", "0.5.0")

        assert metadata.version == "0.5.0"
        assert store.rollback_calls == [("s", "0.5.0")]
        assert manager.catalog_snapshot()[0].version == "0.5.0"
        assert index.updated[-1].version == "0.5.0"


class TestLearnFromExecution:
    async def test_learning_disabled_returns_empty_without_calling_extractor(self) -> None:
        extractor = FakeExtractor([_candidate()])
        manager = _manager(extractor=extractor, config=SkillManagerConfig(learning_enabled=False))

        result = await manager.learn_from_execution(_result())

        assert result == []
        assert extractor.extract_calls == 0

    async def test_no_extractor_configured_returns_empty(self) -> None:
        manager = _manager(extractor=None)

        result = await manager.learn_from_execution(_result())

        assert result == []

    async def test_should_extract_false_returns_empty(self) -> None:
        extractor = FakeExtractor([_candidate()], extract=False)
        manager = _manager(extractor=extractor)

        result = await manager.learn_from_execution(_result())

        assert result == []
        assert extractor.extract_calls == 0

    async def test_candidates_truncated_to_max_candidates(self) -> None:
        candidates = [_candidate(f"cand-{i}", f"skill-{i}") for i in range(5)]
        extractor = FakeExtractor(candidates)
        governor = FakeGovernor(approve=True)
        manager = _manager(
            extractor=extractor, governor=governor,
            config=SkillManagerConfig(max_candidates=2, learning_enabled=True),
        )

        result = await manager.learn_from_execution(_result())

        assert len(result) == 2

    async def test_evaluator_rejection_excludes_candidate(self) -> None:
        extractor = FakeExtractor([_candidate("cand-1", "rejected-skill"), _candidate("cand-2", "kept-skill")])
        evaluator = FakeEvaluator(reject_names={"rejected-skill"})
        governor = FakeGovernor(approve=True)
        manager = _manager(
            extractor=extractor, evaluator=evaluator, governor=governor,
            config=SkillManagerConfig(learning_enabled=True),
        )

        result = await manager.learn_from_execution(_result())

        assert [c.name for c in result] == ["kept-skill"]

    async def test_governor_approval_marks_approved_and_does_not_queue(self) -> None:
        extractor = FakeExtractor([_candidate()])
        governor = FakeGovernor(approve=True)
        manager = _manager(
            extractor=extractor, governor=governor,
            config=SkillManagerConfig(learning_enabled=True),
        )

        result = await manager.learn_from_execution(_result())

        assert result[0].status == "approved"
        pending = await manager.get_pending_candidates()
        assert pending == []

    async def test_governor_rejection_marks_pending_and_queues(self, tmp_path) -> None:
        extractor = FakeExtractor([_candidate()])
        governor = FakeGovernor(approve=False)
        manager = _manager(
            extractor=extractor, governor=governor,
            config=SkillManagerConfig(skills_dir=str(tmp_path), learning_enabled=True),
        )

        result = await manager.learn_from_execution(_result())

        assert result[0].status == "pending"
        pending = await manager.get_pending_candidates()
        assert [c.candidate_id for c in pending] == ["cand-1"]

    async def test_source_session_id_is_stamped_onto_every_candidate(self) -> None:
        extractor = FakeExtractor([_candidate()])
        governor = FakeGovernor(approve=True)
        manager = _manager(
            extractor=extractor, governor=governor,
            config=SkillManagerConfig(learning_enabled=True),
        )

        result = await manager.learn_from_execution(_result(), session_id="sess-42")

        assert result[0].source_session_id == "sess-42"


class TestCandidateQueueFilesystemFallback:
    async def test_queue_candidate_writes_json_file(self, tmp_path) -> None:
        manager = _manager(config=SkillManagerConfig(skills_dir=str(tmp_path)))

        await manager.queue_candidate(_candidate("cand-1", "new-skill"))

        candidates_dir = os.path.join(str(tmp_path), "_meta", "candidates")
        path = os.path.join(candidates_dir, "cand-1.json")
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["name"] == "new-skill"

    async def test_get_pending_candidates_empty_when_dir_missing(self, tmp_path) -> None:
        manager = _manager(config=SkillManagerConfig(skills_dir=str(tmp_path / "nonexistent")))

        assert await manager.get_pending_candidates() == []

    async def test_get_pending_candidates_reads_back_queued_json(self, tmp_path) -> None:
        manager = _manager(config=SkillManagerConfig(skills_dir=str(tmp_path)))
        await manager.queue_candidate(_candidate("cand-1", "skill-one"))
        await manager.queue_candidate(_candidate("cand-2", "skill-two"))

        pending = await manager.get_pending_candidates()

        assert sorted(c.candidate_id for c in pending) == ["cand-1", "cand-2"]

    async def test_ignores_non_json_files_in_candidates_dir(self, tmp_path) -> None:
        manager = _manager(config=SkillManagerConfig(skills_dir=str(tmp_path)))
        await manager.queue_candidate(_candidate("cand-1", "skill-one"))
        candidates_dir = os.path.join(str(tmp_path), "_meta", "candidates")
        with open(os.path.join(candidates_dir, "README.txt"), "w", encoding="utf-8") as f:
            f.write("not a candidate")

        pending = await manager.get_pending_candidates()

        assert len(pending) == 1

    async def test_reject_candidate_removes_without_creating_skill(self, tmp_path) -> None:
        store = FakeStore()
        manager = _manager(store, config=SkillManagerConfig(skills_dir=str(tmp_path)))
        await manager.queue_candidate(_candidate("cand-1", "skill-one"))

        await manager.reject_candidate("cand-1")

        assert await manager.get_pending_candidates() == []
        assert await store.exists("skill-one") is False

    async def test_approve_candidate_creates_skill_and_removes_from_queue(self, tmp_path) -> None:
        store = FakeStore()
        governor = FakeGovernor()
        manager = _manager(store, governor=governor, config=SkillManagerConfig(skills_dir=str(tmp_path)))
        await manager.queue_candidate(_candidate("cand-1", "skill-one"))

        metadata = await manager.approve_candidate("cand-1")

        assert metadata.name == "skill-one"
        assert await store.exists("skill-one") is True
        assert await manager.get_pending_candidates() == []
        assert governor.created_calls[0].name == "skill-one"

    async def test_approve_unknown_candidate_raises_registry_error(self, tmp_path) -> None:
        manager = _manager(config=SkillManagerConfig(skills_dir=str(tmp_path)))

        try:
            await manager.approve_candidate("nonexistent")
        except RegistryError:
            pass
        else:
            raise AssertionError("expected RegistryError")


class TestCandidateQueueViaCandidateStore:
    async def test_queue_candidate_delegates_to_candidate_store(self, tmp_path) -> None:
        store = FakeCandidateStore()
        manager = _manager(store, config=SkillManagerConfig(skills_dir=str(tmp_path)))

        await manager.queue_candidate(_candidate("cand-1", "skill-one"))

        assert [c.candidate_id for c in await store.get_pending_candidates()] == ["cand-1"]
        # fallback filesystem queue must NOT be used when the store opts in
        assert not os.path.isdir(os.path.join(str(tmp_path), "_meta", "candidates"))

    async def test_get_pending_candidates_delegates_to_candidate_store(self, tmp_path) -> None:
        store = FakeCandidateStore()
        manager = _manager(store, config=SkillManagerConfig(skills_dir=str(tmp_path)))
        await store.queue_candidate(_candidate("cand-1", "skill-one"))

        pending = await manager.get_pending_candidates()

        assert [c.candidate_id for c in pending] == ["cand-1"]

    async def test_remove_candidate_delegates_to_candidate_store(self, tmp_path) -> None:
        store = FakeCandidateStore()
        manager = _manager(store, config=SkillManagerConfig(skills_dir=str(tmp_path)))
        await store.queue_candidate(_candidate("cand-1", "skill-one"))

        await manager.reject_candidate("cand-1")

        assert await store.get_pending_candidates() == []


class TestUsageTracking:
    async def test_record_activation_and_outcome_delegate_to_tracker(self) -> None:
        tracker = FakeTracker()
        manager = _manager(tracker=tracker)

        await manager.record_activation("s", "sess-1")
        await manager.record_outcome("s", "sess-1", True, "worked great")

        assert tracker.activations == [("s", "sess-1")]
        assert tracker.outcomes == [("s", "sess-1", True, "worked great")]

    async def test_evaluate_skill_health_without_evaluator_returns_keep(self) -> None:
        manager = _manager(evaluator=None)

        action, reason = await manager.evaluate_skill_health("s")

        assert action == "keep"
        assert reason == "no evaluator configured"

    async def test_evaluate_skill_health_with_evaluator_delegates(self) -> None:
        tracker = FakeTracker()
        tracker.experiences["s"] = SkillExperience(skill_name="s", successful_outcomes=1, failed_outcomes=4)
        evaluator = FakeEvaluator()
        manager = _manager(tracker=tracker, evaluator=evaluator)

        action, reason = await manager.evaluate_skill_health("s")

        assert action == "refine"
        assert reason == "fake says refine"


class TestCandidateToSkillMd:
    def test_renders_valid_frontmatter_with_agent_created_source(self) -> None:
        candidate = SkillCandidate(
            candidate_id="cand-1",
            name="new-skill",
            description="a useful skill",
            body="Step one.\nStep two.",
            category="devops",
            tags=["k8s"],
            created_at="2026-01-01T00:00:00+00:00",
        )

        content = _candidate_to_skill_md(candidate)

        assert content.startswith("---\n")
        assert "name: new-skill" in content
        assert "description: a useful skill" in content
        assert "source: agent_created" in content
        assert content.strip().endswith("Step two.")

    def test_default_governor_config_precedence(self) -> None:
        from app.agent_loop_lib.modules.providers.skills.governor import (
            AutoApproveGovernor,
            ManualReviewGovernor,
        )
        from app.agent_loop_lib.modules.providers.skills.manager import _default_governor

        assert isinstance(_default_governor(SkillManagerConfig()), ManualReviewGovernor)
        assert isinstance(_default_governor(SkillManagerConfig(auto_approve=True)), AutoApproveGovernor)
        assert isinstance(
            _default_governor(SkillManagerConfig(auto_approve=True, write_approval=True)), ManualReviewGovernor,
        )
