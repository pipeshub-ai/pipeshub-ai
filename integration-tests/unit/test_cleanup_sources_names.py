"""upload_to_kb tracks a record under the name the graph stores, not the file name.

A knowledge base keeps an uploaded file's name without its extension, so a
lookup by "notes.md" finds nothing and a wait on it can only time out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from helper import cleanup_sources as src

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_an_upload_is_tracked_under_its_stored_name() -> None:
    kb_client = MagicMock()
    kb_client.upload_file.return_value = {"summary": {"failed": 0}, "records": [{"recordId": "r1"}]}
    kb_client.get_record.return_value = {
        "record": {"virtualRecordId": "v1", "externalRecordId": "doc-1", "indexingStatus": "COMPLETED"}
    }
    vector_store = AsyncMock()
    vector_store.count_for_virtual_record.return_value = 2

    tracked = await src.upload_to_kb(kb_client, vector_store, "kb-1", "root-abc123.md", b"body")

    assert tracked.name == "root-abc123"
    assert (tracked.record_id, tracked.virtual_record_id, tracked.upload_document_id) == ("r1", "v1", "doc-1")
    kb_client.upload_file.assert_called_once_with(
        "kb-1", "root-abc123.md", b"body", folder_id=None, mimetype="text/markdown"
    )
