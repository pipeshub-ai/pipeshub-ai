"""The agent harness on the running stack: skills, the coding tool, and artifacts.

CTO list, Agent Harness: document skills loaded as needed, coding tools used as
the query needs, artifacts visible in chat, and artifact versions. One chat
asks for a Word document and then for a change to it; a second asks for
something only running code can answer.

A skill reaches the model one of two ways. The model may call ``load_skill``
(a tool call on the stream), or the harness may preload a clearly relevant
skill before the model starts, which emits no event but counts an activation
on the skill's graph node. Either one is the skill being loaded.

The coding tool runs in the stack's own sandbox (the local subprocess sandbox:
the integration compose sets no SANDBOX_MODE), so nothing here is skipped for
want of one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
import requests
from pipeshub_client import PipeshubClient

from ai_agents.support import (
    ItModel,
    delete_conversation,
    fetch_conversation,
    json_body,
    stream_chat,
    stream_chat_message,
)
from helper.agui_run import ArtifactRef, RunTrace, last_bot_message, parse_artifact_markers
from helper.clients.conversations_client import ConversationsClient

pytestmark = [pytest.mark.integration, pytest.mark.ai_agents]

_SKILL = "docx"
# Calls that load a skill; searching for or listing skills is not loading one.
_SKILL_TOOLS = ("load_skill", "load_skill_resource")
_FILE = f"it-harness-brief-{uuid.uuid4().hex[:6]}.docx"
_ZIP_MAGIC = b"PK"


@dataclass
class DocxTurn:
    trace: RunTrace
    activations_before: int
    activations_after: int


def _chat_body(it_model: ItModel, query: str) -> dict[str, Any]:
    return {
        "query": query,
        "chatMode": "agent",
        "modelKey": it_model.model_key,
        "modelName": it_model.model_name,
        "modelFriendlyName": it_model.friendly_name,
    }


async def _skill_activations(graph_provider, org_id: str) -> int:
    doc = await graph_provider.get_document(f"{org_id}_{_SKILL}", "agentSkills")
    return int((doc or {}).get("usageTotalActivations") or 0)


def _docx_refs(refs: list[ArtifactRef]) -> list[ArtifactRef]:
    return [ref for ref in refs if ref.file_name.lower().endswith(".docx")]


def _download(pipeshub_client: PipeshubClient, artifact_id: str, version: int | None = None) -> requests.Response:
    params = {"version": str(version)} if version is not None else None
    return pipeshub_client.request("GET", f"/api/v1/knowledgeBase/stream/record/{artifact_id}", params=params)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def docx_turn(
    pipeshub_client: PipeshubClient,
    conversations_client: ConversationsClient,
    it_model: ItModel,
    graph_provider,
) -> AsyncIterator[DocxTurn]:
    # Listing skills seeds the built-in packs on a fresh org, as opening Settings > Skills does.
    listed = pipeshub_client.request("GET", "/api/v1/skills/")
    assert listed.status_code == 200, f"listing skills: {listed.status_code} {listed.text[:300]}"
    names = [s.get("name") for s in json_body(listed).get("skills") or [] if isinstance(s, dict)]
    assert _SKILL in names, f"the built-in {_SKILL} skill is not in the catalog: {names}"

    org_id = pipeshub_client.org_id
    before = await _skill_activations(graph_provider, org_id)
    trace = stream_chat(conversations_client, _chat_body(it_model, (
        f"Create a Word document named {_FILE} with the title 'Harness check' and one short "
        "paragraph saying this integration run created it. Attach the file to your reply."
    )))
    after = await _skill_activations(graph_provider, org_id)
    try:
        yield DocxTurn(trace, before, after)
    finally:
        delete_conversation(conversations_client, trace)


@pytest.fixture(scope="module")
def update_turn(docx_turn: DocxTurn, conversations_client: ConversationsClient, it_model: ItModel) -> RunTrace:
    assert docx_turn.trace.conversation_id, docx_turn.trace.describe()
    return stream_chat_message(conversations_client, docx_turn.trace.conversation_id, _chat_body(it_model, (
        f"Update {_FILE}: add a second paragraph that says 'Version two.' Keep the same file name "
        "and attach the updated file."
    )))


@pytest.fixture(scope="module")
def first_docx(docx_turn: DocxTurn, conversations_client: ConversationsClient) -> ArtifactRef:
    """The document as the saved chat shows it (the answer's artifact markers)."""
    trace = docx_turn.trace
    assert trace.finished and not trace.error, trace.describe()
    saved = fetch_conversation(conversations_client, trace.conversation_id)
    content = last_bot_message(saved).get("content") or ""
    refs = _docx_refs(parse_artifact_markers(content))
    assert refs, f"the saved answer shows no .docx artifact: {content[:1500]!r}; {trace.describe()}"
    return refs[0]


@pytest.fixture(scope="module")
def coding_turn(conversations_client: ConversationsClient, it_model: ItModel) -> Iterator[RunTrace]:
    text = f"pipeshub-integration-{uuid.uuid4().hex[:8]}"
    trace = stream_chat(conversations_client, _chat_body(it_model, (
        f"What are the first 16 hex characters of the SHA-256 digest of the exact text {text} ?"
    )))
    try:
        yield trace
    finally:
        delete_conversation(conversations_client, trace)


class TestDocumentSkill:
    def test_a_document_request_loads_the_docx_skill(self, docx_turn: DocxTurn) -> None:
        trace = docx_turn.trace
        tool_loads = [
            call for call in trace.tool_calls
            if call.name in _SKILL_TOOLS and _SKILL in call.args.lower()
        ]
        preloaded = docx_turn.activations_after > docx_turn.activations_before
        assert tool_loads or preloaded, (
            f"the {_SKILL} skill was neither loaded by a tool call nor preloaded "
            f"(activations {docx_turn.activations_before} -> {docx_turn.activations_after}); {trace.describe()}"
        )


class TestCodingTool:
    def test_a_computation_uses_the_coding_tool(self, coding_turn: RunTrace) -> None:
        assert coding_turn.finished and not coding_turn.error, coding_turn.describe()
        runs = coding_turn.calls_ending_with("run_code")
        assert runs, f"no run_code call for a question only code can answer: {coding_turn.describe()}"
        assert any(call.status == "completed" for call in runs), [(c.status, c.result) for c in runs]


class TestArtifacts:
    def test_the_document_is_visible_in_the_chat(self, first_docx: ArtifactRef, pipeshub_client: PipeshubClient) -> None:
        resp = _download(pipeshub_client, first_docx.artifact_id)
        assert resp.status_code == 200, f"{resp.status_code} {resp.text[:300]}"
        assert resp.content[:2] == _ZIP_MAGIC, "the artifact is not a .docx (zip) file"

    def test_the_stream_announced_the_same_artifact(self, docx_turn: DocxTurn, first_docx: ArtifactRef) -> None:
        streamed = _docx_refs(docx_turn.trace.streamed_artifacts)
        if not streamed:
            pytest.skip("this producer saves artifacts without a live event; the saved answer is checked above")
        assert first_docx.artifact_id in {ref.artifact_id for ref in streamed}, (streamed, first_docx)

    def test_updating_it_creates_version_two(self, update_turn: RunTrace, first_docx: ArtifactRef) -> None:
        assert update_turn.finished and not update_turn.error, update_turn.describe()
        refs = [
            ref for ref in update_turn.saved_artifacts + update_turn.streamed_artifacts
            if ref.artifact_id == first_docx.artifact_id
        ]
        assert refs, (
            f"the update produced no new version of {first_docx.artifact_id} "
            f"(artifacts: {update_turn.saved_artifacts + update_turn.streamed_artifacts})"
        )
        assert max(ref.version or 0 for ref in refs) == 2, refs

    def test_both_versions_can_be_downloaded(
        self, update_turn: RunTrace, first_docx: ArtifactRef, pipeshub_client: PipeshubClient
    ) -> None:
        del update_turn
        v1 = _download(pipeshub_client, first_docx.artifact_id, 1)
        v2 = _download(pipeshub_client, first_docx.artifact_id, 2)
        assert v1.status_code == 200, f"version 1: {v1.status_code} {v1.text[:300]}"
        assert v2.status_code == 200, f"version 2: {v2.status_code} {v2.text[:300]}"
        assert v1.content[:2] == _ZIP_MAGIC and v2.content[:2] == _ZIP_MAGIC
        assert v1.content != v2.content, "version 1 and version 2 have the same bytes"
