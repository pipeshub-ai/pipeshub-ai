# pyright: ignore-file

"""S3 in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are objects in this run's folder of the shared test bucket; see
``connectors/storage_scenario_adapter.py`` for the layout and the filter used.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from connector_lifecycle import RESOURCE_NAME
from connectors.s3.conftest import s3_connector_config
from connectors.s3.s3_storage_helper import S3StorageHelper
from connectors.scenario_matrix import Action, ConnectorScenarioMatrix
from connectors.storage_scenario_adapter import (
    APP_LEVEL_PERMISSIONS,
    OBJECT_DELETE_NEVER_SYNCED,
    S3CompatibleAdapter,
    storage_matrix,
)
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    s3_storage: S3StorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[S3CompatibleAdapter, None]:
    async with storage_matrix(
        S3CompatibleAdapter,
        storage=s3_storage,
        resource=RESOURCE_NAME,
        client=pipeshub_client,
        graph=graph_provider,
        connector_type="S3",
        connector_config=s3_connector_config(),
    ) as adapter:
        yield adapter


@pytest.mark.integration
@pytest.mark.s3
class TestS3ScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "S3"
    UNSUPPORTED = {Action.CHANGE_PERMISSION.value: APP_LEVEL_PERMISSIONS}
    KNOWN_BUGS = {
        "incr_delete": OBJECT_DELETE_NEVER_SYNCED,
    }
