# pyright: ignore-file

"""S3 on a shared AWS account: a read-only key that can list more buckets than it may read.

Runs against real AWS with the suite's S3 key and writes nothing to S3. The key
must be able to list its buckets, read at least one, and be refused on at least
one other; otherwise the module skips.

  TC-SHARED-001  Only the readable buckets get a group; every object of them has a record
  TC-SHARED-002  One notice names every bucket that was skipped; no per-bucket notices
  TC-SHARED-003  A forced full sync of unchanged objects keeps every record at version 0
  TC-SHARED-004  A record streams the object's bytes; its console link encodes the key
  TC-SHARED-005  A wrong secret is refused with AWS's own reason
  TC-SHARED-006  Bucket Names options page with AWS's own token and search with its Prefix
  Teardown       Deleting the connector removes all of its graph
"""

import logging
import os
import time
import uuid
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from botocore.exceptions import ClientError

from connectors.s3.s3_storage_helper import S3StorageHelper
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import async_poll_until
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("s3-shared-account-test")

SYNC_TIMEOUT = 600


def _bucket_access(storage: S3StorageHelper) -> tuple[dict[str, list[str]], list[str]]:
    """Readable bucket -> its object keys, and the buckets the key may list but not read."""
    client = storage._client
    readable: dict[str, list[str]] = {}
    denied: list[str] = []
    for bucket in client.list_buckets().get("Buckets", []):
        name = bucket["Name"]
        try:
            readable[name] = storage.list_objects(name)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "AccessDenied":
                raise
            denied.append(name)
    return readable, sorted(denied)


def s3_storage_key(name: str) -> str:
    value = os.getenv(name)
    assert value, f"{name} is not set"
    return value


def _finished_after(client: PipeshubClient, connector_id: str, since_ms: int) -> dict[str, Any] | None:
    connector = client.get_connector(connector_id)
    finished = connector.get("lastSyncFinishedAtTimestamp") or 0
    return connector if finished > since_ms and connector.get("status") == "IDLE" else None


def _wait_for_sync(client: PipeshubClient, connector_id: str, since_ms: int) -> dict[str, Any]:
    deadline = time.monotonic() + SYNC_TIMEOUT
    while time.monotonic() < deadline:
        done = _finished_after(client, connector_id, since_ms)
        if done:
            return done
        time.sleep(5)
    raise TimeoutError(f"no sync of {connector_id} finished within {SYNC_TIMEOUT}s")


def _notices(client: PipeshubClient, instance_name: str) -> list[dict[str, Any]]:
    resp = client.request("GET", "/api/v1/notifications", params={"limit": 100})
    assert resp.status_code == 200, f"notifications failed: HTTP {resp.status_code}: {resp.text[:300]}"
    return [n for n in resp.json().get("notifications", []) if str(n.get("title", "")).startswith(f"{instance_name}: ")]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def shared_account(
    s3_storage: S3StorageHelper, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
):
    readable, denied = _bucket_access(s3_storage)
    if not readable or not denied:
        pytest.skip("needs a key that reads some listed buckets and is refused on others")
    instance_name = f"s3-shared-{uuid.uuid4().hex[:8]}"
    config = {"auth": {"accessKey": s3_storage_key("S3_ACCESS_KEY"), "secretKey": s3_storage_key("S3_SECRET_KEY")}}
    started = int(time.time() * 1000)
    connector = pipeshub_client.create_connector("S3", instance_name, config=config)
    try:
        pipeshub_client.toggle_sync(connector.connector_id, enable=True)
        _wait_for_sync(pipeshub_client, connector.connector_id, started)
        yield {
            "connector_id": connector.connector_id,
            "instance_name": instance_name,
            "readable": readable,
            "denied": denied,
        }
    finally:
        try:
            pipeshub_client.delete_connector(connector.connector_id)
            await graph_provider.assert_connector_graph_fully_cleaned(connector.connector_id, timeout=300)
        except Exception as e:
            logger.error("connector %s cleanup leaked: %s", connector.connector_id, e)
            raise


@pytest.mark.integration
@pytest.mark.s3
@pytest.mark.asyncio(loop_scope="session")
class TestS3SharedAccount:
    async def test_tc_shared_001_only_readable_buckets_are_synced(
        self, shared_account, graph_provider: GraphProviderProtocol,
    ) -> None:
        cid = shared_account["connector_id"]
        groups = set(await graph_provider.fetch_record_group_names(cid))
        assert groups == set(shared_account["readable"]), (
            f"a bucket the key cannot read must not get an empty group; groups {sorted(groups)}, "
            f"readable {sorted(shared_account['readable'])}"
        )
        for bucket, keys in shared_account["readable"].items():
            for key in keys:
                if key.endswith("/"):
                    continue
                record = await graph_provider.get_record_by_external_id(cid, f"{bucket}/{key}")
                assert record is not None, f"{bucket}/{key} was listed by S3 but has no record"

    async def test_tc_shared_002_one_notice_names_the_skipped_buckets(
        self, shared_account, pipeshub_client: PipeshubClient,
    ) -> None:
        name, denied = shared_account["instance_name"], shared_account["denied"]

        async def notices():
            found = _notices(pipeshub_client, name)
            return found if any(n["title"].endswith("Buckets skipped: access denied") for n in found) else None

        found = await async_poll_until(notices, timeout=60, interval=3, description="skipped-bucket notice")
        titles = [n["title"] for n in found]
        assert titles == [f"{name}: Buckets skipped: access denied"], f"expected one notice for the run, got {titles}"
        message = found[0]["message"]
        assert f"{len(denied)} bucket(s)" in message, message
        assert all(b in message for b in denied[:10]), message

    async def test_tc_shared_003_a_full_resync_of_unchanged_objects_keeps_version_zero(
        self, shared_account, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        cid = shared_account["connector_id"]
        before = await graph_provider.count_records(cid)
        started = int(time.time() * 1000)
        pipeshub_client.resync_connector(cid, full_sync=True, connector_name="S3")
        finished = _wait_for_sync(pipeshub_client, cid, started)

        assert finished.get("lastSyncOutcome") == "succeeded", finished
        assert await graph_provider.count_records(cid) == before
        for bucket, keys in shared_account["readable"].items():
            for key in (k for k in keys if not k.endswith("/")):
                record = await graph_provider.get_record_by_external_id(cid, f"{bucket}/{key}")
                assert record is not None and record.version == 0, (
                    f"{bucket}/{key} is at version {getattr(record, 'version', None)} with unchanged content"
                )
        # Still one notice: the same skipped buckets are not announced again.
        assert len(_notices(pipeshub_client, shared_account["instance_name"])) == 1

    async def test_tc_shared_004_stream_and_console_link(
        self, shared_account, s3_storage: S3StorageHelper, pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        cid = shared_account["connector_id"]
        bucket, keys = next((b, k) for b, k in sorted(shared_account["readable"].items()) if any(not x.endswith("/") for x in k))
        key = next(k for k in keys if not k.endswith("/"))
        record = await graph_provider.get_record_by_external_id(cid, f"{bucket}/{key}")
        assert record is not None

        assert record.weburl == f"https://s3.console.aws.amazon.com/s3/object/{bucket}?prefix={quote(key, safe='/')}"
        resp = pipeshub_client.stream_record(record.id)
        assert resp.status_code == 200, f"stream failed: HTTP {resp.status_code}: {resp.text[:300]}"
        expected = s3_storage._client.get_object(Bucket=bucket, Key=key)["Body"].read()
        assert resp.content == expected, f"streamed {len(resp.content)} bytes, S3 holds {len(expected)}"

    async def test_tc_shared_005_a_wrong_secret_is_refused_with_aws_reason(
        self, shared_account, pipeshub_client: PipeshubClient,
    ) -> None:
        config = {"auth": {"accessKey": s3_storage_key("S3_ACCESS_KEY"), "secretKey": "not-the-secret-" + uuid.uuid4().hex}}
        connector = pipeshub_client.create_connector("S3", f"s3-badsecret-{uuid.uuid4().hex[:8]}", config=config)
        try:
            resp = pipeshub_client.request(
                "POST", f"/api/v1/connectors/{connector.connector_id}/toggle", json={"type": "sync"},
            )
            assert resp.status_code == 400, f"enable must be refused; got HTTP {resp.status_code}: {resp.text[:300]}"
            assert "SignatureDoesNotMatch" in resp.text, f"the refusal must carry AWS's reason; body: {resp.text[:300]}"
            assert "not-the-secret" not in resp.text
        finally:
            pipeshub_client.delete_connector(connector.connector_id)

    async def test_tc_shared_006_bucket_options_page_and_search_at_aws(
        self, shared_account, s3_storage: S3StorageHelper, pipeshub_client: PipeshubClient,
    ) -> None:
        cid = shared_account["connector_id"]
        names = sorted(set(shared_account["readable"]) | set(shared_account["denied"]))
        # The shared prefix of the most buckets, so the search spans more than one page.
        prefix = max({n[:4] for n in names}, key=lambda p: sum(n.startswith(p) for n in names))
        expected = [n for n in names if n.startswith(prefix)]
        if len(expected) < 3:
            pytest.skip("needs at least three buckets sharing a name prefix")

        seen: list[str] = []
        cursor = None
        for _ in range(len(expected)):
            page = pipeshub_client.get_filter_options(cid, "buckets", limit=2, cursor=cursor, search=prefix)
            options = [o["id"] for o in page.get("options", [])]
            assert len(options) <= 2, f"a page is at most the limit; got {options}"
            seen += options
            cursor = page.get("cursor")
            if not page.get("hasMore"):
                break
            assert cursor, "a page with more after it must carry AWS's continuation token"
        assert seen == expected, f"pages {seen} != buckets starting with {prefix!r} {expected}"
