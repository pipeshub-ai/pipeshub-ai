# pyright: ignore-file

"""S3 setup: the Enable connection test proves the key can read the buckets it syncs (PP-112)."""

import logging
import uuid

import pytest

from connectors.s3.conftest import s3_connector_config
from connectors.s3.s3_storage_helper import S3StorageHelper
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("s3-setup-test")

# Owned by AWS: any other account's key can list its own buckets but gets 403 listing this one.
UNREADABLE_BUCKET = "aws"


@pytest.mark.integration
@pytest.mark.s3
@pytest.mark.asyncio(loop_scope="session")
class TestS3Setup:
    async def test_tc_conn_001_enable_refused_for_a_bucket_the_key_cannot_list(
        self, s3_storage: S3StorageHelper, pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-CONN-001: a key that passes ListBuckets but cannot list the configured bucket is refused on Enable."""
        config = s3_connector_config()
        config["auth"]["bucket"] = UNREADABLE_BUCKET
        connector = pipeshub_client.create_connector(
            "S3", f"s3-conn-test-{uuid.uuid4().hex[:8]}", config=config,
        )
        try:
            resp = pipeshub_client.request(
                "POST", f"/api/v1/connectors/{connector.connector_id}/toggle", json={"type": "sync"},
            )
            assert resp.status_code == 400, (
                f"enable must be refused for a bucket the key cannot list; got HTTP {resp.status_code}: {resp.text[:300]}"
            )
            assert "s3:ListBucket" in resp.text and UNREADABLE_BUCKET in resp.text, (
                f"the refusal must name s3:ListBucket and the bucket; body: {resp.text[:300]}"
            )
            assert not pipeshub_client.get_connector(connector.connector_id).get("isActive"), (
                "the enable was refused yet the connector is active"
            )
        finally:
            try:
                pipeshub_client.delete_connector(connector.connector_id)
            except Exception as e:
                logger.error("connector %s cleanup leaked: %s", connector.connector_id, e)
