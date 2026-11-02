# pyright: ignore-file

"""S3 connector with IAM_ROLE (STS AssumeRole) auth: create it and check a first full sync.

Skips unless ``S3_ROLE_ARN`` is set. The role must read the shared test bucket and
trust the ``S3_ACCESS_KEY`` user; set ``S3_EXTERNAL_ID`` if its trust policy needs one.
"""

import os
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

from connector_lifecycle import constructor, destructor
from connectors.s3.conftest import s3_connector_config
from connectors.s3.s3_storage_helper import S3StorageHelper
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def s3_role_connector(
    s3_storage: S3StorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root,
) -> AsyncGenerator[Dict[str, Any], None]:
    role_arn = os.getenv("S3_ROLE_ARN")
    if not role_arn:
        pytest.skip("S3_ROLE_ARN is not set; the IAM role auth test needs a role the base key can assume.")
    config = s3_connector_config()
    config["auth"]["roleArn"] = role_arn
    external_id = os.getenv("S3_EXTERNAL_ID")
    if external_id:
        config["auth"]["externalId"] = external_id
    state = await constructor(
        s3_storage,
        pipeshub_client,
        graph_provider,
        sample_data_root,
        storage_name="S3 bucket",
        connector_type="S3",
        connector_config=config,
        auth_type="IAM_ROLE",
    )
    yield state
    await destructor(s3_storage, pipeshub_client, graph_provider, state, connector_type="S3")


@pytest.mark.integration
@pytest.mark.s3
@pytest.mark.asyncio(loop_scope="session")
async def test_s3_iam_role_full_sync(
    s3_role_connector: Dict[str, Any],
    graph_provider: GraphProviderProtocol,
) -> None:
    connector_id = s3_role_connector["connector_id"]
    await graph_provider.assert_min_records(connector_id, s3_role_connector["uploaded_count"])
    await graph_provider.assert_no_orphan_records(connector_id)
