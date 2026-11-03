# pyright: ignore-file

"""
S3 Connector – Bucket Names filter integration test
=====================================================

  TC-ACCESS-REVOKE-001 — A bucket taken out of the Bucket Names filter leaves the index:
                         its records and record group go, the other bucket's stay.

Needs a second pre-provisioned bucket the suite's S3 key can read and write, named
in ``S3_SECOND_TEST_BUCKET``; tests never create buckets. Without it the module skips.
The MinIO suite runs the same case on every CI run (``minio_bucket_filter_integration_test.py``).
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

from connector_lifecycle import RESOURCE_NAME  # noqa: E402
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from connectors.s3.conftest import s3_connector_config  # noqa: E402
from connectors.s3.s3_bucket_filter import (  # noqa: E402
    assert_deselected_bucket_leaves_the_index,
    two_bucket_connector,
)
from connectors.s3.s3_storage_helper import S3StorageHelper  # type: ignore[import-not-found]  # noqa: E402

SECOND_BUCKET_ENV = "S3_SECOND_TEST_BUCKET"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def s3_two_bucket_connector(
    s3_storage: S3StorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    second = os.getenv(SECOND_BUCKET_ENV)
    if not second:
        pytest.skip(f"{SECOND_BUCKET_ENV} is not set: TC-ACCESS-REVOKE-001 needs a second pre-provisioned bucket")
    async with two_bucket_connector(
        s3_storage, pipeshub_client, graph_provider,
        connector_type="S3", config=s3_connector_config(), kept=RESOURCE_NAME, second=second,
    ) as state:
        yield state


@pytest.mark.integration
@pytest.mark.s3
@pytest.mark.asyncio(loop_scope="session")
class TestS3BucketFilter:
    async def test_tc_access_revoke_001_a_deselected_bucket_leaves_the_index(
        self,
        s3_two_bucket_connector: Dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        await assert_deselected_bucket_leaves_the_index(s3_two_bucket_connector, pipeshub_client, graph_provider)
