"""A JSON artifact file through the storage service's real routes keeps its exact bytes.

The Node API parses every JSON request body before a route runs, so the local
storage upload route reads a file as opaque bytes only because the signed upload
headers say ``application/octet-stream``. These check that a JSON file (the
manifest is one) goes up and comes back byte for byte on every store, and that a
client sending the JSON content type to the local route is refused rather than
stored as a re-serialised object.

Same environment as ``test_artifact_store_contract.py``:
``ARTIFACTS_IT_STORAGE_URL``, ``ARTIFACTS_IT_SCOPED_SECRET``,
``ARTIFACTS_IT_STORAGE_TYPE``, ``ARTIFACTS_IT_ORG_ID``. Skipped without the URL.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from yarl import URL

from app.config.constants.service import config_node_constants
from app.modules.artifacts import store as store_module
from app.modules.artifacts.models import MANIFEST_PATH
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.transformers.blob_storage import (
    BlobStorage,
    close_shared_session,
    get_shared_session,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
SCOPED_SECRET = os.environ.get("ARTIFACTS_IT_SCOPED_SECRET", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")
ORG_ID = os.environ.get("ARTIFACTS_IT_ORG_ID", "64d0000000000000000a4f01")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(300),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

logger = logging.getLogger("artifact-json-it")
# Bytes a parse and re-serialise would change: spacing, key order, escapes, a duplicate key.
JSON_BYTES = b'{ "b": 1,\n  "a": "\\u00fc \\/ \xc3\xbc",   "b": [1, 2.0, 1e3], "n": null }\n'


@pytest.fixture
async def blob() -> AsyncIterator[BlobStorage]:
    store_module.ARTIFACT_ROUTES.reset()
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": STORAGE_URL}},
        config_node_constants.STORAGE.value: {"storageType": STORAGE_TYPE},
    }
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=lambda key, *a, **kw: values[key])
    yield BlobStorage(logger=logger, config_service=config)
    await close_shared_session()


async def test_a_json_file_comes_back_byte_for_byte(blob: BlobStorage) -> None:
    store = NodeArtifactStore(blob, logger)
    plan = await store.presign_revision(
        ORG_ID, str(uuid.uuid4()), uuid.uuid4().hex[:16], [(MANIFEST_PATH, "application/json")]
    )
    try:
        await store.put(plan.files[MANIFEST_PATH], JSON_BYTES, "application/json")

        urls = await store.read_urls(ORG_ID, plan.document_id, [MANIFEST_PATH])
        assert await store.get(urls[MANIFEST_PATH]) == JSON_BYTES
    finally:
        await store.delete_revision(ORG_ID, plan.document_id)


@pytest.mark.skipif(
    STORAGE_TYPE != "local",
    reason=f"not applicable on {STORAGE_TYPE}: the file goes to the vendor's signed URL, not through the API",
)
async def test_the_local_route_refuses_a_json_content_type(blob: BlobStorage) -> None:
    store = NodeArtifactStore(blob, logger)
    plan = await store.presign_revision(
        ORG_ID, str(uuid.uuid4()), uuid.uuid4().hex[:16], [(MANIFEST_PATH, "application/json")]
    )
    try:
        entry = plan.files[MANIFEST_PATH]
        headers = {**entry.headers, "Content-Type": "application/json"}
        async with get_shared_session().put(
            URL(entry.url, encoded=True), data=JSON_BYTES, headers=headers, skip_auto_headers={"Content-Type"}
        ) as response:
            assert response.status == 400, await response.text()

        urls = await store.read_urls(ORG_ID, plan.document_id, [MANIFEST_PATH])
        assert await store.get_if_present(urls[MANIFEST_PATH]) is None
    finally:
        await store.delete_revision(ORG_ID, plan.document_id)
