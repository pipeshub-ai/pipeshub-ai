"""R33JGAPS-FU-6: a file a person attaches in a chat with a service-account agent is read as theirs.

The run of a service-account agent acts as the agent's account, but the person chatting attached the file: attaching
is their grant to the run they started. On the base the read was checked as the account, which holds no grant on a
member's upload, so the file was dropped (``run.attachments resolved=False``) and the answer read complete. The grant
is that person's and this conversation's only: another person's file, or the same file in another person's run of the
agent, stays unreadable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.attachment_resolver import resolve_attachments_for_goal
from app.harness.knowledge.caller import UNRESOLVED_CALLER
from app.utils.record_access import (
    SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE,
    attachment_reader_id,
)
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from app.harness.assembly.context import Attachment

ORG = "org-1"
ACCOUNT = "sa-agent-1"
LOG = logging.getLogger("test_r34fix_service_account_attachments")


def _attachment(name: str, vrid: str) -> dict[str, Any]:
    return {"recordName": name, "mimeType": "text/plain", "virtualRecordId": vrid, "extension": "txt"}


def _stored(vrid: str, text: str) -> dict[str, Any]:
    return {
        "id": f"rec-{vrid}",
        "virtual_record_id": vrid,
        "context_metadata": text,
        "block_containers": {"blocks": [{"index": 0, "type": "text", "data": text}]},
    }


class _Reader:
    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self._records = records

    async def read_record(self, vrid: str, org_id: str, **_: object) -> dict[str, Any] | None:
        return self._records.get(vrid) if org_id == ORG else None


def _world(*, org_edge_on: tuple[str, ...] = ()) -> tuple[Any, dict[str, dict[str, Any]]]:
    """Two members, each with their own chat upload, and the service account's own upload (granted to the org)."""
    access = (
        InMemoryRecordAccess()
        .readable("alice", ORG, "rec-alice")
        .readable("bob", ORG, "rec-bob")
        .user(ACCOUNT, ORG)
        .record("rec-account", ORG)
    )
    graph = access.serve(AsyncMock())
    graph.get_records_by_virtual_record_id.side_effect = lambda vrid, **_: [f"rec-{vrid}"]

    async def edge(*, from_id: str, to_id: str, **_: object) -> dict[str, Any] | None:
        if from_id == ORG and to_id in {f"rec-{v}" for v in org_edge_on}:
            return {"type": SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE, "role": "READER"}
        return None

    graph.get_edge.side_effect = edge
    records = {v: _stored(v, f"text of {v}'s file") for v in ("alice", "bob", "account")}
    return graph, records


def _service_account_chat(graph: AsyncMock, caller: str | None, attachments: list[dict[str, Any]]) -> AgentContext:
    """The context a service-account agent's run builds: it acts as the account; the person is ``callerUserId``."""
    user_info: dict[str, Any] = {"userId": ACCOUNT, "orgId": ORG}
    if caller is not None:
        user_info["callerUserId"] = caller
    return AgentContext(
        org_id=ORG,
        user_id=ACCOUNT,
        user_email="agent@example.com",
        user_info=user_info,
        is_service_account=True,
        conversation_id="conv-1",
        graph_provider=graph,
        blob_store=object(),
        tool_state={"attachments": attachments},
    )


async def _read(context: AgentContext, records: dict[str, dict[str, Any]]) -> str:
    with patch("app.utils.attachment_utils.record_artifact_reader", return_value=_Reader(records)):
        text, _ = await resolve_attachments_for_goal(context, LOG)
    return text


@pytest.mark.asyncio
async def test_the_person_s_own_attachment_reaches_the_service_account_run() -> None:
    graph, records = _world()

    text = await _read(_service_account_chat(graph, "alice", [_attachment("notes.txt", "alice")]), records)

    assert "text of alice's file" in text


@pytest.mark.asyncio
async def test_another_person_s_file_stays_unreadable() -> None:
    graph, records = _world()

    text = await _read(_service_account_chat(graph, "alice", [_attachment("theirs.txt", "bob")]), records)

    assert "text of bob's file" not in text


@pytest.mark.asyncio
async def test_the_service_account_s_other_runs_cannot_read_it() -> None:
    graph, records = _world()

    text = await _read(_service_account_chat(graph, "bob", [_attachment("notes.txt", "alice")]), records)

    assert "text of alice's file" not in text


@pytest.mark.asyncio
async def test_a_run_with_no_person_behind_it_reads_only_the_account_s_own_uploads() -> None:
    graph, records = _world(org_edge_on=("account",))
    attachments = [_attachment("bot.txt", "account"), _attachment("notes.txt", "alice")]

    text = await _read(_service_account_chat(graph, UNRESOLVED_CALLER, attachments), records)

    assert "text of account's file" in text
    assert "text of alice's file" not in text


def test_the_reader_is_the_person_behind_a_service_account_and_otherwise_the_user() -> None:
    assert attachment_reader_id(ACCOUNT, {"callerUserId": "alice"}) == "alice"
    assert attachment_reader_id(ACCOUNT, {"callerUserId": UNRESOLVED_CALLER}) == ACCOUNT
    assert attachment_reader_id("alice", {}) == "alice"
    assert attachment_reader_id("alice", None) == "alice"


# --- the fallback: a file still not read is named in the answer (ADR-RUN-03 D5) ------------------------------------


def _named(name: str, vrid: str | None, mime: str = "text/plain") -> Attachment:
    from app.harness.assembly.context import Attachment

    return Attachment(record_id=f"rec-{vrid}", record_name=name, mime_type=mime, virtual_record_id=vrid)


def test_each_file_the_model_was_not_given_is_named_with_why() -> None:
    from app.harness.assembly.attachment_limits import unread_attachments

    named = [
        _named("read.txt", "v1"),
        _named("refused.txt", "v2"),
        _named("empty.txt", "v3"),
        _named("photo.png", "v4", "image/png"),
    ]
    kept = [{"virtualRecordId": "v1"}, {"virtualRecordId": "v3"}, {"virtualRecordId": "v4"}]

    items = unread_attachments(named, kept, {"v1": {}}, multimodal=False)

    assert items == [
        {"kind": "attachment.not_accessible", "file": "refused.txt"},
        {"kind": "attachment.unreadable", "file": "empty.txt"},
    ]
    assert {"kind": "attachment.unreadable", "file": "photo.png"} in unread_attachments(
        named, kept, {"v1": {}}, multimodal=True
    )


def _answer(unread: tuple[list[dict[str, Any]], bool], items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from app.harness.presentation import with_limits

    data: dict[str, Any] = {"answer": "Here is the summary.", "partTexts": ["Here is the summary."]}
    with_limits(data, items or [], unread)
    return data


def test_the_answer_leads_with_the_file_it_could_not_read() -> None:
    data = _answer(([{"kind": "attachment.not_accessible", "file": "notes.txt"}], True))

    assert data["answer"].startswith("I couldn't read the attached file notes.txt: access to it was refused.")
    assert data["limits"]["items"] == [{"kind": "attachment.not_accessible", "file": "notes.txt"}]
    assert data["partTexts"] == [data["answer"]]


def test_a_node_that_cannot_read_the_kind_gets_the_line_only() -> None:
    clone = {"kind": "repository.declined", "repository": "github.com/a/b"}
    data = _answer(([{"kind": "attachment.unreadable", "file": "notes.txt"}], False), [clone])

    assert data["limits"]["items"] == [clone]
    assert data["answer"].startswith(data["limits"]["line"])
    assert "I couldn't read the attached file notes.txt: its content could not be read." in data["answer"]


def test_the_contract_carries_the_attachment_kinds_with_the_file() -> None:
    from app.harness.contracts.generated.pipeshub.could_not_do import PipeshubCouldNotDo

    item = PipeshubCouldNotDo.model_validate({"kind": "attachment.unreadable", "file": "notes.txt"})

    assert item.model_dump(mode="json", exclude_none=True) == {"kind": "attachment.unreadable", "file": "notes.txt"}
