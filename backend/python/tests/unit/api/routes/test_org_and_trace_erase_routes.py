"""CLEANUP-G1 and the Opik item: what Node's org erasure and a conversation's erasure ask the query service for.

- ``POST /chat/org/erase`` takes one bounded page of an erased org's artifacts, chat attachments, skill
  candidates and agent versions (OWD-14) per call, until nothing of the org remains; another org is never touched.
- ``POST /chat/traces/erase`` deletes a conversation's Opik traces (by its thread and its runs), answers
  ``configured: false`` without a network call when Opik is off, and 503 when Opik fails, so the step retries.
Both take only Node's ``conversation:erase`` service token, and the org comes from the token.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.versions import AgentVersionStore
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import TokenScopes
from tests.support.bound_org import authed
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

CANDIDATES = CollectionNames.AGENT_SKILL_CANDIDATES.value
VERSIONS = CollectionNames.AGENT_VERSIONS.value


def _request(body: object) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.app.container.logger.return_value = MagicMock()
    return request


async def _seed(graph: FakeGraphProvider, blob: FakeBlobStore, org: str) -> None:
    for n in range(2):
        key = f"{org}-artifact-{n}"
        graph.nodes["records"][key] = {
            "_key": key, "id": key, "orgId": org, "recordType": "ARTIFACT", "externalRecordId": blob.store(org, b"a"),
        }
        graph.nodes["artifacts"][key] = {"_key": key, "id": key, "orgId": org, "conversationId": "conv"}
    graph.nodes["artifacts"][f"{org}-orphan"] = {"_key": f"{org}-orphan", "id": f"{org}-orphan", "orgId": org}
    upload = f"{org}-upload"
    graph.nodes["records"][upload] = {
        "_key": upload, "id": upload, "orgId": org, "connectorName": "ATTACHMENTS", "recordType": "FILE",
        "externalRecordId": blob.store(org, b"u"), "virtualRecordId": f"vr-{upload}", "awaitingSend": False,
    }
    graph.nodes["files"][upload] = {"_key": upload, "id": upload, "orgId": org}
    blob.store(org, b"blocks", virtual_record_id=f"vr-{upload}")
    graph.nodes["records"][f"{org}-knowledge"] = {
        "_key": f"{org}-knowledge", "id": f"{org}-knowledge", "orgId": org, "connectorName": "KB", "recordType": "FILE",
    }
    await GraphSkillStore(graph, org, "u1").queue_candidate(  # type: ignore[arg-type]
        SkillCandidate(
            candidate_id=f"{org}-candidate", name="c", description="d", body="b", source_session_id="conv",
            source_trajectory_summary="s", created_at="now",
        )
    )
    versions = AgentVersionStore(graph)  # type: ignore[arg-type]
    for prompt in ("first", "second"):
        await versions.publish(org, f"{org}-agent", {"name": "a", "systemPrompt": prompt}, published_by="u1")


async def test_cleanup_g1_the_org_erase_takes_the_tokens_orgs_harness_data_page_by_page_and_nothing_else() -> None:
    from app.api.routes.chatbot import erase_org

    graph, blob = FakeGraphProvider(), FakeBlobStore()
    await _seed(graph, blob, "org-1")
    await _seed(graph, blob, "org-2")
    org2_blobs = {k for k, d in blob.documents.items() if d["org_id"] == "org-2"}

    totals = {"attachments": 0, "artifacts": 0, "skillCandidates": 0, "agentVersions": 0}
    with patch("app.api.routes.chatbot.BlobStorage", return_value=blob):
        for _ in range(20):
            out = await authed(erase_org)(_request({"pageLimit": 1}), graph, MagicMock(), {"orgId": "org-1"})
            for name in totals:
                totals[name] += out[name]
            if not out["remaining"]:
                break
        again = await authed(erase_org)(_request({}), graph, MagicMock(), {"orgId": "org-1"})

    assert totals == {"attachments": 1, "artifacts": 3, "skillCandidates": 1, "agentVersions": 2}
    assert again == {
        "attachments": 0, "artifacts": 0, "blobs": 0, "skillCandidates": 0, "agentVersions": 0, "remaining": False,
    }
    assert sorted(k for k, d in graph.nodes["records"].items() if d.get("orgId") == "org-1") == ["org-1-knowledge"]
    assert not [k for k in graph.nodes["artifacts"] if k.startswith("org-1")]
    assert not [k for k in graph.nodes[CANDIDATES] if k.startswith("org-1")]
    assert sorted(graph.nodes[VERSIONS]) == ["org-2-agent-v1", "org-2-agent-v2"]
    assert {k for k, d in blob.documents.items() if d["org_id"] == "org-1"} == set()
    assert {k for k, d in blob.documents.items() if d["org_id"] == "org-2"} == org2_blobs
    assert len([k for k in graph.nodes["records"] if k.startswith("org-2")]) == 4
    assert len([k for k in graph.nodes["artifacts"] if k.startswith("org-2")]) == 3


@pytest.mark.parametrize("body", [{"orgId": "org-2"}, {"pageLimit": 0}, {"pageLimit": 201}, {"pageLimit": "x"}, []])
async def test_cleanup_g1_the_org_erase_refuses_a_body_naming_anything_but_a_page_size(body: object) -> None:
    from app.api.routes.chatbot import erase_org

    with pytest.raises(HTTPException) as refused:
        await authed(erase_org)(_request(body), FakeGraphProvider(), MagicMock(), {"orgId": "org-1"})
    assert refused.value.status_code == 400


@pytest.mark.parametrize("route", ["erase_org", "erase_traces"])
async def test_cleanup_g1_a_token_without_an_org_is_refused(route: str) -> None:
    from app.api.routes import chatbot

    with pytest.raises(HTTPException) as refused:
        if route == "erase_org":
            await authed(chatbot.erase_org)(_request({}), FakeGraphProvider(), MagicMock(), {"scopes": ["conversation:erase"]})
        else:
            await authed(chatbot.erase_traces)(_request({"conversationId": "c", "runIds": []}), {"scopes": ["conversation:erase"]})
    assert refused.value.status_code == 400


def _token(user: dict[str, Any] | None) -> MagicMock:
    request = MagicMock()
    request.state.user = user
    return request


@pytest.mark.parametrize("route", ["erase_org", "erase_traces"])
async def test_cleanup_g1_only_nodes_conversation_erase_service_token_may_call(route: str) -> None:
    from app.api.routes import chatbot

    check = inspect.signature(getattr(chatbot, route)).parameters["claims"].default.dependency
    erase = {"token_type": "scoped", "orgId": "org-1", "scopes": [TokenScopes.CONVERSATION_ERASE.value]}
    assert await check(_token(erase)) == erase
    for refused_user in (
        None,
        {"token_type": "regular", "orgId": "org-1", "userId": "u1"},
        {"token_type": "scoped", "orgId": "org-1", "scopes": [TokenScopes.CONVERSATION_CREATE.value]},
    ):
        with pytest.raises(HTTPException) as refused:
            await check(_token(refused_user))
        assert refused.value.status_code in (401, 403)


class FakeOpik:
    """Traces as Opik keeps them: id, thread and metadata; the filters the eraser sends are recorded."""

    def __init__(self) -> None:
        self.traces: dict[str, dict[str, Any]] = {}
        self.threads: set[str] = set()
        self.filters: list[str] = []
        self.fail = False

    def add(self, trace_id: str, *, thread: str | None = None, run: str | None = None, org: str = "org-1") -> None:
        self.traces[trace_id] = {"thread": thread, "run": run, "org": org}
        if thread:
            self.threads.add(thread)

    def find(self, filter_string: str, limit: int) -> list[str]:
        if self.fail:
            raise ConnectionError("opik down")
        self.filters.append(filter_string)
        keys = {"thread_id": "thread", "metadata.run_id": "run", "metadata.org_id": "org"}
        wanted = {}
        for clause in filter_string.split(" and "):
            field, _, value = clause.partition(" = ")
            wanted[keys[field]] = value.strip('"')
        return [tid for tid, t in self.traces.items() if all(t[k] == v for k, v in wanted.items())][:limit]

    def delete(self, trace_ids: list[str]) -> None:
        for tid in trace_ids:
            self.traces.pop(tid, None)

    def delete_threads(self, thread_ids: list[str]) -> None:
        self.threads -= set(thread_ids)


async def test_opik_a_conversations_traces_are_deleted_by_its_thread_and_its_runs_and_no_other() -> None:
    from app.api.routes.chatbot import erase_traces

    opik = FakeOpik()
    opik.add("t1", thread="conv-1", run="run-1")
    opik.add("t2", thread="conv-1", run="run-2")
    opik.add("t3", run="run-1")
    opik.add("t4", thread="conv-2", run="run-9")
    with patch("app.api.routes.chatbot.opik_trace_store", return_value=opik):
        out = await authed(erase_traces)(_request({"conversationId": "conv-1", "runIds": ["run-1", "run-2"]}), {"orgId": "org-1"})
        again = await authed(erase_traces)(_request({"conversationId": "conv-1", "runIds": ["run-1", "run-2"]}), {"orgId": "org-1"})
    assert out == {"traces": 3, "configured": True}
    assert again == {"traces": 0, "configured": True}
    assert list(opik.traces) == ["t4"]
    assert opik.threads == {"conv-2"}


async def test_opik_a_token_of_one_org_never_deletes_another_orgs_traces() -> None:
    from app.api.routes.chatbot import erase_traces

    opik = FakeOpik()
    opik.add("mine", thread="conv-1", run="run-1", org="org-1")
    opik.add("theirs", thread="conv-9", run="run-9", org="org-2")
    with patch("app.api.routes.chatbot.opik_trace_store", return_value=opik):
        out = await authed(erase_traces)(_request({"conversationId": "conv-9", "runIds": ["run-9"]}), {"orgId": "org-1"})
    assert out == {"traces": 0, "configured": True}
    assert set(opik.traces) == {"mine", "theirs"}
    assert opik.threads == {"conv-1", "conv-9"}
    assert all('metadata.org_id = "org-1"' in f for f in opik.filters)


async def test_opik_off_answers_not_configured_without_asking_anyone() -> None:
    from app.api.routes.chatbot import erase_traces

    with patch("app.api.routes.chatbot.opik_trace_store", return_value=None):
        out = await authed(erase_traces)(_request({"conversationId": "conv-1", "runIds": ["run-1"]}), {"orgId": "org-1"})
    assert out == {"traces": 0, "configured": False}


async def test_opik_failing_is_a_503_the_erasure_step_retries() -> None:
    from app.api.routes.chatbot import erase_traces

    opik = FakeOpik()
    opik.fail = True
    with patch("app.api.routes.chatbot.opik_trace_store", return_value=opik), pytest.raises(HTTPException) as failed:
        await authed(erase_traces)(_request({"conversationId": "conv-1", "runIds": []}), {"orgId": "org-1"})
    assert failed.value.status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"conversationId": 'c" or thread_id != "x', "runIds": []},
        {"conversationId": "c", "runIds": ['r" or 1=1']},
        {"conversationId": "c", "runIds": [f"r{n}" for n in range(51)]},
        {"conversationId": "c", "runIds": [], "orgId": "org-2"},
        {"runIds": []},
        {"conversationId": "", "runIds": []},
    ],
)
async def test_opik_the_trace_erase_refuses_anything_that_is_not_plain_ids(body: object) -> None:
    from app.api.routes.chatbot import erase_traces

    with patch("app.api.routes.chatbot.opik_trace_store", return_value=FakeOpik()), pytest.raises(HTTPException) as refused:
        await authed(erase_traces)(_request(body), {"orgId": "org-1"})
    assert refused.value.status_code == 400
