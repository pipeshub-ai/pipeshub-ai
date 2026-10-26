# pyright: ignore-file

"""S3 setup: the Enable connection test (PP-112) and its notice (PP-120), and the setup guidance in the registry (PP-102, PP-104, PP-111)."""

import logging
import time
import uuid

import pytest

from connectors.s3.conftest import s3_connector_config
from connectors.s3.s3_storage_helper import S3StorageHelper
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("s3-setup-test")

# Owned by AWS: any other account's key can list its own buckets but gets 403 listing this one.
UNREADABLE_BUCKET = "aws"


def _wait_for_notification_title(client: PipeshubClient, prefix: str, timeout: float = 60) -> str | None:
    """The title of the user's newest notification that starts with ``prefix``; notices go through the broker."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.request("GET", "/api/v1/notifications", params={"limit": 50})
        if resp.status_code == 200:
            for n in resp.json().get("notifications", []):
                if str(n.get("title", "")).startswith(prefix):
                    return n["title"]
        time.sleep(3)
    return None


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
        instance_name = f"s3-conn-test-{uuid.uuid4().hex[:8]}"
        connector = pipeshub_client.create_connector("S3", instance_name, config=config)
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
            # PP-120: the notice names the instance, so two S3 connections can be told apart.
            title = _wait_for_notification_title(pipeshub_client, instance_name)
            assert title == f"{instance_name}: Connection test failed", f"unexpected notification title {title!r}"
        finally:
            try:
                pipeshub_client.delete_connector(connector.connector_id)
            except Exception as e:
                logger.error("connector %s cleanup leaked: %s", connector.connector_id, e)

    async def test_tc_setup_001_registry_shows_read_only_and_team_scope_guidance(
        self, pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-SETUP-001: the S3 registry entry carries the banner the connector page shows."""
        resp = pipeshub_client.request("GET", "/api/v1/connectors/registry", params={"search": "S3"})
        assert resp.status_code == 200, f"registry failed: HTTP {resp.status_code}: {resp.text[:300]}"
        s3 = next((c for c in resp.json().get("connectors", []) if c.get("name") == "S3"), None)
        assert s3 is not None, "S3 is missing from the connector registry"
        info = s3.get("connectorInfo") or ""
        for action in ("s3:ListAllMyBuckets", "s3:ListBucket", "s3:GetBucketLocation", "s3:GetObject"):
            assert action in info, f"the S3 banner must name {action}; got: {info!r}"
        assert "everyone in your organization" in info, f"the S3 banner must warn about team scope; got: {info!r}"
