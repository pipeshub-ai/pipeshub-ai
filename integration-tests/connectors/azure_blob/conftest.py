# pyright: ignore-file

"""Azure Blob connector fixtures."""

import os
from typing import Any, Dict, Generator

import pytest
from neo4j import Driver

from connector_lifecycle import constructor, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.azure_blob.azure_blob_storage_helper import AzureBlobStorageHelper


@pytest.fixture(scope="session")
def azure_blob_storage():
    conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    if not conn_str:
        pytest.skip("AZURE_BLOB_CONNECTION_STRING not set.")
    return AzureBlobStorageHelper(connection_string=conn_str)


@pytest.fixture(scope="module")
def azure_blob_connector(
    azure_blob_storage: AzureBlobStorageHelper,
    pipeshub_client: PipeshubClient,
    neo4j_driver: Driver,
    sample_data_root,
) -> Generator[Dict[str, Any], None, None]:
    conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    config = {"auth": {"azureBlobConnectionString": conn_str}}

    state = constructor(
        azure_blob_storage,
        pipeshub_client,
        neo4j_driver,
        sample_data_root,
        storage_name="Azure Blob container",
        connector_type="Azure Blob",
        connector_config=config,
        create_fn="create_container",
    )
    state["container_name"] = state["resource_name"]
    yield state
    destructor(
        azure_blob_storage,
        pipeshub_client,
        neo4j_driver,
        state,
        connector_type="Azure Blob",
    )
