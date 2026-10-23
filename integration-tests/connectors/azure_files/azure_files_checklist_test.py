# pyright: ignore-file

"""
Azure Files connector – connector checklist integration tests
=============================================================

Each case creates its own short-lived shares (deleted afterwards) and its own
connector, against the real storage account.

  TC-SAS-EXPIRED      — a SAS that expires after the first sync: the next sync deletes
                        nothing, the owner is told the credentials were rejected, and
                        opening a file asks to reconnect (409)
  TC-KEY-REJECTED     — a wrong account key is refused on Enable, naming the rejected
                        credentials, and the owner is notified
  TC-LIST-ONLY-SAS    — a List-only SAS is refused on Enable, naming the Read permission
  TC-ENDPOINT         — a connection string pointing at a private address or plain http
                        is refused on Enable
  TC-SHARE-GONE-GROUP — a deleted selected share takes its record group too, and the
                        owner is told once
  TC-FOLDER-PARENTS   — with a folder filter, the chosen folder and those above it are
                        real folder records named by their own name
  TC-FOLDER-DELETE    — deleting a folder removes it and everything under it
  TC-DISABLE-ENABLE   — disabling keeps the records; enabling again keeps their ids
  TC-RECREATE         — deleting the connector and adding the same share again gives a
                        clean sync with no leftovers from the first
"""

import logging
import os
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.azure_files.azure_files_storage_helper import (  # type: ignore[import-not-found]
    AzureFilesStorageHelper,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_until_graph_condition

logger = logging.getLogger("azure-files-checklist-test")

SYNC_TIMEOUT = int(os.getenv("AZURE_FILES_CHECKLIST_SYNC_TIMEOUT", "240"))
# Long enough for the first sync of a two-file share, short enough to wait out.
SAS_LIFETIME = timedelta(seconds=int(os.getenv("AZURE_FILES_CHECKLIST_SAS_SECONDS", "150")))


def _share_name(tag: str) -> str:
    return f"afcheck-{uuid.uuid4().hex[:10]}-{tag}"


def _connection_parts() -> dict[str, str]:
    raw = os.environ["AZURE_FILES_CONNECTION_STRING"]
    return dict(part.split("=", 1) for part in raw.split(";") if "=" in part)


def _sas_connection_string(permission: str, expiry: datetime | None = None) -> str:
    """An account SAS for the File service only, from the test account key."""
    from azure.storage.fileshare import (  # type: ignore[import-not-found]
        AccountSasPermissions,
        ResourceTypes,
        ShareServiceClient,
        generate_account_sas,
    )

    service = ShareServiceClient.from_connection_string(os.environ["AZURE_FILES_CONNECTION_STRING"])
    token = generate_account_sas(
        service.account_name,
        service.credential.account_key,
        resource_types=ResourceTypes(service=True, container=True, object=True),
        permission=AccountSasPermissions(read="r" in permission, list="l" in permission),
        expiry=expiry or datetime.now(UTC) + timedelta(hours=2),
    )
    return f"FileEndpoint={service.primary_endpoint};SharedAccessSignature={token}"


def _wrong_key_connection_string() -> str:
    parts = _connection_parts()
    # A well-formed key that is not the account's.
    wrong = "d3JvbmdrZXk" + "A" * 77 + "=="
    return (
        f"DefaultEndpointsProtocol=https;AccountName={parts['AccountName']};AccountKey={wrong};"
        f"EndpointSuffix={parts.get('EndpointSuffix', 'core.windows.net')}"
    )


def _config(
    shares: list[str] | None, connection_string: str | None = None, folders: list[str] | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {"auth": {
        "connectionString": connection_string or os.getenv("AZURE_FILES_CONNECTION_STRING"),
    }}
    values: dict[str, Any] = {}
    if shares is not None:
        values["shares"] = {"value": shares, "operator": "in", "type": "multiselect"}
    if folders is not None:
        values["folder_paths"] = {"value": folders, "operator": "in", "type": "list"}
    if values:
        config["filters"] = {"sync": {"values": values}}
    return config


def _wait_for_notification_title(client: PipeshubClient, title: str, timeout: float = 90) -> bool:
    """Whether the user has a notification with exactly ``title``; notices go through the broker."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.request("GET", "/api/v1/notifications", params={"limit": 100})
        if resp.status_code == 200 and any(
            n.get("title") == title for n in resp.json().get("notifications", [])
        ):
            return True
        time.sleep(3)
    return False


def _count_notifications(client: PipeshubClient, title: str) -> int:
    resp = client.request("GET", "/api/v1/notifications", params={"limit": 100})
    assert resp.status_code == 200, resp.text[:300]
    return sum(1 for n in resp.json().get("notifications", []) if n.get("title") == title)


@pytest_asyncio.fixture(loop_scope="session")
async def scratch(
    azure_files_storage: AzureFilesStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
):
    """Shares and connectors made by one test, removed after it."""
    made: dict[str, list] = {"shares": [], "connectors": []}

    def share(tag: str) -> str:
        name = _share_name(tag)
        azure_files_storage.create_share(name)
        made["shares"].append(name)
        return name

    def connector(
        shares: list[str] | None,
        *,
        scope: str = "personal",
        connection_string: str | None = None,
        folders: list[str] | None = None,
        enable: bool = True,
    ) -> tuple[str, str]:
        name = f"azure-files-check-{uuid.uuid4().hex[:8]}"
        instance = pipeshub_client.create_connector(
            connector_type="Azure Files",
            instance_name=name,
            scope=scope,
            config=_config(shares, connection_string, folders),
        )
        made["connectors"].append(instance.connector_id)
        if enable:
            pipeshub_client.toggle_sync(instance.connector_id, enable=True)
        return instance.connector_id, name

    yield share, connector, made

    for connector_id in made["connectors"]:
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
            pipeshub_client.delete_connector(connector_id)
            await graph_provider.assert_all_records_cleaned(connector_id, timeout=300)
        except Exception:
            logger.exception("Could not remove connector %s", connector_id)
    for name in made["shares"]:
        azure_files_storage.delete_share(name)


async def _wait(connector_id: str, check: Callable[[], Any], description: str) -> None:
    await wait_until_graph_condition(
        connector_id, check=check, timeout=SYNC_TIMEOUT, poll_interval=10, description=description,
    )


async def _has(graph: GraphProviderProtocol, connector_id: str, *external_ids: str) -> bool:
    for external_id in external_ids:
        if await graph.get_record_by_external_id(connector_id, external_id) is None:
            return False
    return True


async def _ids(graph: GraphProviderProtocol, connector_id: str, *external_ids: str) -> list[str]:
    return [(await graph.get_record_by_external_id(connector_id, e)).id for e in external_ids]


def _resync(client: PipeshubClient, connector_id: str) -> None:
    client.toggle_sync(connector_id, enable=False)
    client.wait(3)
    client.toggle_sync(connector_id, enable=True)


def _enable_refused(client: PipeshubClient, connector_id: str) -> str:
    """Enable the connector, expecting the connection test to refuse it; the refusal's text."""
    resp = client.request("POST", f"/api/v1/connectors/{connector_id}/toggle", json={"type": "sync"})
    assert resp.status_code == 400, f"enable must be refused; got HTTP {resp.status_code}: {resp.text[:300]}"
    assert not client.get_connector(connector_id).get("isActive"), "the enable was refused yet the connector is active"
    return resp.text


@pytest.mark.integration
@pytest.mark.azure_files
@pytest.mark.asyncio(loop_scope="session")
class TestAzureFilesChecklist:

    async def test_tc_sas_expired(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector, _ = scratch
        share = make_share("expiry")
        azure_files_storage.upload_file(share, "a.txt", b"kept when the SAS expires")
        azure_files_storage.upload_file(share, "d/b.txt", b"kept too")
        expiry = datetime.now(UTC) + SAS_LIFETIME
        connector_id, name = make_connector([share], connection_string=_sas_connection_string("rl", expiry))
        expected = (f"{share}/a.txt", f"{share}/d", f"{share}/d/b.txt")

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, *expected)

        await _wait(connector_id, synced, "first sync with a short-lived SAS")
        ids = await _ids(graph_provider, connector_id, *expected)
        count = await graph_provider.count_records(connector_id)

        wait_s = (expiry - datetime.now(UTC)).total_seconds() + 15
        if wait_s > 0:
            pipeshub_client.wait(wait_s)
        # A sync of the running connector, as the schedule would start; Enable would test the connection first.
        pipeshub_client.resync_connector(connector_id, full_sync=True)
        assert _wait_for_notification_title(pipeshub_client, f"{name}: Credentials rejected"), (
            "the owner was not told the credentials were rejected"
        )
        assert await _ids(graph_provider, connector_id, *expected) == ids
        assert await graph_provider.count_records(connector_id) == count

        record = await graph_provider.get_record_by_external_id(connector_id, f"{share}/a.txt")
        with pytest.raises(requests.exceptions.HTTPError) as refused:
            pipeshub_client.stream_record(record.id, timeout=120)
        status = refused.value.response.status_code
        assert status == 409, f"opening a file must ask to reconnect; got {status}"

    async def test_tc_key_rejected(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector, _ = scratch
        share = make_share("badkey")
        azure_files_storage.upload_file(share, "a.txt", b"a")
        connector_id, name = make_connector([share], connection_string=_wrong_key_connection_string(), enable=False)
        text = _enable_refused(pipeshub_client, connector_id)
        assert "rejected the credentials" in text and "AuthenticationFailed" in text, text[:300]
        assert _wait_for_notification_title(pipeshub_client, f"{name}: Connection test failed")
        assert await graph_provider.count_records(connector_id) == 0

    async def test_tc_list_only_sas(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector, _ = scratch
        share = make_share("listonly")
        azure_files_storage.upload_file(share, "a.txt", b"a")
        connector_id, _ = make_connector([share], connection_string=_sas_connection_string("l"), enable=False)
        text = _enable_refused(pipeshub_client, connector_id)
        assert "needs the Read permission" in text, text[:300]

    @pytest.mark.parametrize(
        "endpoint,reason",
        [("https://127.0.0.1:9/", "not a public address"), ("http://{account}.file.core.windows.net/", "https")],
    )
    async def test_tc_endpoint(self, scratch, pipeshub_client, graph_provider, endpoint: str, reason: str) -> None:
        _, make_connector, _ = scratch
        parts = _connection_parts()
        connection_string = (
            f"AccountName={parts['AccountName']};AccountKey={parts['AccountKey']};"
            f"FileEndpoint={endpoint.format(account=parts['AccountName'])}"
        )
        connector_id, _ = make_connector(["any"], connection_string=connection_string, enable=False)
        text = _enable_refused(pipeshub_client, connector_id)
        assert reason in text, text[:300]

    async def test_tc_share_gone_group(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector, _ = scratch
        keep, gone = make_share("keep"), make_share("gone")
        azure_files_storage.upload_file(keep, "k.txt", b"k")
        azure_files_storage.upload_file(gone, "g.txt", b"g")
        connector_id, name = make_connector([keep, gone], scope="team")

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, f"{keep}/k.txt", f"{gone}/g.txt")

        await _wait(connector_id, synced, "first sync of both shares")
        assert await graph_provider.get_record_group_by_external_id(connector_id, gone) is not None

        azure_files_storage.delete_share(gone)
        _resync(pipeshub_client, connector_id)

        async def removed() -> bool:
            return (
                await graph_provider.get_record_by_external_id(connector_id, f"{gone}/g.txt") is None
                and await graph_provider.get_record_group_by_external_id(connector_id, gone) is None
            )

        await _wait(connector_id, removed, "the gone share and its group removed")
        title = f"{name}: File share {gone} not found"
        assert _wait_for_notification_title(pipeshub_client, title)

        _resync(pipeshub_client, connector_id)
        pipeshub_client.wait(60)
        # Not written again by the next sync, and not announced again.
        assert await graph_provider.get_record_group_by_external_id(connector_id, gone) is None
        assert _count_notifications(pipeshub_client, title) == 1
        assert await _has(graph_provider, connector_id, f"{keep}/k.txt")

    async def test_tc_folder_parents(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector, _ = scratch
        share = make_share("parents")
        azure_files_storage.upload_file(share, "a/b/c.txt", b"inside the chosen folder")
        azure_files_storage.upload_file(share, "a/other.txt", b"outside it")
        connector_id, _ = make_connector([share], folders=["a/b"])

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, f"{share}/a/b/c.txt")

        await _wait(connector_id, synced, "sync of the chosen folder")
        a = await graph_provider.get_record_by_external_id(connector_id, f"{share}/a")
        b = await graph_provider.get_record_by_external_id(connector_id, f"{share}/a/b")
        assert (a.record_name, b.record_name) == ("a", "b")
        placeholders = {r.external_record_id for r in await graph_provider.get_placeholder_records(connector_id)}
        assert not placeholders & {f"{share}/a", f"{share}/a/b"}, placeholders
        assert await graph_provider.get_record_parent_external_id(connector_id, f"{share}/a/b") == f"{share}/a"
        assert await graph_provider.get_record_parent_external_id(connector_id, f"{share}/a/b/c.txt") == f"{share}/a/b"
        assert await graph_provider.get_record_by_external_id(connector_id, f"{share}/a/other.txt") is None

    async def test_tc_folder_delete(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector, _ = scratch
        share = make_share("rmdir")
        azure_files_storage.upload_file(share, "keep.txt", b"keep")
        azure_files_storage.upload_file(share, "dir/a.txt", b"a")
        azure_files_storage.upload_file(share, "dir/sub/b.txt", b"b")
        connector_id, _ = make_connector([share])
        tree = (f"{share}/dir", f"{share}/dir/a.txt", f"{share}/dir/sub", f"{share}/dir/sub/b.txt")

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, f"{share}/keep.txt", *tree)

        await _wait(connector_id, synced, "first sync")
        azure_files_storage.delete_directory_tree(share, "dir")
        _resync(pipeshub_client, connector_id)

        async def removed() -> bool:
            for external_id in tree:
                if await graph_provider.get_record_by_external_id(connector_id, external_id) is not None:
                    return False
            return True

        await _wait(connector_id, removed, "the deleted folder and everything under it removed")
        assert await _has(graph_provider, connector_id, f"{share}/keep.txt")

    async def test_tc_disable_enable(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector, _ = scratch
        share = make_share("toggle")
        azure_files_storage.upload_file(share, "a.txt", b"a")
        azure_files_storage.upload_file(share, "d/b.txt", b"b")
        connector_id, _ = make_connector([share])
        expected = (f"{share}/a.txt", f"{share}/d", f"{share}/d/b.txt")

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, *expected)

        await _wait(connector_id, synced, "first sync")
        ids = await _ids(graph_provider, connector_id, *expected)

        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(30)
        assert await _ids(graph_provider, connector_id, *expected) == ids, "disabling must keep the records"

        pipeshub_client.toggle_sync(connector_id, enable=True)
        pipeshub_client.wait(60)
        assert await _ids(graph_provider, connector_id, *expected) == ids, "enabling again must keep their ids"
        assert await graph_provider.count_records(connector_id) == len(expected)

    async def test_tc_recreate(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector, made = scratch
        share = make_share("recreate")
        azure_files_storage.upload_file(share, "a.txt", b"a")
        azure_files_storage.upload_file(share, "d/b.txt", b"b")
        expected = (f"{share}/a.txt", f"{share}/d", f"{share}/d/b.txt")

        first, _ = make_connector([share])

        async def first_synced() -> bool:
            return await _has(graph_provider, first, *expected)

        await _wait(first, first_synced, "first connector's sync")
        first_ids = await _ids(graph_provider, first, *expected)

        pipeshub_client.toggle_sync(first, enable=False)
        pipeshub_client.delete_connector(first)
        made["connectors"].remove(first)
        await graph_provider.assert_all_records_cleaned(first, timeout=300)

        second, _ = make_connector([share])

        async def second_synced() -> bool:
            return await _has(graph_provider, second, *expected)

        await _wait(second, second_synced, "the re-added connector's sync")
        assert await graph_provider.count_records(second) == len(expected)
        assert not set(await _ids(graph_provider, second, *expected)) & set(first_ids)
        assert await graph_provider.count_any_nodes_with_connector_id(first) == 0
