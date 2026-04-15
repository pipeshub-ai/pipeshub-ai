# pyright: ignore-file

"""S3 connector fixtures."""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import RESOURCE_NAME, constructor, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol

from connectors.s3.s3_storage_helper import S3StorageHelper


@pytest.fixture(scope="session")
def s3_storage():
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    if not access_key or not secret_key:
        pytest.skip("S3 credentials not set.")
    return S3StorageHelper(access_key=access_key, secret_key=secret_key)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def s3_connector(
    s3_storage: S3StorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root,
) -> AsyncGenerator[Dict[str, Any], None]:
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    config = {
        "auth": {
            "accessKey": access_key,
            "secretKey": secret_key,
            "bucket": RESOURCE_NAME,
        }
    }
    region = os.getenv("S3_REGION")
    if region:
        config["auth"]["region"] = region

    state = await constructor(
        s3_storage,
        pipeshub_client,
        graph_provider,
        sample_data_root,
        storage_name="S3 bucket",
        connector_type="S3",
        connector_config=config,
        create_fn="create_bucket",
    )
    state["bucket_name"] = state["resource_name"]
    yield state
    await destructor(s3_storage, pipeshub_client, graph_provider, state, connector_type="S3")
