# pyright: ignore-file

"""S3 connector fixtures."""

import os
from typing import Any, Dict, Generator

import pytest
from neo4j import Driver

from connector_lifecycle import RESOURCE_NAME, constructor, destructor
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.s3.s3_storage_helper import S3StorageHelper


@pytest.fixture(scope="session")
def s3_storage():
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    if not access_key or not secret_key:
        pytest.skip("S3 credentials not set.")
    return S3StorageHelper(access_key=access_key, secret_key=secret_key)


@pytest.fixture(scope="module")
def s3_connector(
    s3_storage: S3StorageHelper,
    pipeshub_client: PipeshubClient,
    neo4j_driver: Driver,
    sample_data_root,
) -> Generator[Dict[str, Any], None, None]:
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

    state = constructor(
        s3_storage,
        pipeshub_client,
        neo4j_driver,
        sample_data_root,
        storage_name="S3 bucket",
        connector_type="S3",
        connector_config=config,
        create_fn="create_bucket",
    )
    state["bucket_name"] = state["resource_name"]
    yield state
    destructor(s3_storage, pipeshub_client, neo4j_driver, state, connector_type="S3")
