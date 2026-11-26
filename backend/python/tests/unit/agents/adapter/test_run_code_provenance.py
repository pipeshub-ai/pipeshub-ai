"""OI-B1 (PR1): a `run_code` output records what it was derived from, so SS0 shows it to the viewers who
may read those sources and to nobody else, instead of hiding every one (unknown provenance)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agents.agent_loop.artifact_provenance import (
    note_staged_inputs,
    note_unlabelled_workspace,
)
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.sandbox_bridge import _register_and_deliver
from app.models.entities import ArtifactType
from app.services.artifact_registry import Actor, ArtifactRegistryService
from app.services.artifact_registry.models import ArtifactProvenance
from app.services.artifact_registry.viewer_access import (
    ArtifactViewerAccess,
    RecordedProvenance,
)
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

ORG = "org-1"
OWNER = Actor(org_id=ORG, user_id="owner")
READER = Actor(org_id=ORG, user_id="reader")
OUTSIDER = Actor(org_id=ORG, user_id="outsider")
SEARCH = {"tool_name": "knowledgegraph__search", "status": "success"}
RUN_CODE = {"tool_name": "run_code", "status": "success"}


class _Readers:
    """Which record each viewer may read: the authorizer's answer, kept apart from the graph fake."""

    def __init__(self, grants: dict[str, set[str]]) -> None:
        self._grants = grants

    async def readable(self, actor: Actor, record_ids: list[str]) -> set[str]:
        return set(record_ids) & self._grants.get(actor.user_id, set())


def _world(grants: dict[str, set[str]]) -> tuple[ArtifactRegistryService, ArtifactViewerAccess]:
    graph = FakeGraphProvider()
    for user in ("owner", "reader", "outsider"):
        graph.add_user(user)
    registry = ArtifactRegistryService(graph, FakeBlobStore())
    access = ArtifactViewerAccess(graph, RecordedProvenance(graph))
    access._records = _Readers(grants)  # type: ignore[assignment]
    return registry, access


def _context(**tool_state: Any) -> AgentContext:  # noqa: ANN401
    context = AgentContext(
        org_id=ORG, user_id="owner", user_email="o@example.com", logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()), conversation_id="conv-1",
    )
    context.tool_state.update(tool_state)
    return context


def _shown(record_id: str) -> dict[str, Any]:
    return {"vr-" + record_id: {"metadata": {"recordId": record_id, "connector": "CONFLUENCE"}}}


async def _run_code_output(context: AgentContext, registry: ArtifactRegistryService, name: str = "chart.png") -> str:
    made = await _register_and_deliver(
        context, registry, [(f"output/{name}", b"bytes of " + name.encode())], {f"output/{name}": name}, None,
        source_tool="/toolsets/coding_sandbox/run_code", code_artifact_id=None, code_artifact_version=None,
    )
    (metadata,) = made
    return metadata.artifact_id


async def test_an_output_of_a_turn_that_read_r_is_shown_to_readers_of_r_only() -> None:
    registry, access = _world({"reader": {"rec-R"}})
    context = _context(virtual_record_id_to_result=_shown("rec-R"), all_tool_results=[SEARCH, RUN_CODE])
    artifact = await _run_code_output(context, registry)
    assert await registry.lineage.sources_of(ORG, [artifact]) == {artifact: frozenset({"rec-R"})}
    assert (await access.decide(READER, [artifact]))[artifact] == "readable"
    assert (await access.decide(OUTSIDER, [artifact]))[artifact] == "denied"


async def test_an_output_with_no_knowledge_inputs_is_shown_to_every_participant() -> None:
    registry, access = _world({})
    artifact = await _run_code_output(_context(all_tool_results=[RUN_CODE]), registry)
    assert await registry.lineage.sources_of(ORG, [artifact]) == {artifact: frozenset()}
    assert (await access.decide(OUTSIDER, [artifact]))[artifact] == "readable"


async def test_a_staged_input_artifact_carries_its_own_sources_through() -> None:
    registry, access = _world({"reader": {"rec-A", "rec-R"}, "outsider": {"rec-R"}})
    source, _ = await registry.register_output(
        actor=OWNER, name="data.csv", artifact_type=ArtifactType.SPREADSHEET, mime_type="text/csv",
        content=b"a,b", conversation_id="conv-1",
    )
    await registry.record_derivation(
        output_artifact_id=source.artifact_id, output_version=1,
        provenance=ArtifactProvenance(version=0, run_id="r", task_id="t", source_record_ids=["rec-A"]),
    )
    context = _context(virtual_record_id_to_result=_shown("rec-R"), all_tool_results=[SEARCH, RUN_CODE])
    note_staged_inputs(context, [{"artifact_id": source.artifact_id, "version": 1}, {"artifact_id": "artifact_4", "version": 0}])
    artifact = await _run_code_output(context, registry)
    assert await registry.lineage.sources_of(ORG, [artifact]) == {artifact: frozenset({"rec-A", "rec-R"})}
    stored = await registry.lineage.provenance_of(artifact)
    assert [(p.artifact_id, p.version) for p in stored[0].input_artifacts] == [(source.artifact_id, 1)]
    assert (await access.decide(READER, [artifact]))[artifact] == "readable"
    assert (await access.decide(OUTSIDER, [artifact]))[artifact] == "denied"


async def test_a_staged_input_of_unknown_provenance_makes_the_output_unknown() -> None:
    registry, access = _world({"reader": {"rec-R"}})
    legacy, _ = await registry.register_output(
        actor=OWNER, name="old.csv", artifact_type=ArtifactType.SPREADSHEET, mime_type="text/csv",
        content=b"x", conversation_id="conv-1",
    )
    context = _context(all_tool_results=[RUN_CODE])
    note_staged_inputs(context, [{"artifact_id": legacy.artifact_id, "version": 1}])
    artifact = await _run_code_output(context, registry)
    assert (await access.decide(READER, [artifact]))[artifact] == "unresolved"


@pytest.mark.parametrize(
    ("tool_state", "why"),
    [
        ({"all_tool_results": [{"tool_name": "jira__search_issues"}, RUN_CODE]}, "a connector action's content names no record"),
        ({"all_tool_results": [{"tool_name": "web_search"}, RUN_CODE]}, "a web fetch is opaque"),
        ({}, "no record of the request's tool calls"),
        (
            {
                "all_tool_results": [SEARCH, RUN_CODE],
                "virtual_record_id_to_result": {"vr": {"metadata": {"recordId": "live:drive:f1", "sourceKind": "live", "connector": "DRIVE", "externalRecordId": "f1"}}},
            },
            "a live file has no record a viewer's access can be decided on",
        ),
    ],
)
async def test_content_nobody_can_name_leaves_the_output_hidden(tool_state: dict[str, Any], why: str) -> None:
    registry, access = _world({"reader": {"rec-R"}})
    context = _context(**tool_state)
    if not tool_state:
        context.tool_state.pop("all_tool_results", None)
    artifact = await _run_code_output(context, registry)
    assert (await access.decide(READER, [artifact]))[artifact] == "unresolved", why


async def test_staged_workspace_files_leave_the_output_hidden() -> None:
    registry, access = _world({})
    context = _context(all_tool_results=[RUN_CODE])
    note_unlabelled_workspace(context)
    artifact = await _run_code_output(context, registry)
    assert (await access.decide(READER, [artifact]))[artifact] == "unresolved"
