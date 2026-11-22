"""Settings of the paid-result integration tests: the storage service they read from the environment.

Like the artifact tests, these read ``ARTIFACTS_IT_STORAGE_URL`` and so run in the storage job of
``.github/workflows/backend-matrix.yml``, against the Node API's real storage routes
(``backend/nodejs/apps/tests/support/storage-it-server.ts``) on each file store. Environment:

- ``ARTIFACTS_IT_STORAGE_URL``: the storage service origin, e.g. http://127.0.0.1:3000
- ``ARTIFACTS_IT_SCOPED_SECRET``: the scoped JWT secret it verifies storage tokens with
- ``ARTIFACTS_IT_STORAGE_TYPE``: the storage it is configured with: local, s3 or azureBlob

Skipped when ``ARTIFACTS_IT_STORAGE_URL`` is not set; the CI job fails on any skip.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.service import config_node_constants

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
SCOPED_SECRET = os.environ.get("ARTIFACTS_IT_SCOPED_SECRET", "")
STORAGE_TYPE = os.environ.get("ARTIFACTS_IT_STORAGE_TYPE", "local")

def config_service() -> MagicMock:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": SCOPED_SECRET},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": STORAGE_URL}},
        config_node_constants.STORAGE.value: {"storageType": STORAGE_TYPE},
    }
    service = MagicMock()
    service.get_config = AsyncMock(side_effect=lambda key, *a, **kw: values[key])
    return service


def new_org() -> str:
    """A 24-hex organisation id nobody used before, so a rerun against the same storage starts empty."""
    return uuid.uuid4().hex[:24]
