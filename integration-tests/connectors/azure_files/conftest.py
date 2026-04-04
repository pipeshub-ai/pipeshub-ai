# pyright: ignore-file

"""Azure Files connector fixtures."""

import os
from typing import Any, Dict, Generator

import pytest
from neo4j import Driver

from connector_lifecycle import constructor, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.azure_files.azure_files_storage_helper import AzureFilesStorageHelper


@pytest.fixture(scope="session")
def azure_files_storage():
    conn_str = os.getenv("AZURE_FILES_CONNECTION_STRING")
    if not conn_str:
        pytest.skip("AZURE_FILES_CONNECTION_STRING not set.")
    return AzureFilesStorageHelper(connection_string=conn_str)


@pytest.fixture(scope="module")
def azure_files_connector(
    azure_files_storage: AzureFilesStorageHelper,
    pipeshub_client: PipeshubClient,
    neo4j_driver: Driver,
    sample_data_root,
) -> Generator[Dict[str, Any], None, None]:
    conn_str = os.getenv("AZURE_FILES_CONNECTION_STRING")
    config = {"auth": {"connectionString": conn_str}}

    state = constructor(
        azure_files_storage,
        pipeshub_client,
        neo4j_driver,
        sample_data_root,
        storage_name="Azure Files share",
        connector_type="Azure Files",
        connector_config=config,
        create_fn="create_share",
    )
    state["share_name"] = state["resource_name"]
    yield state
    destructor(
        azure_files_storage,
        pipeshub_client,
        neo4j_driver,
        state,
        connector_type="Azure Files",
    )
