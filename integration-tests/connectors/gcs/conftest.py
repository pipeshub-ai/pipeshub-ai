# pyright: ignore-file

"""GCS connector fixtures."""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import RESOURCE_NAME, constructor, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.gcs.gcs_storage_helper import GCSStorageHelper


@pytest.fixture(scope="session")
def gcs_storage():
    sa_json = os.getenv("GCS_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        pytest.skip("GCS_SERVICE_ACCOUNT_JSON not set.")
    return GCSStorageHelper(service_account_json=sa_json)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def gcs_connector(
    gcs_storage: GCSStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root,
) -> AsyncGenerator[Dict[str, Any], None]:
    sa_json = os.getenv("GCS_SERVICE_ACCOUNT_JSON")
    assert sa_json
    config = {
        "auth": {
            "serviceAccountJson": sa_json,
            "bucket": RESOURCE_NAME,
        }
    }

    state = await constructor(
        gcs_storage,
        pipeshub_client,
        graph_provider,
        sample_data_root,
        storage_name="GCS bucket",
        connector_type="GCS",
        connector_config=config,
        create_fn="create_bucket",
    )
    state["bucket_name"] = state["resource_name"]
    yield state
    await destructor(gcs_storage, pipeshub_client, graph_provider, state, connector_type="GCS")
