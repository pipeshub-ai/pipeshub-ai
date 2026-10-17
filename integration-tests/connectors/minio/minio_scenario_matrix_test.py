# pyright: ignore-file

"""MinIO in the shared scenario matrix (``connectors/scenario_matrix.py``).

MinIO runs in the integration stack and uses the same connector code as S3
(``S3CompatibleBaseConnector``), so it shares the S3 adapter and known bugs.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from connectors.minio.conftest import minio_bucket, minio_connector_config
from connectors.minio.minio_storage_helper import MinioStorageHelper
from connectors.scenario_matrix import Action, ConnectorScenarioMatrix
from connectors.storage_scenario_adapter import (
    APP_LEVEL_PERMISSIONS,
    S3CompatibleAdapter,
    storage_matrix,
)
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


class MinioAdapter(S3CompatibleAdapter):
    source = "MinIO"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    minio_storage: MinioStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[MinioAdapter, None]:
    async with storage_matrix(
        MinioAdapter,
        storage=minio_storage,
        resource=minio_bucket(),
        client=pipeshub_client,
        graph=graph_provider,
        connector_type="MinIO",
        connector_config=minio_connector_config(),
    ) as adapter:
        yield adapter


@pytest.mark.integration
@pytest.mark.minio
class TestMinioScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "MinIO"
    UNSUPPORTED = {Action.CHANGE_PERMISSION.value: APP_LEVEL_PERMISSIONS}
