# pyright: ignore-file

"""Azure Blob connector fixtures."""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import constructor, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.azure_blob.azure_blob_storage_helper import AzureBlobStorageHelper


@pytest.fixture(scope="session")
def azure_blob_storage():
    conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    if not conn_str:
        pytest.skip("AZURE_BLOB_CONNECTION_STRING not set.")
    return AzureBlobStorageHelper(connection_string=conn_str)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def azure_blob_connector(
    azure_blob_storage: AzureBlobStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root,
) -> AsyncGenerator[Dict[str, Any], None]:
    conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    config = {"auth": {"azureBlobConnectionString": conn_str}}

    state = await constructor(
        azure_blob_storage,
        pipeshub_client,
        graph_provider,
        sample_data_root,
        storage_name="Azure Blob container",
        connector_type="Azure Blob",
        connector_config=config,
        create_fn="create_container",
    )
    state["container_name"] = state["resource_name"]
    yield state
    await destructor(
        azure_blob_storage,
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Azure Blob",
    )
