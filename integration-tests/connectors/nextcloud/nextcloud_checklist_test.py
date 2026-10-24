# pyright: ignore-file

"""
Nextcloud connector: the connector-checklist audit cases, live
==============================================================

Same fixture as nextcloud_integration_test.py (its own connector and run
folder, full sync first).

  TC-ACTIVITY-BACKLOG   — more than one page of activity (Nextcloud gives 200 a page) is caught up in one sync
  TC-NO-FS-ACCESS       — an app password without filesystem access removes nothing and cannot be turned on
  TC-MOUNT-OFFLINE      — what was synced from external storage that went offline is kept
  TC-FAILURE-NOTIFIED   — a sync that fails tells the user, naming the connector
  TC-CREATED-TIME       — the creation time a client sent is kept, and an edit does not move it
  TC-DOWNLOAD-DISABLED  — a file shared with download disabled is not synced
  TC-MANUAL-INDEXING    — with manual indexing on, new files sync but are not indexed
  TC-REENABLE           — what changed while the connector was off syncs once it is on again
  TC-BROWSE             — the Knowledge Hub tree matches the drive
  TC-REGISTRY           — the connector is listed with its auth fields and filters
  TC-BAD-PASSWORD       — a revoked app password removes nothing, is reported, and preview does not sign the user out
  TC-DELETE-DURING-SYNC — deleting the connector while it syncs leaves nothing behind

TC-MOUNT-OFFLINE and TC-FAILURE-NOTIFIED need ``occ`` on the server (``NEXTCLOUD_CONTAINER``, or the stack's
nextcloud-source service) and skip without it.
"""

import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connector_lifecycle import create_connector_and_await_sync, destructor  # type: ignore[import-not-found]
from connectors.nextcloud.nextcloud_source_helper import (  # type: ignore[import-not-found]
    NextcloudSourceHelper,
)
from helper.compose_control import ComposeUnavailable
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.storage_incremental import (
    DEFAULT_SYNC_TIMEOUT_SEC,
    settle_record_baseline,
    sync_until_names_visible,
)
from pipeshub_client import (
    PipeshubClient,  # type: ignore[import-not-found]
)

logger = logging.getLogger("nextcloud-checklist-test")

# More rows than one page of the Activity API holds.
BACKLOG_FILES = 230


def _occ_or_skip(source: NextcloudSourceHelper, *args: str) -> str:
    try:
        return source.occ(*args)
    except ComposeUnavailable as e:
        pytest.skip(f"occ is not available on the test Nextcloud: {e}")


def _instance_name(client: PipeshubClient, connector_id: str) -> str:
    connector = client.get_connector(connector_id)
    return str(connector.get("instanceName") or connector.get("name") or "")


def _notification_titled(client: PipeshubClient, prefix: str, timeout: float = 60) -> str | None:
    """The title of a notification of the user's that starts with ``prefix``; they go through the broker."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.request("GET", "/api/v1/notifications", params={"limit": 50})
        if resp.status_code == 200:
            for n in resp.json().get("notifications", []):
                if str(n.get("title", "")).startswith(prefix):
                    return n["title"]
        time.sleep(3)
    return None


def _manual_indexing(on: bool) -> dict[str, Any]:
    return {"indexing": {"values": {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": on}}}}


async def _record(graph: GraphProviderProtocol, connector_id: str, name: str, timeout: float = 180) -> dict[str, Any]:
    """The stored record named ``name``, waiting for a sync already running to save it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = await graph.get_record_by_name(connector_id, name)
        if record:
            return record
        time.sleep(5)
    raise AssertionError(f"{name} was never synced")


async def _own_connector(
    client: PipeshubClient, graph: GraphProviderProtocol, source: NextcloudSourceHelper,
    state: dict[str, Any], app_password: str, expected_records: int,
) -> str:
    """A second connector on the sync user's drive, signed in with ``app_password``."""
    await create_connector_and_await_sync(
        client, graph, state,
        connector_type="Nextcloud",
        connector_name=f"nextcloud-own-{uuid.uuid4().hex[:8]}",
        connector_config={"auth": {
            "baseUrl": os.getenv("NEXTCLOUD_CONNECTOR_URL", "http://nextcloud-source"),
            "username": source.user,
            "password": app_password,
        }},
        expected_records=expected_records,
        scope="personal",
    )
    return state["connector_id"]


async def _full_sync(client: PipeshubClient, graph: GraphProviderProtocol, connector_id: str) -> None:
    client.resync_connector(connector_id, full_sync=True)
    await wait_for_sync_completion(client, graph, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC)


@pytest.mark.integration
@pytest.mark.nextcloud
@pytest.mark.asyncio(loop_scope="session")
class TestNextcloudChecklist:
    @pytest.mark.order(1)
    async def test_tc_activity_backlog_caught_up_in_one_sync(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-ACTIVITY-BACKLOG: Nextcloud pages its feed at 200 rows; one incremental sync reads every page."""
        connector_id = nextcloud_connector["connector_id"]
        folder = f"{nextcloud_connector['folder']}/backlog-{uuid.uuid4().hex[:6]}"
        # The folder is synced first: a folder created in the same run is saved with all it
        # holds, which would hide a feed read only in part.
        nextcloud_source.put(f"{folder}/first.txt", "The folder exists before the backlog.")
        await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, ["first.txt"])
        before = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        for i in range(BACKLOG_FILES):
            nextcloud_source.put(f"{folder}/note-{i:03d}.txt", f"Backlog note number {i}.")

        pipeshub_client.resync_connector(connector_id, full_sync=False)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC)

        after = await graph_provider.count_records(connector_id)
        assert after == before + BACKLOG_FILES, (
            f"TC-ACTIVITY-BACKLOG: one incremental sync brought {after - before} of "
            f"{BACKLOG_FILES} new files; the rest of the feed was left for a later run"
        )

    @pytest.mark.order(2)
    async def test_tc_no_fs_access_removes_nothing(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-NO-FS-ACCESS: unticking "Allow filesystem access" makes the drive look empty; nothing is removed."""
        name = f"pipeshub-it-{uuid.uuid4().hex[:6]}"
        app_password, token_id = nextcloud_source.create_app_password(name)
        instance = f"nextcloud-fs-access-{uuid.uuid4().hex[:8]}"
        state: dict[str, Any] = {"resource_name": nextcloud_source.user, "folder": f"never-created-{name}"}
        try:
            await create_connector_and_await_sync(
                pipeshub_client, graph_provider, state,
                connector_type="Nextcloud",
                connector_name=instance,
                connector_config={"auth": {
                    "baseUrl": os.getenv("NEXTCLOUD_CONNECTOR_URL", "http://nextcloud-source"),
                    "username": nextcloud_source.user,
                    "password": app_password,
                }},
                expected_records=nextcloud_connector["uploaded_count"],
                scope="personal",
            )
            connector_id = state["connector_id"]
            linked = await graph_provider.count_records(connector_id, scoped=True)
            assert linked >= nextcloud_connector["uploaded_count"]

            nextcloud_source.set_filesystem_access(token_id, name, allowed=False)
            await _full_sync(pipeshub_client, graph_provider, connector_id)

            after = await graph_provider.count_records(connector_id, scoped=True)
            assert after == linked, (
                f"TC-NO-FS-ACCESS: {linked - after} record(s) were removed when the app password lost filesystem access"
            )
            assert _notification_titled(pipeshub_client, f"{instance}: Nextcloud app password cannot read files"), (
                "TC-NO-FS-ACCESS: the user was not told the app password cannot read files"
            )

            pipeshub_client.toggle_sync(connector_id, enable=False)
            resp = pipeshub_client.request(
                "POST", f"/api/v1/connectors/{connector_id}/toggle", json={"type": "sync"},
            )
            assert resp.status_code == 400 and "Allow filesystem access" in resp.text, (
                f"TC-NO-FS-ACCESS: turning the connector on must be refused with the fix; got HTTP "
                f"{resp.status_code}: {resp.text[:300]}"
            )
        finally:
            nextcloud_source.set_filesystem_access(token_id, name, allowed=True)
            if state.get("connector_id"):
                await destructor(nextcloud_source, pipeshub_client, graph_provider, state, connector_type="Nextcloud")
            nextcloud_source.delete_app_password(token_id)

    @pytest.mark.order(3)
    async def test_tc_mount_offline_keeps_what_was_synced(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-MOUNT-OFFLINE: storage that goes offline lists as an empty folder with file id -1; its records stay."""
        connector_id = nextcloud_connector["connector_id"]
        token = uuid.uuid4().hex[:6]
        owner = (f"mount-owner-{token}", f"Owner-Pass-{token}-2026!")
        mount, folder, files = f"it-mount-{token}", f"shelf-{token}", [f"atlas-{token}.txt", f"ledger-{token}.txt"]
        _occ_or_skip(nextcloud_source, "status")
        nextcloud_source.ensure_other_user(*owner)
        mount_id = ""
        try:
            for name in files:
                nextcloud_source.put(f"{folder}/{name}", f"Kept on the mount: {name}.", auth=owner)
            mount_id = nextcloud_source.mount_other_users_files(mount, owner)
            nextcloud_source.wait_until_listed(f"{mount}/{folder}")
            # A new mount logs no activity; the full listing finds it.
            await _full_sync(pipeshub_client, graph_provider, connector_id)
            await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, [mount, folder, *files])
            linked = await graph_provider.count_records(connector_id, scoped=True)

            nextcloud_source.take_mount_offline(mount_id)
            await _full_sync(pipeshub_client, graph_provider, connector_id)

            after = await graph_provider.count_records(connector_id, scoped=True)
            assert after == linked, (
                f"TC-MOUNT-OFFLINE: {linked - after} record(s) were removed while the mount was offline"
            )
            assert await graph_provider.get_record_by_external_id(connector_id, "-1") is None, (
                "TC-MOUNT-OFFLINE: a record was saved under Nextcloud's file id -1"
            )
            instance = _instance_name(pipeshub_client, connector_id)
            assert _notification_titled(pipeshub_client, f"{instance}: some Nextcloud folders could not be read"), (
                "TC-MOUNT-OFFLINE: the user was not told a folder could not be read"
            )
        finally:
            if mount_id:
                nextcloud_source.occ("files_external:delete", "-y", mount_id)
            nextcloud_source.delete_user(owner[0])

    @pytest.mark.order(4)
    async def test_tc_failure_notified(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-FAILURE-NOTIFIED: a full sync that cannot read the drive (maintenance mode) is reported to the user."""
        connector_id = nextcloud_connector["connector_id"]
        instance = _instance_name(pipeshub_client, connector_id)
        _occ_or_skip(nextcloud_source, "maintenance:mode", "--on")
        try:
            await _full_sync(pipeshub_client, graph_provider, connector_id)
        finally:
            nextcloud_source.occ("maintenance:mode", "--off")

        assert _notification_titled(pipeshub_client, f"{instance}: Nextcloud sync failed"), (
            "TC-FAILURE-NOTIFIED: a failed sync was not reported to the user"
        )
        await _full_sync(pipeshub_client, graph_provider, connector_id)

    @pytest.mark.order(5)
    async def test_tc_created_time_kept(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CREATED-TIME: Nextcloud keeps a creation time only when the client sends one (X-OC-CTime)."""
        connector_id = nextcloud_connector["connector_id"]
        name = f"dated-{uuid.uuid4().hex[:6]}.txt"
        path = f"{nextcloud_connector['folder']}/{name}"
        nextcloud_source.put(path, "Written by a desktop client.", ctime=1_600_000_000)
        await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, [name])

        record = await graph_provider.get_record_by_name(connector_id, name)
        assert record["sourceCreatedAtTimestamp"] == 1_600_000_000_000, (
            f"TC-CREATED-TIME: created {record['sourceCreatedAtTimestamp']}, not the time the client sent"
        )

        nextcloud_source.put(path, "Edited later, without a creation time.")
        before = record["sourceLastModifiedTimestamp"]
        deadline = time.monotonic() + DEFAULT_SYNC_TIMEOUT_SEC
        while record["sourceLastModifiedTimestamp"] == before and time.monotonic() < deadline:
            pipeshub_client.resync_connector(connector_id, full_sync=False)
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC)
            record = await graph_provider.get_record_by_name(connector_id, name)
        assert record["sourceLastModifiedTimestamp"] > before, "TC-CREATED-TIME: the edit was not synced"
        assert record["sourceCreatedAtTimestamp"] == 1_600_000_000_000, "TC-CREATED-TIME: the edit moved the created time"

    @pytest.mark.order(6)
    async def test_tc_download_disabled_not_synced(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DOWNLOAD-DISABLED: Nextcloud lists a file shared with "Allow download" unticked but refuses its content (403)."""
        connector_id = nextcloud_connector["connector_id"]
        token = uuid.uuid4().hex[:6]
        owner = (f"share-owner-{token}", f"Owner-Pass-{token}-2026!")
        view_only, shared = f"view-only-{token}.txt", f"shared-{token}.txt"
        nextcloud_source.ensure_other_user(*owner)
        try:
            nextcloud_source.put(view_only, "Not to leave Nextcloud.", auth=owner)
            nextcloud_source.put(shared, "Shared normally.", auth=owner)
            nextcloud_source.share_with_sync_user(owner, view_only, download=False)
            nextcloud_source.share_with_sync_user(owner, shared)
            nextcloud_source.wait_until_listed(shared)

            await _full_sync(pipeshub_client, graph_provider, connector_id)
            await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, [shared])

            assert await graph_provider.get_record_by_name(connector_id, view_only) is None, (
                "TC-DOWNLOAD-DISABLED: a file the owner shared with download disabled was synced"
            )
        finally:
            nextcloud_source.delete_user(owner[0])

    @pytest.mark.order(7)
    async def test_tc_manual_indexing(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-MANUAL-INDEXING: "Enable Manual Indexing" saves new files as AUTO_INDEX_OFF."""
        connector_id = nextcloud_connector["connector_id"]
        name = f"manual-{uuid.uuid4().hex[:6]}.txt"
        pipeshub_client.update_connector_filters_sync_safe(connector_id, filters=_manual_indexing(True))
        try:
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC)
            nextcloud_source.put(f"{nextcloud_connector['folder']}/{name}", "Index me only when I ask.")
            await sync_until_names_visible(pipeshub_client, graph_provider, connector_id, [name])

            status = (await graph_provider.get_record_by_name(connector_id, name))["indexingStatus"]
            assert status == "AUTO_INDEX_OFF", f"TC-MANUAL-INDEXING: a new file was saved {status}"
        finally:
            pipeshub_client.update_connector_filters_sync_safe(connector_id, filters=_manual_indexing(False))
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC)

    @pytest.mark.order(8)
    async def test_tc_reenable_catches_up(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-REENABLE: turning the connector on again syncs what changed while it was off, and nothing twice."""
        connector_id = nextcloud_connector["connector_id"]
        before = await settle_record_baseline(pipeshub_client, graph_provider, connector_id)
        pipeshub_client.toggle_sync(connector_id, enable=False)
        name = f"while-off-{uuid.uuid4().hex[:6]}.txt"
        nextcloud_source.put(f"{nextcloud_connector['folder']}/{name}", "Written while the connector was off.")

        pipeshub_client.toggle_sync(connector_id, enable=True)
        await _record(graph_provider, connector_id, name)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=DEFAULT_SYNC_TIMEOUT_SEC)

        assert await graph_provider.count_records(connector_id) == before + 1

    @pytest.mark.order(9)
    async def test_tc_browse_matches_the_drive(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-BROWSE: app > drive > run folder > its files and folders, as Nextcloud lists them."""
        connector_id = nextcloud_connector["connector_id"]
        folder = nextcloud_connector["folder"]

        def children(node_type: str, node_id: str) -> list[dict[str, Any]]:
            return pipeshub_client.get_knowledge_hub_children(node_type, node_id, limit=200)["items"]

        drives = [i for i in children("app", connector_id) if i.get("nodeType") == "recordGroup"]
        assert len(drives) == 1, f"TC-BROWSE: expected the one drive under the app, got {drives}"
        top = next((i for i in children("recordGroup", drives[0]["id"]) if i.get("name") == folder), None)
        assert top is not None, f"TC-BROWSE: the run folder {folder} is not at the top of the drive"
        shown = {i["name"] for i in children(top["nodeType"], top["id"])}
        listed = {p.rstrip("/") for p in nextcloud_source.list(folder) if p.rstrip("/").count("/") == 1}
        assert shown == {p.split("/", 1)[1] for p in listed}, (
            f"TC-BROWSE: the hub shows {sorted(shown)}, Nextcloud lists {sorted(listed)}"
        )

    @pytest.mark.order(10)
    async def test_tc_registry_lists_nextcloud(self, pipeshub_client: PipeshubClient) -> None:
        """TC-REGISTRY: what the "add connector" page reads for Nextcloud."""
        resp = pipeshub_client.request("GET", "/api/v1/connectors/registry/Nextcloud/schema")
        assert resp.status_code == 200, f"TC-REGISTRY: HTTP {resp.status_code}: {resp.text[:300]}"
        text = resp.text
        for field in ("baseUrl", "username", "password", "file_extensions", "modified", "enable_manual_sync"):
            assert field in text, f"TC-REGISTRY: the schema has no {field}"

    @pytest.mark.order(11)
    async def test_tc_bad_password_removes_nothing(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-BAD-PASSWORD: the app password is revoked in Nextcloud after the first sync.

        Nextcloud slows every request from an address with many failed sign-ins (25 s each),
        and every connector reaches it from the same address, so the rejected password must
        not be sent again and again.
        """
        _occ_or_skip(nextcloud_source, "status")
        address = nextcloud_source.connector_address()
        nextcloud_source.reset_failed_logins(address)
        name = f"pipeshub-it-{uuid.uuid4().hex[:6]}"
        app_password, token_id = nextcloud_source.create_app_password(name)
        state: dict[str, Any] = {"resource_name": nextcloud_source.user, "folder": f"never-created-{name}"}
        try:
            connector_id = await _own_connector(
                pipeshub_client, graph_provider, nextcloud_source, state, app_password,
                nextcloud_connector["uploaded_count"],
            )
            linked = await graph_provider.count_records(connector_id, scoped=True)
            record = await graph_provider.get_record_by_name(connector_id, "notes.txt")
            nextcloud_source.delete_app_password(token_id)

            await _full_sync(pipeshub_client, graph_provider, connector_id)

            assert await graph_provider.count_records(connector_id, scoped=True) == linked, (
                "TC-BAD-PASSWORD: records were removed when the app password stopped working"
            )
            instance = _instance_name(pipeshub_client, connector_id)
            assert _notification_titled(pipeshub_client, f"{instance}: Nextcloud app password rejected"), (
                "TC-BAD-PASSWORD: the user was not told"
            )
            # Fetches already under way when the 401 came are counted too; nothing after them.
            failed = nextcloud_source.failed_logins(address)
            with pytest.raises(requests.HTTPError) as preview:
                pipeshub_client.stream_record(str(record.get("_key") or record.get("id")))
            assert preview.value.response.status_code == 409, (
                f"TC-BAD-PASSWORD: preview answered {preview.value.response.status_code}; a Nextcloud 401 must be "
                "a 409, never a PipesHub sign-out"
            )
            await _full_sync(pipeshub_client, graph_provider, connector_id)
            assert nextcloud_source.failed_logins(address) == failed, (
                f"TC-BAD-PASSWORD: the rejected password was sent again ({failed} failed sign-ins before a "
                f"preview and a sync, {nextcloud_source.failed_logins(address)} after)"
            )
        finally:
            if state.get("connector_id"):
                await destructor(nextcloud_source, pipeshub_client, graph_provider, state, connector_type="Nextcloud")
            nextcloud_source.delete_app_password(token_id)
            nextcloud_source.reset_failed_logins(address)

    @pytest.mark.order(12)
    async def test_tc_delete_during_sync_leaves_nothing(
        self,
        nextcloud_connector: dict[str, Any],
        nextcloud_source: NextcloudSourceHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DELETE-DURING-SYNC: a full sync is still saving when the connector is deleted."""
        name = f"pipeshub-it-{uuid.uuid4().hex[:6]}"
        app_password, token_id = nextcloud_source.create_app_password(name)
        state: dict[str, Any] = {"resource_name": nextcloud_source.user}
        try:
            connector_id = await _own_connector(
                pipeshub_client, graph_provider, nextcloud_source, state, app_password,
                nextcloud_connector["uploaded_count"],
            )
            pipeshub_client.resync_connector(connector_id, full_sync=True)
            time.sleep(2)
            pipeshub_client.delete_connector(connector_id)
            state.pop("connector_id")

            await graph_provider.assert_all_records_cleaned(connector_id, timeout=300)
            time.sleep(30)
            assert await graph_provider.count_any_nodes_with_connector_id(connector_id) == 0, (
                "TC-DELETE-DURING-SYNC: the sync went on saving after the connector was deleted"
            )
        finally:
            if state.get("connector_id"):
                await destructor(nextcloud_source, pipeshub_client, graph_provider, state, connector_type="Nextcloud")
            nextcloud_source.delete_app_password(token_id)
