"""The previous-document heal after a content change: best effort, isolated, and never steered by tags."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from app.connectors.sources.localKB.push import service as SV
from app.connectors.sources.localKB.push.service import push_record_key

from .image_harness import KB, ORG, did, image_body, make_harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .image_harness import Harness


def key() -> str:
    return push_record_key(ORG, KB, "k1")


@pytest.fixture
async def h(tmp_path) -> AsyncIterator[Harness]:
    harness = await make_harness(tmp_path)
    log = harness.cleanups

    async def cleanup(
        org, kb, ids
    ) -> None:  # the real consumer purges a document soon after the event
        log.append((org, kb, list(ids)))
        harness.docs.purged.update(ids)

    harness.svc._cleanup = cleanup
    SV.HEAL.clear()
    yield harness
    await harness.store.stop()


async def version(h, v: int) -> object:
    return await h.push(image_body([], version=v, text=f"content {v}"))


def doc_of(h) -> str:
    return h.graph.record["externalRecordId"]


async def test_a_leaked_document_is_cleaned_by_the_next_content_change(h) -> None:
    await version(h, 1)
    e1 = doc_of(h)
    await version(h, 2)
    e2 = doc_of(h)
    assert (
        h.docs.created[e2]["previousDocumentId"] == e1
        and "previousDocumentId" not in h.docs.created[e1]
    )
    h.docs.purged.discard(e1)  # the cleanup after v2 never ran: a crash left E1 behind
    h.cleanups.clear()
    await version(h, 3)
    assert h.cleanups == [(ORG, KB, [e2, e1])]
    assert (
        SV.HEAL["cleaned"] == 1
        and h.docs.created[doc_of(h)]["previousDocumentId"] == e2
    )


async def test_heal_survives_the_consumer_purging_the_old_document(h) -> None:
    """The superseded document is purged the moment its cleanup is published; its tags must have been read before."""
    await version(h, 1)
    e1 = doc_of(h)
    await version(h, 2)
    h.docs.purged.discard(e1)
    await version(h, 3)
    assert e1 in h.cleanups[-1][2] and h.docs.created[doc_of(h)]


async def test_no_leak_means_only_the_superseded_document_is_cleaned(h) -> None:
    await version(h, 1)
    e1 = doc_of(h)
    await version(h, 2)
    assert h.cleanups == [(ORG, KB, [e1])]  # E1 had no previous; nothing else to read
    await version(h, 3)
    assert h.cleanups[-1][2] == [doc_of_prev(h, 2)]
    assert SV.HEAL["cleaned"] == 0


def doc_of_prev(h, v: int) -> str:
    return h.cleanups[-1][2][0]


async def test_a_create_and_an_unchanged_push_read_nothing(h) -> None:
    await version(h, 1)
    await version(h, 1)
    assert h.docs.single_infos == []


@pytest.mark.parametrize("failure", ["raises", "hangs", "none"])
async def test_heal_failure_never_changes_the_answer(h, failure, monkeypatch) -> None:
    await version(h, 1)
    await version(h, 2)
    if failure == "raises":
        h.docs.single_info_error = RuntimeError("storage down")
    elif failure == "hangs":
        monkeypatch.setattr(SV, "HEAL_TIMEOUT_S", 0.1)

        async def hang(*a, **kw) -> None:
            await asyncio.sleep(30)

        h.docs.info = hang
    else:

        async def gone(*a, **kw) -> None:
            return None

        h.docs.info = gone
    started = time.monotonic()
    result = await version(h, 3)
    assert (result.status, result.http_status, result.content_changed) == (
        "updated",
        200,
        True,
    )
    assert time.monotonic() - started < 5
    assert SV.HEAL["failed" if failure != "none" else "skipped"] >= 1
    assert h.cleanups[-1][2] == [h.cleanups[-1][2][0]]  # only the superseded document


async def test_a_cancelled_heal_is_not_swallowed(h) -> None:
    await version(h, 1)
    await version(h, 2)

    async def cancelled(*a, **kw) -> None:
        raise asyncio.CancelledError

    h.docs.info = cancelled
    with pytest.raises(asyncio.CancelledError):
        await version(h, 3)


async def seed_two_versions(h) -> tuple[str, str]:
    await version(h, 1)
    e1 = doc_of(h)
    await version(h, 2)
    h.docs.purged.discard(e1)
    h.cleanups.clear()
    return e1, doc_of(h)


@pytest.mark.parametrize(
    "forge", ["e_wrong_key", "e_wrong_kb", "p_other_record", "p_untagged", "p_other_kb"]
)
async def test_user_written_tags_cannot_steer_a_delete(h, forge) -> None:
    """Anyone who can upload sets ``customMetadata``; a forged tag must never make the push delete another record's document."""
    e1, e2 = await seed_two_versions(h)
    victim = did(99)
    if forge == "e_wrong_key":
        h.docs.created[e2]["pushRecordKey"] = "another-records-key"
    elif forge == "e_wrong_kb":
        h.docs.created[e2]["connectorId"] = "another-kb"
    elif (
        forge == "p_other_record"
    ):  # P is a real document of ANOTHER record (tags are that record's)
        h.docs.created[e2]["previousDocumentId"] = victim
        h.docs.put(99, tags={"connectorId": KB, "pushRecordKey": "victim-record-key"})
    elif forge == "p_untagged":  # P is someone's ordinary upload
        h.docs.created[e2]["previousDocumentId"] = victim
        h.docs.put(99)
    else:
        h.docs.created[e2]["previousDocumentId"] = victim
        h.docs.put(99, tags={"connectorId": "another-kb", "pushRecordKey": key()})
    await version(h, 3)
    deleted = {d for _, _, ids in h.cleanups for d in ids}
    assert victim not in deleted
    assert (
        (e1 in deleted) is False and e2 in deleted
    )  # a forged chain heals nothing; the superseded document is still cleaned


async def test_a_previous_document_id_that_is_not_a_plain_id_is_never_read(h) -> None:
    e1, e2 = await seed_two_versions(h)
    h.docs.created[e2]["previousDocumentId"] = "../../x"
    await version(h, 3)
    assert "../../x" not in h.docs.single_infos
    assert all("../../x" not in ids for _, _, ids in h.cleanups)


async def test_a_previous_document_in_another_org_is_unknown_to_org_scoped_storage_and_is_ignored(
    h,
) -> None:
    e1, e2 = await seed_two_versions(h)
    h.docs.created[e2]["previousDocumentId"] = (
        "docOfAnotherOrg0000001"  # storage answers None: not this org's
    )
    await version(h, 3)
    assert not any("docOfAnotherOrg0000001" in ids for _, _, ids in h.cleanups)


async def test_a_stale_tag_after_a_lost_attempt_leaks_at_most_one_and_never_the_live_document(
    h,
) -> None:
    """A racing push replaced the record's document between attempts: the new document's tag names the older one."""
    from app.connectors.sources.localKB.push.service import PushRecordSkipped

    await version(h, 1)
    e1 = doc_of(h)
    real_claim = h.svc._claim_and_write
    state = {"first": True}

    async def racing(
        processor, existing, action, v, key_, kb, org, user, parent, new_doc, revision
    ) -> object:
        if state["first"] and action == "content":
            state["first"] = False
            h.docs.created["docRacing00000000000001"] = {
                "connectorId": KB,
                "pushRecordKey": key(),
                "previousDocumentId": e1,
            }
            h.graph.record = {
                **h.graph.record,
                "externalRecordId": "docRacing00000000000001",
            }
            raise PushRecordSkipped(key_)
        return await real_claim(
            processor,
            existing,
            action,
            v,
            key_,
            kb,
            org,
            user,
            parent,
            new_doc,
            revision,
        )

    h.svc._claim_and_write = racing
    result = await version(h, 2)
    assert result.status == "updated"
    live = doc_of(h)
    assert all(live not in ids for _, _, ids in h.cleanups), (
        "the document the record now names is never cleaned"
    )


async def test_a_tag_naming_the_document_itself_publishes_nothing_extra(h) -> None:
    """A self-referencing ``previousDocumentId`` must not make the superseded document be published twice or count as a heal."""
    e1, e2 = await seed_two_versions(h)
    h.docs.created[e2]["previousDocumentId"] = e2
    await version(h, 3)
    assert h.cleanups == [(ORG, KB, [e2])] and SV.HEAL["cleaned"] == 0
