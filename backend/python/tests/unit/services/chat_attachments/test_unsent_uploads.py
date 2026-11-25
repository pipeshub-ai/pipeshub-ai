"""CLEANUP-G5: a chat attachment no message carried is erased with its conversation, and found for Node to collect.

An upload is written awaiting a message. A conversation's erasure takes the uploads made into it that still await
one; a sent upload is left to the message-based erase, which also marks it removed in forks. Node's collector lists
the org's old awaiting uploads, marks those a message names as sent and erases the rest. An upload written before
the marker existed is never listed.
"""

from __future__ import annotations

import pytest

from app.services.chat_attachments.erasure import (
    AttachmentEraser,
    AttachmentRef,
    UnsentUpload,
    list_unsent,
)
from app.services.chat_attachments.uploads import (
    AWAITING_SEND_FIELD,
    UPLOAD_CONVERSATION_FIELD,
    AttachmentFile,
    Uploader,
    UploaderKind,
    attachment_documents,
    mark_sent,
)
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

HOUR_MS = 3_600_000
NOW = 1_800_000_000_000
PERSON = Uploader("u1", UploaderKind.PERSON)


async def _no_owner(user_id: str, record_ids: list[str]) -> set[str]:
    return set()


async def _upload(
    graph: FakeGraphProvider,
    blobs: FakeBlobStore,
    org: str,
    key: str,
    *,
    at: int = NOW,
    conversation: str | None = None,
    tracked: bool = True,
) -> str:
    graph.add_user("u1", key="user-key-1")
    vr = f"vr-{key}"
    document_id = blobs.store(org, b"the file")
    blobs.store(org, b"its blocks", virtual_record_id=vr)
    record, file = attachment_documents(
        org,
        AttachmentFile(
            record_id=key, virtual_record_id=vr, file_name=f"{key}.txt", mime_type="text/plain", size=8,
            extension="txt", external_record_id=document_id,
        ),
        PERSON,
        at,
        tracked=tracked,
        conversation_id=conversation,
    )
    # The fake keeps edges in the generic from/to form; write_attachment_graph's real-provider path is
    # covered by test_erasure_real_graph.py.
    await graph.batch_upsert_nodes([record], "records")
    await graph.batch_upsert_nodes([file], "files")
    await graph.batch_create_edges(
        [{"from_id": "user-key-1", "from_collection": "users", "to_id": key, "to_collection": "records", "role": "OWNER"}],
        "permission",
    )
    return key


def _eraser(graph: FakeGraphProvider, blobs: FakeBlobStore, page: int = 50) -> AttachmentEraser:
    return AttachmentEraser(graph, blobs, _no_owner, page=page)


def _gone(graph: FakeGraphProvider, blobs: FakeBlobStore, key: str) -> bool:
    return (
        key not in graph.nodes["records"]
        and key not in graph.nodes["files"]
        and not graph.edges_touching(key)
        and not any(d.get("virtual_record_id") == f"vr-{key}" for d in blobs.documents.values())
    )


def test_cleanup_g5_an_upload_is_written_awaiting_a_message_and_names_its_conversation() -> None:
    file = AttachmentFile(
        record_id="r", virtual_record_id="v", file_name="a.txt", mime_type="text/plain", size=1, extension="txt",
        external_record_id="d",
    )
    into, _ = attachment_documents("org-1", file, PERSON, 1, tracked=True, conversation_id="conv-1")
    fresh, _ = attachment_documents("org-1", file, PERSON, 1, tracked=True)
    assert (into[AWAITING_SEND_FIELD], into[UPLOAD_CONVERSATION_FIELD]) == (True, "conv-1")
    # A new chat has no conversation yet; Neo4j stores no nulls, so there is no field.
    assert fresh[AWAITING_SEND_FIELD] is True and UPLOAD_CONVERSATION_FIELD not in fresh


@pytest.mark.asyncio
async def test_cleanup_g5_marking_sent_touches_only_the_orgs_uploads_and_records_the_conversation() -> None:
    graph, blobs = FakeGraphProvider(), FakeBlobStore()
    await _upload(graph, blobs, "org-1", "mine")
    await _upload(graph, blobs, "org-2", "theirs")
    graph.add_record("kb-record", org_id="org-1")

    marked = await mark_sent(graph, "org-1", ["mine", "mine", "theirs", "kb-record", "missing"], "conv-9")

    assert marked == ["mine"]
    assert graph.nodes["records"]["mine"][AWAITING_SEND_FIELD] is False
    assert graph.nodes["records"]["mine"][UPLOAD_CONVERSATION_FIELD] == "conv-9"
    assert graph.nodes["records"]["theirs"][AWAITING_SEND_FIELD] is True
    assert AWAITING_SEND_FIELD not in graph.nodes["records"]["kb-record"]
    assert await mark_sent(graph, "org-1", ["mine"], "conv-other") == ["mine"]
    assert graph.nodes["records"]["mine"][UPLOAD_CONVERSATION_FIELD] == "conv-9"


@pytest.mark.asyncio
async def test_cleanup_g5_a_conversations_erasure_takes_only_its_uploads_no_message_carried() -> None:
    graph, blobs = FakeGraphProvider(), FakeBlobStore()
    for key in ("a", "b", "c"):
        await _upload(graph, blobs, "org-1", key, conversation="conv-1")
    await mark_sent(graph, "org-1", ["a"], "conv-1")
    await _upload(graph, blobs, "org-1", "other-chat", conversation="conv-2")
    await _upload(graph, blobs, "org-2", "other-org", conversation="conv-1")
    eraser = _eraser(graph, blobs, page=1)

    first = await eraser.erase_conversation_uploads("org-1", "conv-1")
    second = await eraser.erase_conversation_uploads("org-1", "conv-1")
    third = await eraser.erase_conversation_uploads("org-1", "conv-1")

    assert (first.attachments, first.remaining, second.attachments, second.remaining) == (1, True, 1, False)
    assert (third.attachments, third.remaining) == (0, False)
    assert _gone(graph, blobs, "b") and _gone(graph, blobs, "c")
    # Sent: the message-based erase takes it, and marks it removed in forks that carry the message.
    assert {"a", "other-chat", "other-org"} <= set(graph.nodes["records"])


@pytest.mark.asyncio
async def test_cleanup_g5_listing_names_the_orgs_old_unsent_uploads_with_their_uploader() -> None:
    graph, blobs = FakeGraphProvider(), FakeBlobStore()
    await _upload(graph, blobs, "org-1", "k1-old", at=NOW - 30 * HOUR_MS, conversation="conv-1")
    await _upload(graph, blobs, "org-1", "k2-new-chat", at=NOW - 30 * HOUR_MS)
    await _upload(graph, blobs, "org-1", "k3-sent", at=NOW - 30 * HOUR_MS)
    await mark_sent(graph, "org-1", ["k3-sent"], "conv-1")
    await _upload(graph, blobs, "org-1", "k4-recent", at=NOW)
    await _upload(graph, blobs, "org-1", "k5-legacy", at=NOW - 30 * HOUR_MS)
    graph.nodes["records"]["k5-legacy"].pop(AWAITING_SEND_FIELD)
    await _upload(graph, blobs, "org-2", "k6-other-org", at=NOW - 30 * HOUR_MS)

    page = await list_unsent(graph, "org-1", NOW - HOUR_MS, 50)

    assert page.uploads == (UnsentUpload("k1-old", "u1", "conv-1"), UnsentUpload("k2-new-chat", "u1", None))
    assert (page.remaining, page.next) == (False, None)
    assert len(graph.nodes["records"]) == 6, "listing erases nothing"


@pytest.mark.asyncio
async def test_cleanup_g5_listing_pages_by_key_and_reads_a_bounded_number_of_pages() -> None:
    graph, blobs = FakeGraphProvider(), FakeBlobStore()
    for i in range(5):
        await _upload(graph, blobs, "org-1", f"old-{i}", at=NOW - 30 * HOUR_MS)
    for i in range(5):
        await _upload(graph, blobs, "org-1", f"recent-{i}", at=NOW)

    first = await list_unsent(graph, "org-1", NOW - HOUR_MS, 2, page=2)
    second = await list_unsent(graph, "org-1", NOW - HOUR_MS, 2, after=first.next, page=2)
    third = await list_unsent(graph, "org-1", NOW - HOUR_MS, 2, after=second.next, page=2)
    assert [u.record_id for page in (first, second, third) for u in page.uploads] == [f"old-{i}" for i in range(5)]
    assert (first.remaining, second.remaining) == (True, True)

    # Only recent uploads past the cursor: one call stops after its pages, saying where to go on from.
    bounded = await list_unsent(graph, "org-1", NOW - HOUR_MS, 2, after="old-4", page=1, pages=2)
    assert (bounded.uploads, bounded.remaining, bounded.next) == ((), True, "recent-1")
    done = await list_unsent(graph, "org-1", NOW - HOUR_MS, 2, after="recent-4", page=1)
    assert (done.uploads, done.remaining, done.next) == ((), False, None)


UPLOAD_FIELDS = {"awaitingSend", "uploadConversationId", "uploaderAgentKey", "uploaderKind", "uploaderUserId"}


def test_with_the_harness_off_an_upload_is_written_without_the_upload_fields() -> None:
    file = AttachmentFile(
        record_id="r", virtual_record_id="v", file_name="a.txt", mime_type="text/plain", size=1, extension="txt",
        external_record_id="d",
    )
    service = Uploader("p1", UploaderKind.SERVICE_ACCOUNT, "agent-7")
    for uploader in (PERSON, service):
        off, off_file = attachment_documents("org-1", file, uploader, 1, tracked=False, conversation_id="conv-1")
        on, on_file = attachment_documents("org-1", file, uploader, 1, tracked=True, conversation_id="conv-1")
        assert not UPLOAD_FIELDS & set(off)
        assert {k: v for k, v in on.items() if k not in UPLOAD_FIELDS} == off
        assert off_file == on_file


# ADR-OPS-08: an upload is tracked where the harness is on for the org: the switch (unset or blank is on since
# 2026-11-20) and the org's Labs flag. The switch on with Labs off is the switch off.
@pytest.mark.parametrize(
    ("raw", "labs", "expected"),
    [(None, True, True), ("", True, True), ("true", True, True), ("false", True, False),
     (None, False, False), ("true", False, False), ("false", False, False)],
)
@pytest.mark.asyncio
async def test_uploads_are_tracked_only_where_the_harness_is_on_for_the_org(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, labs: bool, expected: bool,
) -> None:
    from app.services.chat_attachments.uploads import uploads_tracked
    from app.services.featureflag.config.config import CONFIG
    from tests.support.config_service import FakeConfigurationService

    if raw is None:
        monkeypatch.delenv("AGENT_HARNESS_WORKER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", raw)
    config = FakeConfigurationService(flags={CONFIG.ENABLE_AGENT_HARNESS: labs})
    assert await uploads_tracked(config) is expected


@pytest.mark.asyncio
async def test_with_the_harness_off_the_unsent_collector_and_conversation_sweep_leave_uploads_alone() -> None:
    graph, blobs = FakeGraphProvider(), FakeBlobStore()
    await _upload(graph, blobs, "org-1", "old", at=NOW - 30 * HOUR_MS, conversation="conv-1", tracked=False)
    await _upload(graph, blobs, "org-1", "new-chat", at=NOW - 30 * HOUR_MS, tracked=False)
    before = {key: dict(doc) for key, doc in graph.nodes["records"].items()}
    documents = dict(blobs.documents)

    page = await list_unsent(graph, "org-1", NOW, 50)
    swept = await _eraser(graph, blobs).erase_conversation_uploads("org-1", "conv-1")
    marked = await mark_sent(graph, "org-1", ["old", "new-chat"], "conv-1")

    assert (page.uploads, page.remaining) == ((), False)
    assert (swept.attachments, swept.blobs, swept.remaining) == (0, 0, False)
    assert marked == []
    assert graph.nodes["records"] == before, "nothing is written onto an untracked upload"
    assert blobs.documents == documents


@pytest.mark.asyncio
async def test_with_the_harness_off_a_members_upload_is_erased_by_its_owner_edge_as_before() -> None:
    graph, blobs = FakeGraphProvider(), FakeBlobStore()
    await _upload(graph, blobs, "org-1", "mine", tracked=False)

    async def owned_by(user_id: str, record_ids: list[str]) -> set[str]:
        return set(record_ids) if user_id == "u1" else set()

    eraser = AttachmentEraser(graph, blobs, owned_by)
    refused = await eraser.erase("org-1", [AttachmentRef("mine", "someone-else")])
    erased = await eraser.erase("org-1", [AttachmentRef("mine", "u1")])

    assert (refused.attachments, refused.refused, refused.unrecorded) == (0, 1, 1)
    assert (erased.attachments, erased.removed) == (1, ("mine",))
    assert _gone(graph, blobs, "mine")
