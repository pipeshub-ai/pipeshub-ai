"""A direct caller of the connector service cannot point its KB record at another user's storage document.

Runs unchanged on the tree before the fix and after it: the route gets the HTTP body a caller to port 8088
would send (a foreign ``externalRecordId``, no gateway grant), through the real service. The secure outcome is
a 403 and no record written; before the fix the record was written pointing at the foreign document.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.connectors.sources.localKB.api.kb_router import (
    upload_records_to_folder,
    upload_records_to_kb,
)
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService

ORG, USER, KB, FOLDER, VICTIM = "org-1", "attacker", "kb-1", "folder-1", "victim-doc-0001"


def _service() -> tuple:
    gp = MagicMock()
    gp._validate_upload_context = AsyncMock(return_value={"valid": True, "user": {"email": "a@b.c", "fullName": "A"}})
    gp._analyze_upload_structure = MagicMock(
        side_effect=lambda files, validation: {
            "sorted_folder_paths": [], "parent_folder_id": None, "file_destinations": {i: {"type": "root"} for i in range(len(files))},
        }
    )
    gp._populate_file_destinations = MagicMock()
    gp._fetch_existing_file_names_in_parent = AsyncMock(return_value=set())
    gp._normalize_name = MagicMock(side_effect=lambda n: n)
    gp._normalized_name_variants_lower = MagicMock(side_effect=lambda n: [n.lower()])
    gp._generate_upload_message = MagicMock(return_value="ok")
    processor = MagicMock()
    processor.on_new_records = AsyncMock()
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={"scopedJwtSecret": "s"})
    svc = KnowledgeBaseService(MagicMock(), gp, AsyncMock(), processor_for_kb=AsyncMock(return_value=processor), config_service=config)
    svc._refuse_if_deleting = AsyncMock(return_value=None)
    svc._exceeds_folder_depth = AsyncMock(return_value=False)
    svc._get_storage_url = AsyncMock(return_value="http://storage.test")
    return svc, processor


def _body() -> dict:
    return {"files": [{
        "record": {
            "_key": "rec-1", "orgId": ORG, "recordName": "stolen.txt", "externalRecordId": VICTIM, "recordType": "FILE",
            "origin": "UPLOAD", "connectorId": KB, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            "sourceCreatedAtTimestamp": 1, "sourceLastModifiedTimestamp": 1, "version": 1,
        },
        "fileRecord": {"_key": "rec-1", "orgId": ORG, "name": "stolen.txt", "isFile": True},
        "filePath": "stolen.txt", "lastModified": 1,
    }]}


def _request() -> MagicMock:
    request = MagicMock()
    data = {"userId": USER, "orgId": ORG}
    request.state.user = MagicMock()
    request.state.user.get = MagicMock(side_effect=lambda k: data.get(k))
    request.json = AsyncMock(return_value=_body())
    return request


def _written(processor) -> list[str]:
    return [
        f"{fr.external_record_id} -> {fr.fetch_signed_url}"
        for call in processor.on_new_records.await_args_list
        for fr, _ in call.args[0]
    ]


@pytest.mark.asyncio
async def test_a_foreign_storage_document_id_in_the_body_is_refused_and_no_record_is_written() -> None:
    svc, processor = _service()
    try:
        await upload_records_to_kb(kb_id=KB, request=_request(), kb_service=svc, kafka_service=AsyncMock())
        refused = None
    except HTTPException as e:
        refused = e.status_code
    assert _written(processor) == [], f"record written pointing at a foreign document: {_written(processor)}"
    assert refused == 403


@pytest.mark.asyncio
async def test_the_same_request_to_the_folder_route_is_refused_and_writes_nothing() -> None:
    svc, processor = _service()
    try:
        await upload_records_to_folder(kb_id=KB, folder_id=FOLDER, request=_request(), kb_service=svc, kafka_service=AsyncMock())
        refused = None
    except HTTPException as e:
        refused = e.status_code
    assert _written(processor) == [], f"record written pointing at a foreign document: {_written(processor)}"
    assert refused == 403
