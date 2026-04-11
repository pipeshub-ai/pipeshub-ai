# pyright: ignore-file

"""GCS connector fixtures."""

import os
from typing import Any, Dict, Generator

import pytest
from neo4j import Driver

from connector_lifecycle import RESOURCE_NAME, constructor, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.gcs.gcs_storage_helper import GCSStorageHelper


@pytest.fixture(scope="session")
def gcs_storage():
    sa_json = os.getenv("GCS_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        pytest.skip("GCS_SERVICE_ACCOUNT_JSON not set.")
    return GCSStorageHelper(service_account_json=sa_json)


@pytest.fixture(scope="module")
def gcs_connector(
    gcs_storage: GCSStorageHelper,
    pipeshub_client: PipeshubClient,
    neo4j_driver: Driver,
    sample_data_root,
) -> Generator[Dict[str, Any], None, None]:
    sa_json = os.getenv("GCS_SERVICE_ACCOUNT_JSON")
    assert sa_json
    config = {
        "auth": {
            "serviceAccountJson": sa_json,
            "bucket": RESOURCE_NAME,
        }
    }

    state = constructor(
        gcs_storage,
        pipeshub_client,
        neo4j_driver,
        sample_data_root,
        storage_name="GCS bucket",
        connector_type="GCS",
        connector_config=config,
        create_fn="create_bucket",
    )
    state["bucket_name"] = state["resource_name"]
    yield state
    destructor(gcs_storage, pipeshub_client, neo4j_driver, state, connector_type="GCS")
