"""Fixtures for the cross-store cleanup scenarios.

Every scenario needs the same two things: a record that genuinely exists in all
four stores, and a note of what those stores held before it was deleted. The
note has to be taken first — once the record is gone the graph no longer knows
its virtual record id, and blob storage and MongoDB are both keyed on it.

The virtual record id is read from the record itself rather than from whatever
the vector database happens to return first. With more than one record indexed
those are not the same answer, and the wrong one silently points every
assertion at another record's data.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper.clients.kb_client import KBClient

logger = logging.getLogger("cleanup-fixtures")

INDEXING_TIMEOUT = 300
POLL = 5

POLICY = b"""# Falconry Reimbursement Policy

Mews cleaning is reimbursed at thirty pounds a visit.
Telemetry transmitters are capital items and require sign-off.
Jesses and swivels are consumable and need no approval.
"""

# Deliberately different content, so the two records are unmistakably distinct
# in every store and a survivor assertion cannot be satisfied by the other
# record's data.
HUSBANDRY = b"""# Ferret Husbandry Notes

Bedding is replaced weekly and charged to the field budget.
Vaccination records are retained for three years.
"""


@pytest.fixture(scope="module")
def kb_client(pipeshub_client) -> KBClient:
    return KBClient(pipeshub_client)


async def _indexed_record(
    kb_client: KBClient,
    vector_store,
    test_org_id: str,
    body: bytes,
    label: str,
) -> AsyncGenerator[dict[str, Any], None]:
    kb = kb_client.create_kb(f"cleanup-{label}-{uuid.uuid4().hex[:8]}")
    kb_id = kb["id"]
    name = f"{label}-{uuid.uuid4().hex[:6]}.md"

    try:
        upload = kb_client.upload_file(kb_id, name, body, mimetype="text/markdown")
        assert upload["summary"]["failed"] == 0, f"Upload failed: {upload}"
        record_id = upload["records"][0]["recordId"]

        virtual_record_id = await _wait_for_virtual_id(kb_client, record_id)
        await _wait_for_embeddings(vector_store, virtual_record_id, record_id)

        yield {
            "kb_id": kb_id,
            "record_id": record_id,
            "record_name": name,
            "virtual_record_id": virtual_record_id,
            "storage_prefix": f"{test_org_id}/PipesHub/records/{virtual_record_id}",
        }
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            # A 404 means the test deleted it, which several of them do on
            # purpose. Anything else is worth seeing.
            already_gone = "404" in str(exc)
            logger.log(
                logging.DEBUG if already_gone else logging.WARNING,
                "Could not delete knowledge base %s: %s",
                kb_id,
                exc,
            )


@pytest_asyncio.fixture(loop_scope="session")
async def indexed_record(
    kb_client: KBClient, vector_store, test_org_id: str
) -> AsyncGenerator[dict[str, Any], None]:
    """A knowledge-base record that has finished indexing."""
    async for record in _indexed_record(
        kb_client, vector_store, test_org_id, POLICY, "policy"
    ):
        yield record


@pytest_asyncio.fixture(loop_scope="session")
async def second_indexed_record(
    kb_client: KBClient, vector_store, test_org_id: str
) -> AsyncGenerator[dict[str, Any], None]:
    """An independent second record, for checking a delete did not overreach."""
    async for record in _indexed_record(
        kb_client, vector_store, test_org_id, HUSBANDRY, "survivor"
    ):
        yield record


async def _wait_for_virtual_id(kb_client: KBClient, record_id: str) -> str:
    """The record's own virtual id, which is what the other stores key on."""
    deadline = asyncio.get_event_loop().time() + INDEXING_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        payload = kb_client.get_record(record_id)
        virtual_id = (payload.get("record") or {}).get("virtualRecordId")
        if virtual_id:
            return str(virtual_id)
        await asyncio.sleep(POLL)
    raise AssertionError(
        f"Record {record_id} never got a virtualRecordId within "
        f"{INDEXING_TIMEOUT}s, so there is nothing to key the other three "
        "stores on."
    )


async def _wait_for_embeddings(vector_store, virtual_id: str, record_id: str) -> None:
    """Block until the record is really in the vector database.

    Fails rather than proceeding on an empty result. Every assertion downstream
    would otherwise be checking that nothing is nothing, which passes and means
    nothing — the exact failure these tests exist to rule out.
    """
    deadline = asyncio.get_event_loop().time() + INDEXING_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        if await vector_store.count_for_virtual_record(virtual_id):
            logger.info("Record %s indexed as virtual record %s", record_id, virtual_id)
            return
        await asyncio.sleep(POLL)

    raise AssertionError(
        f"Record {record_id} (virtual {virtual_id}) produced no embeddings "
        f"within {INDEXING_TIMEOUT}s. Check that an embedding model is "
        "configured for the org — with none, indexing fails and the record is "
        "dead-lettered rather than falling back to the local embedder."
    )
