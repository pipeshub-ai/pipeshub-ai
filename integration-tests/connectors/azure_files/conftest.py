# pyright: ignore-file

"""Azure Files connector fixtures."""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import constructor, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.azure_files.azure_files_storage_helper import AzureFilesStorageHelper


@pytest.fixture(scope="session")
def azure_files_storage():
    conn_str = os.getenv("AZURE_FILES_CONNECTION_STRING")
    if not conn_str:
        pytest.skip("AZURE_FILES_CONNECTION_STRING not set.")
    return AzureFilesStorageHelper(connection_string=conn_str)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def azure_files_connector(
    azure_files_storage: AzureFilesStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root,
) -> AsyncGenerator[Dict[str, Any], None]:
    conn_str = os.getenv("AZURE_FILES_CONNECTION_STRING")
    config = {"auth": {"connectionString": conn_str}}

    state = await constructor(
        azure_files_storage,
        pipeshub_client,
        graph_provider,
        sample_data_root,
        storage_name="Azure Files share",
        connector_type="Azure Files",
        connector_config=config,
    )
    resource = state["resource_name"]
    state["share_name"] = resource
    state["container_name"] = resource
    yield state
    await destructor(
        azure_files_storage,
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Azure Files",
    )
