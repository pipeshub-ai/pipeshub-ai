# pyright: ignore-file

"""GCS in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are objects in this run's folder of the shared test bucket; see
``connectors/storage_scenario_adapter.py`` for the layout and the filter used.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from connector_lifecycle import GCS_BUCKET_NAME
from connectors.gcs.conftest import gcs_connector_config
from connectors.gcs.gcs_storage_helper import GCSStorageHelper
from connectors.scenario_matrix import Action, ConnectorScenarioMatrix
from connectors.storage_scenario_adapter import (
    APP_LEVEL_PERMISSIONS,
    StorageScenarioAdapter,
    storage_matrix,
)
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


class GcsAdapter(StorageScenarioAdapter):
    source = "GCS"

    def _write(self, key: str, text: str) -> None:
        self.storage.upload_blob(self.resource, key, text.encode(), "text/plain")

    def _delete(self, key: str) -> None:
        self.storage.delete_blob(self.resource, key)

    def _rename(self, key: str, new_key: str) -> None:
        # rename_blob copies then deletes; the copy keeps the MD5, which the
        # connector uses as the revision to recognise the move.
        self.storage.rename_object(self.resource, key, new_key)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    gcs_storage: GCSStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[GcsAdapter, None]:
    async with storage_matrix(
        GcsAdapter,
        storage=gcs_storage,
        resource=GCS_BUCKET_NAME,
        client=pipeshub_client,
        graph=graph_provider,
        connector_type="GCS",
        connector_config=gcs_connector_config(),
    ) as adapter:
        yield adapter


@pytest.mark.integration
@pytest.mark.gcs
class TestGcsScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "GCS"
    UNSUPPORTED = {Action.CHANGE_PERMISSION.value: APP_LEVEL_PERMISSIONS}
