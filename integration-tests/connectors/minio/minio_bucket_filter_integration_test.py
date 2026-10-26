# pyright: ignore-file

"""
MinIO Connector – Bucket Names filter integration test
========================================================

  TC-ACCESS-REVOKE-001 — A bucket taken out of the Bucket Names filter leaves the index:
                         its records and record group go, the other bucket's stay.

The integration compose file creates both buckets (``MINIO_TEST_BUCKET`` and
``MINIO_SECOND_TEST_BUCKET``), so this runs wherever the stack runs. The S3 suite
runs the same case only when a second AWS bucket is configured.
"""

import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from connectors.minio.conftest import minio_bucket, minio_connector_config  # noqa: E402
from connectors.minio.minio_storage_helper import MinioStorageHelper  # type: ignore[import-not-found]  # noqa: E402
from connectors.s3.s3_bucket_filter import (  # noqa: E402
    assert_deselected_bucket_leaves_the_index,
    two_bucket_connector,
)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def minio_two_bucket_connector(
    minio_storage: MinioStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    async with two_bucket_connector(
        minio_storage, pipeshub_client, graph_provider,
        connector_type="MinIO", config=minio_connector_config(), kept=minio_bucket(),
        second=os.getenv("MINIO_SECOND_TEST_BUCKET", "pipeshub-connector-test-2"),
    ) as state:
        yield state


@pytest.mark.integration
@pytest.mark.minio
@pytest.mark.asyncio(loop_scope="session")
class TestMinioBucketFilter:
    async def test_tc_access_revoke_001_a_deselected_bucket_leaves_the_index(
        self,
        minio_two_bucket_connector: Dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        await assert_deselected_bucket_leaves_the_index(minio_two_bucket_connector, pipeshub_client, graph_provider)
