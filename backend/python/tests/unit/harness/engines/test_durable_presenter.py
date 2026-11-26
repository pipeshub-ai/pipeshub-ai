"""PR-1, JS-7, EK-11 (E1): PipesHub's presentation of a durable engine run, on the in-memory world with
PipesHub's event catalog validated as Node validates it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.artifacts.events import PRESENTED
from app.harness.contracts.generated.pipeshub.event_catalog import (
    EVENT_MODELS,
    EVENT_WRITERS,
)
from app.harness.engines.presenter import (
    PROGRESS,
    PipesHubEngineRunPresenter,
    SessionRevisions,
)
from harness_core.testing.contracts.engine_kernel import EngineRunPresenterContract
from harness_core.testing.engines.durable import (
    SESSION,
    EngineWorld,
    MemoryEngineAssembler,
)

if TYPE_CHECKING:
    from harness_core.engines.durable.ports import AssembledEngineRun, EngineAttempt

RUN = "run-pr"


class TestPipesHubEngineRunPresenter(EngineRunPresenterContract):
    @pytest.fixture
    def presenter(self) -> PipesHubEngineRunPresenter:
        return PipesHubEngineRunPresenter()


class _PresentingAssembler(MemoryEngineAssembler):
    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        from app.harness.engines.durable import TaskCardPublicationLog
        from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
        from harness_core.workspace.types import ObjectScope

        runtime: AssembledEngineRun = await super().build(attempt)
        w = self.world
        presenter = PipesHubEngineRunPresenter(SessionRevisions(w.store, w.objects, "org-e1", SESSION))
        publisher = WorkspaceArtifactPublisher(
            w.artifacts,
            TaskCardPublicationLog(attempt, presenter),
            w.objects,
            object_scope=lambda b: ObjectScope(b.tenant, f"ws:{b.scope}"),
        )
        runtime.presenter = presenter
        runtime.artifacts = publisher
        return runtime


@pytest.fixture
def world() -> EngineWorld:
    return EngineWorld(
        approvals={"create": "allow"},
        extension_writers=cast("Any", EVENT_WRITERS),
        extension_models=cast("Any", EVENT_MODELS),
    )


def frames(world: EngineWorld) -> list[dict[str, Any]]:
    return [cast("dict[str, Any]", e.data) for e in world.events(RUN) if e.type == PROGRESS]


async def test_pr1_a_run_shows_stage_ones_frames_from_started_to_finished(world: EngineWorld) -> None:
    world.admit(RUN, 'shell ls\nwrite out/report.md # Q3\ntool lookup {"q": "x"}\nsay all done')
    kernel = world.kernel(assembler=_PresentingAssembler(world))
    assert (await world.attempt(RUN, kernel=kernel)).status == "completed"
    shown = frames(world)
    kinds = [f["kind"] for f in shown]
    assert kinds[0] == "started" and kinds[-1] == "finished"
    assert {"command", "file", "tool", "version", "diff", "artifact"} <= set(kinds)
    assert all(f["tool"] == "code_task" and f["taskId"] == RUN for f in shown)
    assert shown[-1]["data"] == {"success": True} and "all done" in shown[-1]["text"]
    diff = next(f for f in shown if f["kind"] == "diff" and f["text"] == "out/report.md")
    assert "+# Q3" in diff["data"]["diff"]
    lifecycles = [f["data"]["lifecycle"] for f in shown if f["kind"] == "artifact"]
    assert lifecycles[0] == "draft" and lifecycles[-1] == "final"
    assert all(not e.critical for e in world.events(RUN) if e.type in (PROGRESS, PRESENTED))


async def test_js7_publications_ride_the_runs_fenced_commit_and_its_journal_is_their_history(world: EngineWorld) -> None:
    world.admit(RUN, "write out/a.md x\nsay done")
    kernel = world.kernel(assembler=_PresentingAssembler(world))
    assert (await world.attempt(RUN, kernel=kernel)).status == "completed"
    events = world.events(RUN)
    presented = [e for e in events if e.type == PRESENTED]
    assert presented and all(e.attempt_id == f"{RUN}.a1" and e.writer == "worker" for e in presented)
    intents = [e for e in events if e.type == "x.pipeshub.artifact.publish_intent"]
    assert len(intents) == 1


async def test_ek11_an_engine_approval_is_journaled_with_the_call_it_is_about() -> None:
    world = EngineWorld(
        approvals={"create": "ask"},
        extension_writers=cast("Any", EVENT_WRITERS),
        extension_models=cast("Any", EVENT_MODELS),
    )
    world.admit(RUN, 'tool create {"name": "Q3"}\nsay waiting')
    kernel = world.kernel(assembler=_PresentingAssembler(world))
    disposition = await world.attempt(RUN, kernel=kernel)
    assert disposition.status == "suspended"
    events = world.events(RUN)
    requested = next(i for i, e in enumerate(events) if e.type == "interaction.requested")
    # WP-97: the card's reason sits between the call line and the request; the line is the event before both.
    assert events[requested - 1].type == "interaction.explained"
    approval = events[requested - 2]
    assert approval.type == PROGRESS and approval.data["kind"] == "approval"
    assert approval.data["data"]["interactionId"] == events[requested].data["interactionId"]  # type: ignore[index]
    assert approval.data["data"]["toolName"] == "create"  # type: ignore[index]
    assert '"Q3"' in str(approval.data["data"]["argsJson"])  # type: ignore[index]


def test_qafix6_an_engine_approval_names_its_tool_as_the_loop_does() -> None:
    from harness_core.engines.durable.ports import ProgressStamp

    names = {"mcp_e2esaas_create_ticket": "E2Esaas · create_ticket"}
    presenter = PipesHubEngineRunPresenter(display_name=names.get)
    stamp = ProgressStamp(RUN, "trn_1", iter(range(10)).__next__)
    [line] = presenter.approval(stamp, "ix-1", "mcp_e2esaas_create_ticket", {"title": "T"})
    assert line.data["data"]["displayName"] == "E2Esaas · create_ticket"  # type: ignore[index]
    [unnamed] = presenter.approval(stamp, "ix-2", "bash", {})
    assert "displayName" not in unnamed.data["data"]  # type: ignore[operator]
