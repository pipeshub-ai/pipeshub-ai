"""OI-B1 (PR1), the other producers: an artifact saved, updated, generated as an image or written by a
legacy code toolset records what the request showed the model, so SS0 shows it to the viewers who may
read those sources, hides it from the rest, and hides it from everyone when the sources are unknown."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.agents.actions.coding_sandbox import coding_sandbox as coding_module
from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
from app.agents.actions.image_generator import image_generator as image_module
from app.agents.actions.image_generator.image_generator import ImageGenerator
from app.agents.agent_loop.artifact_provenance import request_provenance
from app.sandbox.artifact_upload import save_query_result_csv
from app.sandbox.models import ArtifactOutput, ExecutionResult
from app.services.artifact_registry import Actor
from app.services.artifact_registry.viewer_access import (
    ArtifactViewerAccess,
    RecordedProvenance,
)
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    import asyncio

ORG = "org-1"
READER = Actor(org_id=ORG, user_id="reader")
OUTSIDER = Actor(org_id=ORG, user_id="outsider")
SEARCH = {"tool_name": "knowledgegraph__search", "status": "success"}
EARLIER_SAVE = {"tool_name": "artifacts__save_artifact", "status": "success"}
CONNECTOR = {"tool_name": "jira__search_issues", "status": "success"}


class _Readers:
    def __init__(self, grants: dict[str, set[str]]) -> None:
        self._grants = grants

    async def readable(self, actor: Actor, record_ids: list[str]) -> set[str]:
        return set(record_ids) & self._grants.get(actor.user_id, set())


class _Blobs(FakeBlobStore):
    async def save_conversation_file_to_storage(
        self, *, org_id: str, conversation_id: str, file_name: str, file_bytes: bytes, content_type: str = "",
    ) -> dict[str, Any]:
        return await self.save_versioned_artifact_to_storage(
            org_id=org_id, conversation_id=conversation_id, file_name=file_name,
            file_bytes=file_bytes, content_type=content_type,
        )


def _world(grants: dict[str, set[str]]) -> tuple[FakeGraphProvider, _Blobs, ArtifactViewerAccess]:
    graph = FakeGraphProvider()
    for user in ("owner", "reader", "outsider"):
        graph.add_user(user)
    access = ArtifactViewerAccess(graph, RecordedProvenance(graph))
    access._records = _Readers(grants)  # type: ignore[assignment]
    return graph, _Blobs(), access


def _shown(*record_ids: str) -> dict[str, Any]:
    return {f"vr-{rid}": {"metadata": {"recordId": rid, "connector": "CONFLUENCE"}} for rid in record_ids}


def _state(graph: FakeGraphProvider, blobs: _Blobs, *, calls: list[dict[str, Any]] | None, shown: tuple[str, ...] = ()) -> dict[str, Any]:
    state: dict[str, Any] = {
        "org_id": ORG, "user_id": "owner", "conversation_id": "conv-1",
        "graph_provider": graph, "blob_store": blobs,
    }
    if calls is not None:
        state["all_tool_results"] = calls
    if shown:
        state["virtual_record_id_to_result"] = _shown(*shown)
    return state


async def _status(access: ArtifactViewerAccess, actor: Actor, artifact_id: str) -> str:
    return (await access.decide(actor, [artifact_id]))[artifact_id]


async def _save(state: dict[str, Any], name: str = "report.md") -> str:
    ok, body = await ArtifactManager(state).save_artifact(name=name, content="# findings")  # type: ignore[arg-type]
    assert ok, body
    return json.loads(body)["artifact_id"]


class _Captured:
    """The background upload a tool registers, so the test awaits it."""

    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[Any]] = []

    def __call__(self, conversation_id: str, task: asyncio.Task[Any]) -> None:
        self.tasks.append(task)

    async def results(self) -> list[Any]:
        return [await task for task in self.tasks]


# ---- save_artifact / update_artifact ----


async def test_a_saved_artifact_is_shown_to_readers_of_what_the_request_read_only() -> None:
    graph, blobs, access = _world({"reader": {"rec-R"}})
    artifact = await _save(_state(graph, blobs, calls=[SEARCH, EARLIER_SAVE], shown=("rec-R",)))
    assert await _status(access, READER, artifact) == "readable"
    assert await _status(access, OUTSIDER, artifact) == "denied"


@pytest.mark.parametrize("calls", [None, [CONNECTOR]], ids=["no-tool-list", "connector-action"])
async def test_a_saved_artifact_with_unknown_sources_stays_hidden(calls: list[dict[str, Any]] | None) -> None:
    graph, blobs, access = _world({"reader": {"rec-R"}})
    artifact = await _save(_state(graph, blobs, calls=calls, shown=("rec-R",)))
    assert await _status(access, READER, artifact) == "unresolved"


async def test_an_update_adds_its_request_s_sources_to_the_earlier_versions() -> None:
    graph, blobs, access = _world({"reader": {"rec-A", "rec-R"}, "outsider": {"rec-R"}})
    artifact = await _save(_state(graph, blobs, calls=[SEARCH], shown=("rec-A",)))
    second = _state(graph, blobs, calls=[SEARCH], shown=("rec-R",))
    ok, body = await ArtifactManager(second).update_artifact(artifact_id=artifact, content="v2")  # type: ignore[arg-type]
    assert ok, body
    assert await _status(access, READER, artifact) == "readable"
    assert await _status(access, OUTSIDER, artifact) == "denied"


async def test_an_update_with_unknown_sources_hides_the_artifact() -> None:
    graph, blobs, access = _world({"reader": {"rec-A"}})
    artifact = await _save(_state(graph, blobs, calls=[SEARCH], shown=("rec-A",)))
    ok, _ = await ArtifactManager(_state(graph, blobs, calls=None)).update_artifact(artifact_id=artifact, content="v2")  # type: ignore[arg-type]
    assert ok
    assert await _status(access, READER, artifact) == "unresolved"


# ---- image generation ----


async def _generated(state: dict[str, Any], **kwargs: Any) -> str:  # noqa: ANN401
    captured = _Captured()
    generator = ImageGenerator(state)  # type: ignore[arg-type]
    with patch.object(image_module, "register_task", captured):
        generator._schedule_artifact_upload(
            images=[b"png bytes"], blob_store=state["blob_store"], graph_provider=state["graph_provider"],
            org_id=ORG, conversation_id="conv-1", user_id="owner", model_name="img-1",
            file_name_hint="diagram", **kwargs,
        )
        (result,) = await captured.results()
    (entry,) = result["artifacts"]
    return entry["recordId"]


async def test_a_generated_image_is_shown_to_readers_of_what_the_prompt_could_carry_only() -> None:
    graph, blobs, access = _world({"reader": {"rec-R"}})
    state = _state(graph, blobs, calls=[SEARCH], shown=("rec-R",))
    artifact = await _generated(state, provenance=request_provenance(state, task_id="image"))
    assert await _status(access, READER, artifact) == "readable"
    assert await _status(access, OUTSIDER, artifact) == "denied"


async def test_a_generated_image_without_provenance_stays_hidden() -> None:
    graph, blobs, access = _world({"reader": {"rec-R"}})
    artifact = await _generated(_state(graph, blobs, calls=[SEARCH], shown=("rec-R",)))
    assert await _status(access, READER, artifact) == "unresolved"


# ---- legacy code toolsets ----


async def _coding_output(state: dict[str, Any], tmp_path: Path) -> str:
    out = tmp_path / "chart.png"
    out.write_bytes(b"chart")
    result = ExecutionResult(
        success=True, exit_code=0,
        artifacts=[ArtifactOutput(file_name="chart.png", file_path=str(out), mime_type="image/png", size_bytes=5)],
    )
    captured = _Captured()
    # The sandbox-root check reads only under the executors' own roots (app/sandbox/roots.py), which this test does not use.
    with (
        patch.object(coding_module, "register_task", captured),
        patch("app.sandbox.artifact_upload._read_file_bytes", lambda path, *a, **k: Path(path).read_bytes()),
    ):
        CodingSandbox(state)._schedule_artifact_upload(result)  # type: ignore[arg-type]
        (uploaded,) = await captured.results()
    (entry,) = uploaded["artifacts"]
    return entry["recordId"]


async def test_a_coding_sandbox_file_is_shown_to_readers_of_what_the_request_read_only(tmp_path: Path) -> None:
    graph, blobs, access = _world({"reader": {"rec-R"}})
    artifact = await _coding_output(_state(graph, blobs, calls=[SEARCH], shown=("rec-R",)), tmp_path)
    assert await _status(access, READER, artifact) == "readable"
    assert await _status(access, OUTSIDER, artifact) == "denied"


async def test_a_coding_sandbox_file_after_a_connector_action_stays_hidden(tmp_path: Path) -> None:
    graph, blobs, access = _world({"reader": {"rec-R"}})
    artifact = await _coding_output(_state(graph, blobs, calls=[SEARCH, CONNECTOR], shown=("rec-R",)), tmp_path)
    assert await _status(access, READER, artifact) == "unresolved"


@pytest.mark.parametrize(("with_provenance", "expected"), [(True, "readable"), (False, "unresolved")])
async def test_a_sqlite_export_follows_its_provenance(with_provenance: bool, expected: str) -> None:
    graph, blobs, access = _world({"reader": {"rec-R"}})
    state = _state(graph, blobs, calls=[SEARCH], shown=("rec-R",))
    result = await save_query_result_csv(
        blob_store=blobs, graph_provider=graph, org_id=ORG, user_id="owner", conversation_id="conv-1",
        columns=["a"], rows=[(1,)], file_name="sqlite_result.csv", source_tool="database_sandbox.sqlite",
        provenance=request_provenance(state, task_id="sqlite") if with_provenance else None,
    )
    assert result is not None
    (entry,) = result["artifacts"]
    assert await _status(access, READER, entry["recordId"]) == expected
