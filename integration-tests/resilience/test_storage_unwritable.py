"""An upload made while blob storage cannot be written fails cleanly, and says so.

Local blob storage lives in one folder in the app container. This test moves
that folder aside and leaves a plain file in its place, so every write fails
(a real full disk needs a mount the container may not make; to the product
both are a write that cannot happen). Then it uploads to a fresh knowledge base:

  * the upload is reported as failed, not as saved;
  * the knowledge base lists nothing for it, and MongoDB holds no storage
    document for it;
  * once storage is writable again, the same upload succeeds and indexes.

The scenario runs once, in a module fixture; each test below checks one
thing it saw, so a failure names the part that broke.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
import requests

from helper.clients.kb_client import KBClient
from helper.compose_control import ComposeStack
from helper.fault_switches import LOCAL_STORAGE_MOUNT, LOCAL_STORAGE_ROOT, storage_off_script, storage_on_script
from helper.indexing_progress import document, wait_until_finished
from helper.kb_upload_sse import iter_sse_envelopes
from helper.plain_message import plain_language_problems

logger = logging.getLogger("resilience")

pytestmark = [pytest.mark.resilience, pytest.mark.asyncio(loop_scope="session")]

def _raw_upload(kb_client: KBClient, kb_id: str, name: str, content: bytes) -> dict[str, Any]:
    """Upload without raising on failure: the saved records, and every message the product sent back."""
    client = kb_client._client
    client._ensure_access_token()
    with requests.post(
        f"{client.base_url}{KBClient.BASE}/{kb_id}/upload",
        headers={"Authorization": f"Bearer {client._access_token}"},
        files=[("files", (name, io.BytesIO(content), "text/markdown"))],
        stream=True,
        timeout=client.timeout_seconds,
    ) as resp:
        if "text/event-stream" not in (resp.headers.get("Content-Type") or "").lower():
            try:
                body = resp.json()
            except ValueError:
                body = {"message": resp.text[:500]}
            message = body.get("message") or body.get("error") or body.get("detail") or json.dumps(body)
            return {"records": [], "messages": [str(message)], "status": resp.status_code}
        records: list[dict[str, Any]] = []
        messages: list[str] = []
        for envelope in iter_sse_envelopes(resp):
            payload = json.loads(envelope["data"]) if envelope["data"] else {}
            if envelope["event"] == "file:succeeded":
                records.append(payload)
            elif envelope["event"] == "file:failed":
                messages += [str(e) for e in payload.get("errors") or []]
                if payload.get("reason"):
                    messages.append(str(payload["reason"]))
            elif envelope["event"] == "error":
                messages.append(str(payload.get("message") if isinstance(payload, dict) else payload))
        return {"records": records, "messages": messages, "status": resp.status_code}


@dataclass
class StorageOutage:
    """What one upload into unwritable storage did, and what it left behind."""

    name: str = ""
    outcome: dict[str, Any] = field(default_factory=dict)
    listed_after_failure: list[str] = field(default_factory=list)
    leftover_storage_documents: int = 0
    retry: dict[str, Any] = field(default_factory=dict)
    retry_final: dict[str, str] = field(default_factory=dict)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def storage_outage(
    compose: ComposeStack,
    kb_client: KBClient,
    mongo_store,
    test_org_id: str,
) -> StorageOutage:
    """Upload once into unwritable storage, then once more after it is back; every test here reads the result."""
    probe = compose.exec(
        "pipeshub-ai", ["sh", "-c", f"[ -d {LOCAL_STORAGE_ROOT}/{LOCAL_STORAGE_MOUNT} ] && echo local || echo other"]
    ).stdout.strip()
    if probe != "local":
        pytest.skip(f"blob storage is not local storage under {LOCAL_STORAGE_ROOT}/{LOCAL_STORAGE_MOUNT}")

    kb_id = kb_client.create_kb(f"resilience-storage-{uuid.uuid4().hex[:8]}")["id"]
    token = uuid.uuid4().hex[:12]
    result = StorageOutage(name=f"runbook-{token}.md")
    try:
        documents_before = await mongo_store.count_documents_for_org(test_org_id)
        compose.exec("pipeshub-ai", ["sh", "-c", storage_off_script()])
        try:
            result.outcome = _raw_upload(kb_client, kb_id, result.name, document(token))
        finally:
            compose.exec("pipeshub-ai", ["sh", "-c", storage_on_script()])
        result.leftover_storage_documents = await mongo_store.count_documents_for_org(test_org_id) - documents_before
        result.listed_after_failure = [item["name"] for item in kb_client.list_records(kb_id).get("items") or []]

        result.retry = kb_client.upload_file(kb_id, result.name, document(token), mimetype="text/markdown")
        if not result.retry["summary"]["failed"]:
            record_id = result.retry["records"][0]["recordId"]
            result.retry_final = await wait_until_finished(kb_client, [record_id])
        return result
    finally:
        compose.exec("pipeshub-ai", ["sh", "-c", storage_on_script()])
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask the result
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


async def test_upload_into_unwritable_storage_is_reported_as_failed(storage_outage: StorageOutage) -> None:
    outcome = storage_outage.outcome
    assert not outcome["records"], (
        f"with blob storage unwritable, the upload of {storage_outage.name} was reported as saved: {outcome}"
    )
    assert outcome["messages"], f"the failed upload of {storage_outage.name} came back with no message: {outcome}"


async def test_failed_upload_is_not_listed(storage_outage: StorageOutage) -> None:
    assert not storage_outage.listed_after_failure, (
        f"the knowledge base lists {storage_outage.listed_after_failure} after an upload that failed to save"
    )


async def test_upload_succeeds_once_storage_is_writable_again(storage_outage: StorageOutage) -> None:
    assert storage_outage.retry["summary"]["failed"] == 0, (
        f"uploading {storage_outage.name} again once storage was writable failed: {storage_outage.retry}"
    )
    assert set(storage_outage.retry_final.values()) == {"COMPLETED"}, (
        f"the re-uploaded {storage_outage.name} did not finish indexing: {storage_outage.retry_final}"
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "A failed upload leaves its storage document behind in MongoDB: the storage service saves "
        "the document before writing the file and, when the write fails, only releases its upload "
        "lease (storage.upload.service.ts handleDocumentUpload), so a file-less document remains."
    ),
)
async def test_failed_upload_leaves_no_storage_document(storage_outage: StorageOutage) -> None:
    assert storage_outage.leftover_storage_documents == 0, (
        f"a failed upload left {storage_outage.leftover_storage_documents} storage document(s) behind in MongoDB"
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "An upload that fails because storage cannot be written reports the storage layer's own "
        "error text (the upload pipeline forwards the thrown error's message), not a plain "
        "explanation with a next step."
    ),
)
async def test_storage_failure_message_reads_plainly(storage_outage: StorageOutage) -> None:
    messages = storage_outage.outcome["messages"]
    assert messages, "the failed upload returned no message to check"
    unclear = {
        message: why
        for message in set(messages)
        if (why := plain_language_problems(message, about=("upload", "save", "saved", "storage", "file")))
    }
    assert not unclear, f"messages shown when an upload could not be saved do not read plainly: {unclear}"
