# pyright: ignore-file

"""MinIO connector lifecycle, links and credentials, against the compose MinIO server.

Every object this module writes is under its own prefix, removed at teardown.

  TC-LIFE-001  Disable stops syncing and keeps the data; re-enable resumes from the saved checkpoint
  TC-LIFE-002  Delete is refused while an agent uses the connector, naming the agent
  TC-LIFE-003  Only the creator or an admin can delete
  TC-LIFE-004  A record streams the object's bytes; its link opens the object in the MinIO Console
  TC-LIFE-005  A wrong secret is refused with a reason naming the Secret Key; secrets are not echoed
  TC-LIFE-006  An endpoint without http:// takes its scheme from Use SSL/TLS and syncs
  TC-LIFE-007  Delete while a sync runs: it stops, a second delete is refused, and every node goes
  TC-LIFE-008  A key that may read only some chosen folders connects, syncs those, and names the others
"""

import logging
import os
import subprocess
import time
import uuid
from typing import Any
from urllib.parse import quote, urlparse

import pytest
import pytest_asyncio
import requests
from connectors.minio.conftest import minio_bucket, minio_connector_config
from connectors.minio.minio_storage_helper import MinioStorageHelper
from helper.clients.agents_client import AgentsClient
from helper.graph_provider import GraphProviderProtocol
from helper.second_user import create_second_user, delete_second_user
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("minio-connector-lifecycle-test")

SYNC_TIMEOUT = 900
# More than one ListObjectsV2 page (1000 keys), and enough that a full sync is still
# running when the test looks for it.
FILE_COUNT = 1200
# The Console as the test process reaches it; the connector's links name it as the connector does.
CONSOLE_URL = os.getenv("MINIO_TEST_CONSOLE_URL", "http://localhost:9001")
TRICKY_KEY = "Résumé dossier/naïve & co #1+2.txt"
# The compose MinIO, for `mc admin` (users and policies have no S3 API). CI runs compose
# without a project name, so its container is docker-compose-minio-1.
MINIO_CONTAINER = os.getenv("MINIO_CONTAINER", "docker-compose-minio-1")


def _mc(*args: str) -> str:
    """Run mc in the MinIO container; skip locally and fail in CI when it cannot be reached."""
    root = minio_connector_config()["auth"]
    alias = ["mc", "alias", "set", "it", "http://localhost:9000", root["accessKey"], root["secretKey"]]
    try:
        subprocess.run(["docker", "exec", MINIO_CONTAINER, *alias], check=True, capture_output=True, timeout=60)
        done = subprocess.run(["docker", "exec", "-i", MINIO_CONTAINER, "mc", *args], check=True, capture_output=True, timeout=60, text=True)
    except (OSError, subprocess.SubprocessError) as e:
        if os.getenv("CI"):
            raise
        pytest.skip(f"mc in {MINIO_CONTAINER} not reachable (set MINIO_CONTAINER): {e}")
    return done.stdout


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


def _config_for(prefix: str, **auth: Any) -> dict[str, Any]:
    config = minio_connector_config()
    config["auth"].update(auth)
    config["filters"] = {
        "sync": {
            "values": {
                "buckets": {"operator": "in", "value": [minio_bucket()], "type": "multiselect"},
                "folder_paths": {"operator": "in", "value": [prefix.rstrip("/")], "type": "list"},
            }
        },
        # Sync is under test, not indexing: keep the files off the embedding pipeline.
        "indexing": {"values": {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": True}}},
    }
    return config


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def synced(minio_storage: MinioStorageHelper, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol):
    """A team MinIO connector on this module's prefix, after its first sync."""
    bucket = minio_bucket()
    prefix = f"it-life-{uuid.uuid4().hex[:8]}/"
    keys = [f"{prefix}batch-{i // 100}/file-{i:04d}.txt" for i in range(FILE_COUNT)] + [f"{prefix}{TRICKY_KEY}"]
    for i, key in enumerate(keys):
        if i == len(keys) - 1:
            # LastModified has one-second precision: the last object alone sits in the checkpoint's second.
            time.sleep(2)
        minio_storage._client.put_object(Bucket=bucket, Key=key, Body=f"lifecycle file {i} {key}".encode())
    modified = {
        o["Key"]: o["LastModified"]
        for page in minio_storage._client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
        for o in page.get("Contents") or []
    }
    started = int(time.time() * 1000)
    state: dict[str, Any] = {"bucket": bucket, "prefix": prefix, "files": keys, "modified": modified}
    connector = None
    try:
        connector = pipeshub_client.create_connector(
            "MinIO", f"minio-life-{uuid.uuid4().hex[:8]}", scope="team", config=_config_for(prefix),
        )
        state["connector_id"] = connector.connector_id
        pipeshub_client.toggle_sync(connector.connector_id, enable=True)
        _wait_for_sync(pipeshub_client, connector.connector_id, started)
        state["records"] = await graph_provider.count_records(connector.connector_id)
        # Every object and every folder above one (folders are FILE records too), so the listing went past its first page.
        folders = {key.rsplit("/", i)[0] + "/" for key in keys for i in range(1, key.count("/") + 1)}
        assert await graph_provider.count_records_by_type(connector.connector_id, "FILE") == len(keys) + len(folders)
        yield state
    finally:
        try:
            if connector is not None and not state.get("deleted"):
                pipeshub_client.delete_connector(connector.connector_id)
                await graph_provider.assert_connector_graph_fully_cleaned(connector.connector_id, timeout=300)
        finally:
            for key in keys:
                minio_storage._client.delete_object(Bucket=bucket, Key=key)


async def _settled_update_times(graph_provider: GraphProviderProtocol, cid: str, synced: dict[str, Any], keys: list[str]) -> dict[str, int]:
    """Each file record's last update time, once no file is still waiting for or in indexing."""
    deadline = time.monotonic() + 900
    while True:
        records = {k: await graph_provider.get_record_by_external_id(cid, f"{synced['bucket']}/{k}") for k in keys}
        busy = [k for k, r in records.items() if r is None or r.indexing_status in ("NOT_STARTED", "QUEUED", "IN_PROGRESS")]
        if not busy:
            return {k: r.updated_at for k, r in records.items()}
        assert time.monotonic() < deadline, f"indexing never settled: {busy[:5]}"
        time.sleep(5)


@pytest.mark.integration
@pytest.mark.minio
@pytest.mark.asyncio(loop_scope="session")
class TestMinioLifecycle:
    @pytest.mark.order(1)
    async def test_tc_life_001_disable_keeps_data_and_re_enable_resumes(
        self, synced, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        cid = synced["connector_id"]
        sync_point_key = f"FILE/bucket/{synced['bucket']}/{synced['prefix']}"
        saved = await graph_provider.get_sync_point(cid, sync_point_key)
        assert saved and saved.get("last_sync_time"), f"no checkpoint saved for {sync_point_key}: {saved}"
        checkpoint_ms = int(saved["last_sync_time"])
        # A sample keeps the polling short; indexing writes to records too, so wait until it is done.
        sample = sorted(synced["files"], key=lambda k: synced["modified"][k])[::100] + [synced["files"][-1]]
        before = await _settled_update_times(graph_provider, cid, synced, sample)
        pipeshub_client.toggle_sync(cid, enable=False)
        assert not pipeshub_client.get_connector(cid).get("isActive")

        resync = pipeshub_client.request("POST", f"/api/v1/connectors/{cid}/resync", json={"connectorName": "MinIO", "fullSync": False})
        assert resync.status_code >= 400, f"a disabled connector must not sync; got HTTP {resync.status_code}"
        assert await graph_provider.count_records(cid) == synced["records"], "disabling must keep the data"

        started = int(time.time() * 1000)
        pipeshub_client.toggle_sync(cid, enable=True)
        finished = _wait_for_sync(pipeshub_client, cid, started)

        assert finished.get("lastSyncOutcome") == "succeeded", finished
        assert await graph_provider.count_records(cid) == synced["records"]
        after = {key: await graph_provider.get_record_by_external_id(cid, f"{synced['bucket']}/{key}") for key in before}
        # Re-sent objects keep version 0: their content did not change.
        assert all(r is not None and r.version == 0 for r in after.values()), {k: getattr(r, "version", None) for k, r in after.items()}
        # Resumed from the saved checkpoint: objects older than it are not re-sent. The checkpoint trails
        # the listing's start by the stores' 15-minute clock-skew allowance, so objects written just
        # before the first sync are, by design, read again.
        older = [k for k in before if synced["modified"][k].timestamp() * 1000 < checkpoint_ms]
        resent = [k for k in older if after[k].updated_at != before[k]]
        assert not resent, f"re-enable re-sent objects older than the checkpoint: {resent}"
        if not older:
            logger.info("TC-LIFE-001: every object is inside the checkpoint's clock-skew window; resume shown by the saved checkpoint only")

    @pytest.mark.order(2)
    async def test_tc_life_002_delete_refused_while_an_agent_uses_it(
        self, synced, pipeshub_client: PipeshubClient,
    ) -> None:
        # Its own connector: a deleted agent is kept (soft delete) with its knowledge link,
        # which would otherwise stay behind as a node of the connector the other tests clean up.
        connector = pipeshub_client.create_connector(
            "MinIO", f"minio-agent-use-{uuid.uuid4().hex[:8]}", scope="team", config=_config_for(synced["prefix"]),
        )
        cid = connector.connector_id
        agents = AgentsClient(pipeshub_client)
        name = f"it-minio-agent-{uuid.uuid4().hex[:6]}"
        agent_key = None
        try:
            created = agents.create_agent(
                name=name, description="Uses the MinIO connector as knowledge.", systemPrompt="Answer from the files.",
                knowledge=[{"connectorId": cid, "filters": {}}],
            )
            assert created.status_code in (200, 201), f"creating the agent: {created.status_code} {created.text[:300]}"
            agent_key = created.json()["agent"]["_key"]
            resp = pipeshub_client.request("DELETE", f"/api/v1/connectors/{cid}")
            assert resp.status_code == 409, f"delete must be refused while an agent uses it; got {resp.status_code}"
            assert name in resp.text, f"the refusal must name the agent; body: {resp.text[:300]}"
            assert pipeshub_client.get_connector(cid).get("status") != "DELETING"
        finally:
            if agent_key:
                agents.delete_agent(agent_key)
            pipeshub_client.delete_connector(cid)

    @pytest.mark.order(3)
    async def test_tc_life_003_only_the_creator_or_an_admin_can_delete(
        self, synced, pipeshub_client: PipeshubClient,
    ) -> None:
        cid = synced["connector_id"]
        user = create_second_user(pipeshub_client)
        try:
            resp = requests.delete(f"{user.base_url}/api/v1/connectors/{cid}", headers=user.headers, timeout=user.timeout)
            assert resp.status_code in (403, 404), f"a member who did not create it must not delete it; got {resp.status_code}"
            assert pipeshub_client.get_connector(cid).get("status") != "DELETING"
        finally:
            delete_second_user(pipeshub_client, user, strict=True)

    @pytest.mark.order(4)
    async def test_tc_life_004_stream_and_console_link(
        self, synced, minio_storage: MinioStorageHelper, pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        cid, bucket = synced["connector_id"], synced["bucket"]
        key = f"{synced['prefix']}{TRICKY_KEY}"
        record = await graph_provider.get_record_by_external_id(cid, f"{bucket}/{key}")
        assert record is not None

        endpoint = urlparse(minio_connector_config()["auth"]["endpointUrl"])
        console_host = f"{endpoint.scheme}://{endpoint.hostname}:9001"
        path = f"/browser/{quote(bucket, safe='')}/{quote(key, safe='')}"
        assert record.weburl == f"{console_host}{path}", record.weburl
        # The Console answers 200 for any path; the S3 API port reads it as a bucket named "browser".
        assert requests.get(f"{CONSOLE_URL}{path}", timeout=30).status_code == 200
        api = os.getenv("MINIO_TEST_ENDPOINT", "http://localhost:9000")
        assert requests.get(f"{api}{path}", timeout=30).status_code == 403

        resp = pipeshub_client.stream_record(record.id)
        assert resp.status_code == 200, f"stream failed: HTTP {resp.status_code}: {resp.text[:300]}"
        expected = minio_storage._client.get_object(Bucket=bucket, Key=key)["Body"].read()
        assert resp.content == expected

    @pytest.mark.order(5)
    async def test_tc_life_005_a_wrong_secret_names_the_secret_key(self, synced, pipeshub_client: PipeshubClient) -> None:
        wrong = "not-the-secret-" + uuid.uuid4().hex
        connector = pipeshub_client.create_connector(
            "MinIO", f"minio-badsecret-{uuid.uuid4().hex[:8]}", config=_config_for(synced["prefix"], secretKey=wrong),
        )
        try:
            resp = pipeshub_client.request("POST", f"/api/v1/connectors/{connector.connector_id}/toggle", json={"type": "sync"})
            assert resp.status_code == 400, f"enable must be refused; got HTTP {resp.status_code}: {resp.text[:300]}"
            assert "Secret Key" in resp.text, f"the refusal must say what to fix; body: {resp.text[:300]}"
            assert wrong not in resp.text
            config = pipeshub_client.request("GET", f"/api/v1/connectors/{connector.connector_id}/config")
            assert config.status_code == 200, config.text[:300]
            assert wrong not in config.text, "the secret key must not be returned"
        finally:
            pipeshub_client.delete_connector(connector.connector_id)

    @pytest.mark.order(6)
    async def test_tc_life_006_an_endpoint_without_a_scheme_uses_use_ssl(
        self, synced, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        endpoint = urlparse(minio_connector_config()["auth"]["endpointUrl"]).netloc
        started = int(time.time() * 1000)
        connector = pipeshub_client.create_connector(
            "MinIO", f"minio-bare-{uuid.uuid4().hex[:8]}",
            config=_config_for(synced["prefix"], endpointUrl=endpoint, useSsl=False),
        )
        cid = connector.connector_id
        try:
            pipeshub_client.toggle_sync(cid, enable=True)
            finished = _wait_for_sync(pipeshub_client, cid, started)
            assert finished.get("lastSyncOutcome") == "succeeded", finished
            assert await graph_provider.count_records(cid) >= len(synced["files"])
        finally:
            pipeshub_client.delete_connector(cid)
            await graph_provider.assert_connector_graph_fully_cleaned(cid, timeout=300)

    @pytest.mark.order(7)
    async def test_tc_life_007_delete_while_syncing_stops_it_and_removes_everything(
        self, synced, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        cid = synced["connector_id"]
        pipeshub_client.resync_connector(cid, full_sync=True, connector_name="MinIO")
        # The resync is only queued; delete once the sync is seen running.
        deadline = time.monotonic() + 60
        while pipeshub_client.get_connector(cid).get("status") not in ("FULL_SYNCING", "SYNCING"):
            assert time.monotonic() < deadline, "the full sync was never seen running"
            time.sleep(0.2)

        first = pipeshub_client.request("DELETE", f"/api/v1/connectors/{cid}")
        assert first.status_code in (200, 202), f"delete: HTTP {first.status_code}: {first.text[:300]}"
        synced["deleted"] = True
        second = pipeshub_client.request("DELETE", f"/api/v1/connectors/{cid}")
        if second.status_code != 404:  # 404 once the delete already finished
            assert second.status_code == 409, f"a second delete must be refused; got HTTP {second.status_code}"
            assert "already in progress" in second.text

        await graph_provider.assert_connector_graph_fully_cleaned(cid, timeout=300)
        # The stopped sync writes nothing after the delete.
        time.sleep(10)
        assert await graph_provider.count_any_nodes_with_connector_id(cid) == 0

    @pytest.mark.order(8)
    async def test_tc_life_008_a_key_that_reads_only_some_folders_syncs_those(
        self, minio_storage: MinioStorageHelper, pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        bucket = minio_bucket()
        prefix = f"it-folders-{uuid.uuid4().hex[:8]}/"
        readable, locked = f"{prefix}open/notes.txt", f"{prefix}locked/payroll.txt"
        user, secret, policy = f"it-{uuid.uuid4().hex[:8]}", uuid.uuid4().hex, f"it-open-{uuid.uuid4().hex[:8]}"
        for key in (readable, locked):
            minio_storage._client.put_object(Bucket=bucket, Key=key, Body=key.encode())
        statement = (
            '{"Version":"2012-10-17","Statement":['
            f'{{"Effect":"Allow","Action":["s3:ListBucket","s3:GetBucketLocation"],"Resource":["arn:aws:s3:::{bucket}"]}},'
            f'{{"Effect":"Allow","Action":["s3:GetObject"],"Resource":["arn:aws:s3:::{bucket}/{prefix}open/*"]}}]}}'
        )
        _mc("admin", "user", "add", "it", user, secret)
        cid = None
        try:
            subprocess.run(
                ["docker", "exec", "-i", MINIO_CONTAINER, "sh", "-c", f"cat > /tmp/{policy}.json"],
                input=statement, text=True, check=True, timeout=60,
            )
            _mc("admin", "policy", "create", "it", policy, f"/tmp/{policy}.json")
            _mc("admin", "policy", "attach", "it", policy, "--user", user)
            name = f"minio-folders-{uuid.uuid4().hex[:8]}"
            config = _config_for(prefix, accessKey=user, secretKey=secret)
            config["filters"]["sync"]["values"]["folder_paths"]["value"] = [f"{prefix}open", f"{prefix}locked"]
            cid = pipeshub_client.create_connector("MinIO", name, scope="team", config=config).connector_id
            started = int(time.time() * 1000)
            resp = pipeshub_client.request("POST", f"/api/v1/connectors/{cid}/toggle", json={"type": "sync"})
            assert resp.status_code == 200, f"a key that reads one chosen folder must connect; got {resp.status_code}: {resp.text[:300]}"
            finished = _wait_for_sync(pipeshub_client, cid, started)
            assert finished.get("lastSyncOutcome") == "succeeded", finished

            record = await graph_provider.get_record_by_external_id(cid, f"{bucket}/{readable}")
            assert record is not None
            assert pipeshub_client.stream_record(record.id).content == readable.encode()
            notices = pipeshub_client.request("GET", "/api/v1/notifications", params={"limit": 100}).json().get("notifications", [])
            warned = [n for n in notices if n.get("title") == f"{name}: Some folders cannot be read"]
            assert warned and f"folder '{prefix}locked/'" in warned[0].get("message", ""), [n.get("title") for n in notices][:10]
        finally:
            if cid:
                pipeshub_client.delete_connector(cid)
                await graph_provider.assert_connector_graph_fully_cleaned(cid, timeout=300)
            _mc("admin", "user", "remove", "it", user)
            _mc("admin", "policy", "remove", "it", policy)
            for key in (readable, locked):
                minio_storage._client.delete_object(Bucket=bucket, Key=key)
