"""The trash on the live stack: delete, restore and purge a collection upload through the API.

With the Labs setting "Move Deleted Records to the Trash" (``ENABLE_SOFT_DELETE``)
on, a record delete hides the record and removes only its search vectors. The
graph node, the uploaded file and its MongoDB storage documents stay until the
purge, so a restore can bring the record back under the same id and index it
again.

The setting applies to the whole deployment, so a hard-delete test running
beside this one would see the trash instead. That is why this module carries the
``resilience`` marker, like the Labs rebuild tests, and runs on its own
(``-m resilience``). It turns the setting on for its own run and puts it back.

The purge test runs only on a stack whose connectors service shortens the purge
(``SOFT_DELETE_PURGE_MIN_AGE_SECONDS=0``, ``SOFT_DELETE_PURGE_INTERVAL_SECONDS=0``,
``SOFT_DELETE_PURGE_TICK_SECONDS`` and ``SOFT_DELETE_PURGE_STARTUP_GRACE_SECONDS``
small); set ``PIPESHUB_IT_TRASH_PURGE_TIMEOUT_SEC`` to say so.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, AsyncGenerator, Iterator

import pytest
import pytest_asyncio

from helper.cleanup_sources import (
    INDEXING_TIMEOUT,
    wait_for_embeddings,
    wait_for_virtual_id,
)
from helper.clients.kb_client import KBClient
from helper.indexing_progress import wait_until_enriched
from helper.mongo_store import records_folder
from helper.vector_rebuild import read_platform_settings, write_platform_settings

logger = logging.getLogger("soft-delete-trash")

pytestmark = [pytest.mark.resilience, pytest.mark.asyncio(loop_scope="session")]

TRASH_FLAG = "ENABLE_SOFT_DELETE"
PURGE_TIMEOUT = int(os.getenv("PIPESHUB_IT_TRASH_PURGE_TIMEOUT_SEC", "0"))
RECORDS = "records"

BODY = b"""# Hawk Weathering Policy

Birds are weathered for no more than two hours in direct sun.
A bath is offered at every weathering.
"""


@pytest.fixture(scope="module")
def trash_on(pipeshub_client) -> Iterator[None]:
    before = read_platform_settings(pipeshub_client)
    write_platform_settings(pipeshub_client, before.with_flag(TRASH_FLAG, True))
    try:
        yield
    finally:
        write_platform_settings(pipeshub_client, before)


@pytest.fixture(scope="module")
def kb_client(pipeshub_client) -> KBClient:
    return KBClient(pipeshub_client)


@pytest_asyncio.fixture(loop_scope="session")
async def uploaded(
    trash_on, kb_client: KBClient, vector_store, mongo_store, test_org_id: str
) -> AsyncGenerator[dict[str, Any], None]:
    """A collection upload that has finished indexing, with what each store holds of it."""
    kb_id = kb_client.create_kb(f"trash-{uuid.uuid4().hex[:8]}")["id"]
    try:
        body = BODY + f"\nReference {uuid.uuid4().hex}\n".encode()
        upload = kb_client.upload_file(kb_id, f"weathering-{uuid.uuid4().hex[:6]}.md", body, mimetype="text/markdown")
        assert upload["summary"]["failed"] == 0, f"Upload failed: {upload}"
        record_id = upload["records"][0]["recordId"]
        virtual_id = await wait_for_virtual_id(kb_client, record_id)
        await wait_for_embeddings(vector_store, virtual_id, record_id)
        await wait_until_enriched(kb_client, record_id, timeout=INDEXING_TIMEOUT)
        prefix = await mongo_store.envelope_path(test_org_id, virtual_id, within=records_folder(test_org_id, kb_id))
        yield {
            "kb_id": kb_id,
            "record_id": record_id,
            "virtual_record_id": virtual_id,
            "storage_prefix": prefix,
            "storage_vendor": await mongo_store.storage_vendor_under_path(prefix) or "local",
            "storage_documents": await mongo_store.count_documents_under_path(prefix),
        }
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


def _listed(kb_client: KBClient, kb_id: str) -> set[str]:
    return {item.get("id") for item in kb_client.list_records(kb_id).get("items") or []}


async def _move_to_the_trash(kb_client: KBClient, vector_store, record: dict[str, Any]) -> dict[str, Any]:
    response = kb_client.delete_record(record["record_id"])
    assert response.get("softDeleted") is True, f"The delete did not go to the trash: {response}"
    await vector_store.assert_embeddings_gone(record["virtual_record_id"], timeout=120)
    return response


async def test_a_deleted_record_is_hidden_and_keeps_what_a_restore_needs(
    uploaded, kb_client, vector_store, blob_store, mongo_store, graph_provider
) -> None:
    record_id = uploaded["record_id"]
    assert record_id in _listed(kb_client, uploaded["kb_id"])

    await _move_to_the_trash(kb_client, vector_store, uploaded)

    assert kb_client.get(f"/record/{record_id}").status_code == 404, "opening it answers 'not found'"
    assert record_id not in _listed(kb_client, uploaded["kb_id"])
    node = await graph_provider.get_document(record_id, RECORDS)
    assert node is not None and node.get("isDeleted") is True and node.get("deleteSource") == "USER", node
    await blob_store.assert_blobs_survive(uploaded["storage_prefix"], uploaded["storage_vendor"])
    assert await mongo_store.count_documents_under_path(uploaded["storage_prefix"]) == uploaded["storage_documents"]


async def test_a_restored_record_comes_back_under_its_id_and_is_searchable_again(
    uploaded, kb_client, vector_store, graph_provider
) -> None:
    record_id = uploaded["record_id"]
    await _move_to_the_trash(kb_client, vector_store, uploaded)

    restored = kb_client.restore_record(record_id)

    assert [r.get("recordId") for r in restored.get("restoredRecords") or []] == [record_id], restored
    assert kb_client.get(f"/record/{record_id}").status_code == 200
    assert record_id in _listed(kb_client, uploaded["kb_id"])
    node = await graph_provider.get_document(record_id, RECORDS)
    assert node.get("isDeleted") is False and node.get("deletedAtTimestamp") is None, node
    # Its vectors went with the delete; indexing puts them back from the kept file.
    await wait_for_embeddings(vector_store, uploaded["virtual_record_id"], record_id)


@pytest.mark.skipif(PURGE_TIMEOUT <= 0, reason=(
    "the stack's purge runs every hour on records 14 days in the trash; set "
    "PIPESHUB_IT_TRASH_PURGE_TIMEOUT_SEC on a stack started with the SOFT_DELETE_PURGE_* overrides"
))
async def test_the_purge_removes_the_record_from_every_store(
    uploaded, kb_client, vector_store, blob_store, mongo_store, graph_provider
) -> None:
    record_id = uploaded["record_id"]
    await _move_to_the_trash(kb_client, vector_store, uploaded)

    deadline = asyncio.get_event_loop().time() + PURGE_TIMEOUT
    while await graph_provider.get_document(record_id, RECORDS) is not None:
        assert asyncio.get_event_loop().time() < deadline, f"The purge did not remove {record_id} in {PURGE_TIMEOUT}s"
        await asyncio.sleep(5)

    await blob_store.assert_blobs_gone(uploaded["storage_prefix"], uploaded["storage_vendor"], timeout=120)
    await mongo_store.assert_documents_under_path_gone(uploaded["storage_prefix"], timeout=120)
    assert await vector_store.count_for_virtual_record(uploaded["virtual_record_id"]) == 0
    restore = kb_client.post(f"/record/{record_id}/restore")
    assert restore.status_code == 404, "after the purge there is nothing to restore"
