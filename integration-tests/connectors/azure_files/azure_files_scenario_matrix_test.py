# pyright: ignore-file

"""Azure Files in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are files in this run's folder of the shared test share; see
``connectors/storage_scenario_adapter.py`` for the layout and the filter used.
Every Azure Files sync walks the share and removes records of files it did not
see (``_remove_records_not_seen``), so deletes and filter exclusions both land.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from connector_lifecycle import RESOURCE_NAME
from connectors.azure_files.azure_files_storage_helper import AzureFilesStorageHelper
from connectors.azure_files.conftest import azure_files_connector_config
from connectors.scenario_matrix import Action, ConnectorScenarioMatrix
from connectors.storage_scenario_adapter import (
    APP_LEVEL_PERMISSIONS,
    StorageScenarioAdapter,
    storage_matrix,
)
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


class AzureFilesAdapter(StorageScenarioAdapter):
    source = "Azure Files"

    def _write(self, key: str, text: str) -> None:
        # upload_file creates the directories; it also overwrites an existing file.
        self.storage.upload_file(self.resource, key, text.encode())

    def _delete(self, key: str) -> None:
        self.storage.delete_file(self.resource, key)

    def _rename(self, key: str, new_key: str) -> None:
        # A real rename: the SMB FileId survives it, which is the revision the
        # connector matches to keep the same record.
        self.storage.rename_object(self.resource, key, new_key)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    azure_files_storage: AzureFilesStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[AzureFilesAdapter, None]:
    async with storage_matrix(
        AzureFilesAdapter,
        storage=azure_files_storage,
        resource=RESOURCE_NAME,
        client=pipeshub_client,
        graph=graph_provider,
        connector_type="Azure Files",
        connector_config=azure_files_connector_config(),
    ) as adapter:
        yield adapter


@pytest.mark.integration
@pytest.mark.azure_files
class TestAzureFilesScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Azure Files"
    UNSUPPORTED = {Action.CHANGE_PERMISSION.value: APP_LEVEL_PERMISSIONS}
