"""StageRegistry: dependency-ordered registration keeps the DAG acyclic and complete."""

from typing import ClassVar

import pytest

from app.modules.pipeline.models import HeadlineField, Workload
from app.modules.pipeline.registry import StageRegistry, UnknownStageError


class _FakeStage:
    name: ClassVar[str] = ""
    version: ClassVar[int] = 1
    requires: ClassVar[frozenset[str]] = frozenset()
    workload: ClassVar[Workload] = Workload.LLM
    headline: ClassVar[HeadlineField | None] = None
    budget_s: ClassVar[float] = 60.0


def _stage(name: str, *requires: str, version: int = 1) -> _FakeStage:
    cls = type(f"Stage_{name}", (_FakeStage,), {"name": name, "requires": frozenset(requires), "version": version})
    return cls()


def test_registration_order_is_dependency_order() -> None:
    registry = StageRegistry()
    registry.register(_stage("parse"))
    registry.register(_stage("embed", "parse"))
    registry.register(_stage("classify", "parse"))
    registry.register(_stage("project-metadata", "classify", "embed"))
    assert registry.names() == ("parse", "embed", "classify", "project-metadata")


def test_a_stage_cannot_require_one_registered_later() -> None:
    registry = StageRegistry()
    with pytest.raises(ValueError, match="register them first"):
        registry.register(_stage("classify", "embed"))


def test_a_cycle_cannot_be_expressed() -> None:
    registry = StageRegistry()
    registry.register(_stage("a"))
    registry.register(_stage("b", "a"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_stage("a", "b"))


def test_external_prerequisites_satisfy_requires_but_do_not_run() -> None:
    registry = StageRegistry()
    registry.register_external("embed")
    registry.register(_stage("classify", "embed"))
    assert registry.names() == ("classify",)
    assert registry.is_external("embed")
    assert [stage.name for stage in registry.successors("embed")] == ["classify"]
    with pytest.raises(UnknownStageError):
        registry.get("embed")


def test_successors_are_stages_that_require_the_given_one() -> None:
    registry = StageRegistry()
    registry.register(_stage("parse"))
    registry.register(_stage("embed", "parse"))
    registry.register(_stage("classify", "parse"))
    assert {stage.name for stage in registry.successors("parse")} == {"embed", "classify"}
    assert registry.successors("classify") == ()


@pytest.mark.parametrize("name", ["Classify", "classify:v2", "enrich_blocks", "", "1parse", "a@b"])
def test_names_must_be_safe_for_topics_and_keys(name: str) -> None:
    with pytest.raises(ValueError, match="invalid stage name"):
        StageRegistry().register(_stage(name))


def test_version_must_be_positive() -> None:
    with pytest.raises(ValueError, match="version"):
        StageRegistry().register(_stage("classify", version=0))


def test_topic_for_a_registered_stage() -> None:
    registry = StageRegistry()
    registry.register(_stage("classify"))
    assert registry.topic_for("classify") == "pipeline.classify"
    with pytest.raises(UnknownStageError):
        registry.topic_for("entities")
