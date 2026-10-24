# pyright: ignore-file

"""
Azure Files connector – PP-341 integration tests
=================================================

Each case creates its own short-lived shares (deleted afterwards) and its own
connector, so they can delete a share or reuse FileIds without touching the
shared test share.

  TC-SHARE-GONE        — a selected share is deleted: deletes in the other share
                         still land and the gone share's records are removed (PP-343)
  TC-LARGE-FILE        — a file larger than the SDK's first download range streams
                         back byte for byte (PP-351)
  TC-NAMES             — %, #, spaces and unicode in names: one record each, and the
                         download returns that file's own bytes (PP-345, PP-346)
  TC-TEAM-SCOPE        — a team connector without a share filter syncs nothing; with
                         one it syncs only that share (PP-347)
  TC-FILEID-TWO-SHARES — folders of two fresh shares keep their own records even
                         when the service hands out the same FileId (PP-344)
  TC-SAS               — a Read+List SAS connection string syncs and downloads; a
                         List-only one is refused by the connection test (PP-349, PP-356)
  TC-FOLDER-RENAME     — renaming a folder moves it and everything below it, keeping
                         every record id (PP-359)
"""

import hashlib
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest
import pytest_asyncio

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import wait_until_graph_condition  # noqa: E402
from connectors.azure_files.azure_files_storage_helper import (  # type: ignore[import-not-found]  # noqa: E402
    AzureFilesStorageHelper,
)

logger = logging.getLogger("azure-files-pp341-test")

SYNC_TIMEOUT = int(os.getenv("AZURE_FILES_PP341_SYNC_TIMEOUT", "240"))


def _share_name(tag: str) -> str:
    return f"pp341-{uuid.uuid4().hex[:10]}-{tag}"


def _sas_connection_string(permission: str) -> str:
    """An account SAS for the File service only, from the test account key."""
    from datetime import datetime, timedelta, timezone

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
        expiry=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    return f"FileEndpoint={service.primary_endpoint};SharedAccessSignature={token}"


def _config(shares: List[str] | None, connection_string: str | None = None) -> Dict[str, Any]:
    config: Dict[str, Any] = {"auth": {
        "connectionString": connection_string or os.getenv("AZURE_FILES_CONNECTION_STRING"),
    }}
    if shares is not None:
        config["filters"] = {"sync": {"values": {
            "shares": {"value": shares, "operator": "in", "type": "multiselect"},
        }}}
    return config


@pytest_asyncio.fixture(loop_scope="session")
async def scratch(
    azure_files_storage: AzureFilesStorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
):
    """Shares and connectors made by one test, removed after it."""
    made: Dict[str, list] = {"shares": [], "connectors": []}

    def share(tag: str) -> str:
        name = _share_name(tag)
        azure_files_storage.create_share(name)
        made["shares"].append(name)
        return name

    def connector(shares: List[str] | None, *, scope: str = "personal", connection_string: str | None = None) -> str:
        instance = pipeshub_client.create_connector(
            connector_type="Azure Files",
            instance_name=f"azure-files-pp341-{uuid.uuid4().hex[:8]}",
            scope=scope,
            config=_config(shares, connection_string),
        )
        made["connectors"].append(instance.connector_id)
        pipeshub_client.toggle_sync(instance.connector_id, enable=True)
        return instance.connector_id

    yield share, connector

    for connector_id in made["connectors"]:
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
            pipeshub_client.delete_connector(connector_id)
            await graph_provider.assert_all_records_cleaned(connector_id, timeout=300)
        except Exception:  # noqa: BLE001 - teardown must reach the shares
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


def _resync(client: PipeshubClient, connector_id: str) -> None:
    client.toggle_sync(connector_id, enable=False)
    client.wait(3)
    client.toggle_sync(connector_id, enable=True)


def _download(client: PipeshubClient, record_id: str) -> bytes:
    response = client.stream_record(record_id, timeout=300)
    assert response.status_code == 200, f"stream returned {response.status_code}"
    return b"".join(response.iter_content(chunk_size=1 << 20))


@pytest.mark.integration
@pytest.mark.azure_files
@pytest.mark.asyncio(loop_scope="session")
class TestAzureFilesPP341:

    async def test_tc_share_gone(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector = scratch
        keep, gone = make_share("keep"), make_share("gone")
        azure_files_storage.upload_file(keep, "secret.txt", b"a distinctive phrase pp341")
        azure_files_storage.upload_file(keep, "stay.txt", b"stays")
        azure_files_storage.upload_file(gone, "old.txt", b"old")
        connector_id = make_connector([keep, gone])

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, f"{keep}/secret.txt", f"{keep}/stay.txt", f"{gone}/old.txt")

        await _wait(connector_id, synced, "first sync of both shares")

        azure_files_storage.delete_share(gone)
        azure_files_storage.delete_file(keep, "secret.txt")
        _resync(pipeshub_client, connector_id)

        async def removed() -> bool:
            return (
                await graph_provider.get_record_by_external_id(connector_id, f"{keep}/secret.txt") is None
                and await graph_provider.get_record_by_external_id(connector_id, f"{gone}/old.txt") is None
            )

        await _wait(connector_id, removed, "deletes after a selected share was deleted")
        assert await _has(graph_provider, connector_id, f"{keep}/stay.txt")

    async def test_tc_large_file(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector = scratch
        share = make_share("large")
        # Larger than the SDK's 32 MiB first range, so the rest arrives in chunks.
        data = os.urandom(40 * 1024 * 1024)
        azure_files_storage.upload_file(share, "large.bin", data)
        connector_id = make_connector([share])

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, f"{share}/large.bin")

        await _wait(connector_id, synced, "sync of the large file")
        record = await graph_provider.get_record_by_external_id(connector_id, f"{share}/large.bin")
        body = _download(pipeshub_client, record.id)
        assert len(body) == len(data)
        assert hashlib.sha256(body).hexdigest() == hashlib.sha256(data).hexdigest()

    async def test_tc_names(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector = scratch
        share = make_share("names")
        names = ["100%25.txt", "a%20b.txt", "a b.txt", " lead.txt", "hash#1.txt", "ünïcödé-文件.txt", "dir x/in #dir.txt"]
        for name in names:
            azure_files_storage.upload_file(share, name, f"content of {name!r}".encode())
        try:
            # Windows clients refuse a trailing space; record what the service does (PP-346).
            azure_files_storage.upload_file(share, "trail .txt", b"content of 'trail .txt'")
            names.append("trail .txt")
            logger.info("TC-NAMES: the service accepted a name ending in a space")
        except Exception as e:  # noqa: BLE001
            logger.info("TC-NAMES: the service refused a name ending in a space: %s", e)
        connector_id = make_connector([share])

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, *(f"{share}/{n}" for n in names))

        await _wait(connector_id, synced, "sync of the special names")
        for name in names:
            record = await graph_provider.get_record_by_external_id(connector_id, f"{share}/{name}")
            assert _download(pipeshub_client, record.id) == f"content of {name!r}".encode(), name

    async def test_tc_team_scope(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector = scratch
        chosen, other = make_share("chosen"), make_share("other")
        azure_files_storage.upload_file(chosen, "c.txt", b"chosen")
        azure_files_storage.upload_file(other, "o.txt", b"other")

        unfiltered = make_connector(None, scope="team")
        pipeshub_client.wait(60)
        assert await graph_provider.count_records(unfiltered) == 0, (
            "a team connector without a share filter must not sync every share"
        )

        filtered = make_connector([chosen], scope="team")

        async def synced() -> bool:
            return await _has(graph_provider, filtered, f"{chosen}/c.txt")

        await _wait(filtered, synced, "team sync of the chosen share")
        assert await graph_provider.get_record_by_external_id(filtered, f"{other}/o.txt") is None

    async def test_tc_fileid_two_shares(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector = scratch
        a, b = make_share("a"), make_share("b")
        azure_files_storage.create_directory(a, "Finance")
        azure_files_storage.create_directory(b, "Docs")
        azure_files_storage.upload_file(a, "Finance/f.txt", b"finance")
        azure_files_storage.upload_file(b, "Docs/d.txt", b"docs")
        ids = (
            azure_files_storage.file_id(a, "Finance", is_directory=True),
            azure_files_storage.file_id(b, "Docs", is_directory=True),
        )
        logger.info("TC-FILEID-TWO-SHARES: FileIds %s (same: %s)", ids, ids[0] == ids[1])
        connector_id = make_connector([a, b])
        expected = (f"{a}/Finance", f"{b}/Docs", f"{a}/Finance/f.txt", f"{b}/Docs/d.txt")

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, *expected)

        await _wait(connector_id, synced, "sync of both shares")
        _resync(pipeshub_client, connector_id)
        pipeshub_client.wait(60)
        assert await _has(graph_provider, connector_id, *expected)
        finance = await graph_provider.get_record_by_external_id(connector_id, f"{a}/Finance")
        docs = await graph_provider.get_record_by_external_id(connector_id, f"{b}/Docs")
        assert finance.id != docs.id
        assert await graph_provider.get_record_parent_external_id(connector_id, f"{a}/Finance/f.txt") == f"{a}/Finance"

    async def test_tc_sas(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]

        make_share, make_connector = scratch
        share = make_share("sas")
        azure_files_storage.upload_file(share, "d/a.txt", b"read through a SAS")
        connector_id = make_connector([share], connection_string=_sas_connection_string("rl"))

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, f"{share}/d", f"{share}/d/a.txt")

        await _wait(connector_id, synced, "sync with a Read+List SAS")
        record = await graph_provider.get_record_by_external_id(connector_id, f"{share}/d/a.txt")
        # No account key in a SAS string: the download streams through the SDK.
        assert _download(pipeshub_client, record.id) == b"read through a SAS"

        with pytest.raises(PipeshubClientError, match="Connection test failed"):
            make_connector([share], connection_string=_sas_connection_string("l"))

    async def test_tc_folder_rename(self, scratch, azure_files_storage, pipeshub_client, graph_provider) -> None:
        make_share, make_connector = scratch
        share = make_share("rename")
        azure_files_storage.upload_file(share, "dir/a.txt", b"a")
        azure_files_storage.upload_file(share, "dir/sub/b.txt", b"b")
        connector_id = make_connector([share])
        old = (f"{share}/dir", f"{share}/dir/a.txt", f"{share}/dir/sub", f"{share}/dir/sub/b.txt")
        new = tuple(p.replace(f"{share}/dir", f"{share}/dir2", 1) for p in old)

        async def synced() -> bool:
            return await _has(graph_provider, connector_id, *old)

        await _wait(connector_id, synced, "first sync")
        ids = [(await graph_provider.get_record_by_external_id(connector_id, p)).id for p in old]

        azure_files_storage.rename_directory(share, "dir", "dir2")
        _resync(pipeshub_client, connector_id)

        async def moved() -> bool:
            return await _has(graph_provider, connector_id, *new)

        await _wait(connector_id, moved, "sync after the folder rename")
        assert [(await graph_provider.get_record_by_external_id(connector_id, p)).id for p in new] == ids
        for p in old:
            assert await graph_provider.get_record_by_external_id(connector_id, p) is None
