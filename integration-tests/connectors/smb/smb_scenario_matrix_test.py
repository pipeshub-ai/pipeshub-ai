# pyright: ignore-file

"""SMB in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are files in this run's folder of the test share (the ``smb-source``
container in CI); see ``connectors/storage_scenario_adapter.py`` for the layout
and the filter used. Every SMB sync walks the share and prunes records of files
it did not see (``network_share/operations.py`` ``prune_unseen``), so deletes and
filter exclusions both land.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from connectors.scenario_matrix import Action, ConnectorScenarioMatrix
from connectors.smb.conftest import _require_smb_creds, smb_connector_config
from connectors.smb.smb_storage_helper import SmbStorageHelper
from connectors.storage_scenario_adapter import (
    APP_LEVEL_PERMISSIONS,
    StorageScenarioAdapter,
    storage_matrix,
)
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


class SmbAdapter(StorageScenarioAdapter):
    source = "SMB"

    def _write(self, key: str, text: str) -> None:
        self.storage.write_file(self.resource, key, text.encode())

    def _delete(self, key: str) -> None:
        self.storage.delete_file(self.resource, key)

    def _rename(self, key: str, new_key: str) -> None:
        # A real rename: the FileId survives it, which is the revision the
        # connector matches to keep the same record.
        self.storage.rename_object(self.resource, key, new_key)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    smb_storage: SmbStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[SmbAdapter, None]:
    async with storage_matrix(
        SmbAdapter,
        storage=smb_storage,
        resource=_require_smb_creds()["share"],
        client=pipeshub_client,
        graph=graph_provider,
        connector_type="SMB",
        connector_config=smb_connector_config(),
    ) as adapter:
        yield adapter


@pytest.mark.integration
@pytest.mark.smb
class TestSmbScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "SMB"
    UNSUPPORTED = {
        Action.CHANGE_PERMISSION.value: (
            f"{APP_LEVEL_PERMISSIONS} (network_share/permissions.py app_level_permissions); "
            "NTFS ACLs on the share are not read"
        ),
    }
