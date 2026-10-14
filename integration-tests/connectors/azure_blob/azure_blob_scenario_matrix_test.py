# pyright: ignore-file

"""Azure Blob in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are blobs in this run's folder of the shared test container; see
``connectors/storage_scenario_adapter.py`` for the layout and the filter used.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from connector_lifecycle import RESOURCE_NAME
from connectors.azure_blob.azure_blob_storage_helper import AzureBlobStorageHelper
from connectors.azure_blob.conftest import azure_blob_connector_config
from connectors.scenario_matrix import Action, ConnectorScenarioMatrix
from connectors.storage_scenario_adapter import (
    APP_LEVEL_PERMISSIONS,
    StorageScenarioAdapter,
    storage_matrix,
)
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


class AzureBlobAdapter(StorageScenarioAdapter):
    source = "Azure Blob"

    def _write(self, key: str, text: str) -> None:
        self.storage.upload_blob(self.resource, key, text.encode(), "text/plain")

    def _delete(self, key: str) -> None:
        self.storage.delete_blob(self.resource, key)

    def _rename(self, key: str, new_key: str) -> None:
        # Copy then delete. The service stores the upload's Content-MD5, which the
        # connector uses as the revision to recognise the move.
        self.storage.rename_object(self.resource, key, new_key)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    azure_blob_storage: AzureBlobStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[AzureBlobAdapter, None]:
    async with storage_matrix(
        AzureBlobAdapter,
        storage=azure_blob_storage,
        resource=RESOURCE_NAME,
        client=pipeshub_client,
        graph=graph_provider,
        connector_type="Azure Blob",
        connector_config=azure_blob_connector_config(),
    ) as adapter:
        yield adapter


@pytest.mark.integration
@pytest.mark.azure_blob
class TestAzureBlobScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Azure Blob"
    UNSUPPORTED = {Action.CHANGE_PERMISSION.value: APP_LEVEL_PERMISSIONS}
