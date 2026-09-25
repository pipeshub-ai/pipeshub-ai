"""OneDrive connector, driven over a fake Microsoft 365 tenant.

The connector, ``MSGraphClient``, the Graph SDK (``GraphServiceClient``, kiota
request adapter and retry middleware) and Azure's ``ClientSecretCredential`` are
real. Every HTTP request to Graph and to the Microsoft sign-in endpoint is
answered by an in-memory stub, and our databases are in-memory fakes.
"""

import logging
import sys
from typing import Any, Optional
from unittest.mock import MagicMock

import httpx
import pytest
from azure.identity.aio import ClientSecretCredential
from fastapi import HTTPException
from ms_graph_fakes import (
    GRAPH,
    TENANT,
    FakeCheckpointStore,
    FakeConfigService,
    FakeRecordsDb,
    MicrosoftCloudStub,
    RecordingNotifications,
    bearer,
    graph_error,
    graph_retry_skipped,
    json_response,
    page,
    route_microsoft_http,
)
from msgraph import GraphServiceClient
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from app.connectors.sources.microsoft.common.msgraph_client import MSGraphClient
from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.models.entities import RecordGroupType
from app.models.permission import EntityType, PermissionType

MODULE = "app.connectors.sources.microsoft.onedrive.connector"
CONNECTOR_ID = "onedrive-1"
DRIVE = "drive-ana"
DELTA_PATH = "/v1.0/users/u-ana/drive/root/delta"
GROUPS_DELTA_PATH = "/v1.0/groups/delta"


def delta_link(user: str, token: str) -> str:
    return f"{GRAPH}/users/{user}/drive/root/delta?token={token}"


def groups_link(token: str) -> str:
    return f"{GRAPH}/groups/delta?$deltatoken={token}"


def drive_item(
    item_id: str,
    name: str,
    *,
    etag: str = "v1",
    modified: str = "2024-05-01T10:00:00Z",
    created: str = "2024-01-01T00:00:00Z",
    parent: str = "root-ana",
    drive: str = DRIVE,
    folder: bool = False,
    shared: bool = False,
    quick_xor: Optional[str] = "hash-1",
    deleted: bool = False,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": item_id,
        "name": name,
        "eTag": etag,
        "cTag": f"c-{etag}",
        "createdDateTime": created,
        "lastModifiedDateTime": modified,
        "webUrl": f"https://acme-my.sharepoint.com/personal/ana/{name}",
        "size": 42,
        "parentReference": {"driveId": drive, "id": parent, "path": "/drive/root:"},
    }
    if folder:
        item["folder"] = {"childCount": 0}
    else:
        item["file"] = {"mimeType": "application/pdf", "hashes": {"quickXorHash": quick_xor}}
    if shared:
        item["shared"] = {"scope": "users"}
    if deleted:
        item["deleted"] = {"state": "deleted"}
    return item


def deleted_business_item(item_id: str, *, drive: str = DRIVE) -> dict[str, Any]:
    """What OneDrive for Business sends for a deleted file: no name or dates."""
    return {"id": item_id, "deleted": {"state": "deleted"}, "file": {}, "parentReference": {"driveId": drive, "id": "root-ana"}}


def user_grant(user_id: str, email: str, role: str = "read") -> dict[str, Any]:
    return {"id": f"p-{user_id}", "roles": [role], "grantedToV2": {"user": {"id": user_id, "displayName": email, "email": email}}}


class TokenFeed:
    """Answers one delta URL by its ``token`` / ``$deltatoken`` query value."""

    def __init__(self, param: str = "token") -> None:
        self.param = param
        self.by_token: dict[Optional[str], Any] = {}
        self.seen: list[Optional[str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        token = MicrosoftCloudStub.query(request).get(self.param)
        self.seen.append(token)
        answer = self.by_token.get(token, page([]))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        return answer if isinstance(answer, httpx.Response) else json_response(answer)


class Tenant:
    """A small Microsoft 365 tenant: users, their drives, groups and item sharing."""

    def __init__(self, cloud: MicrosoftCloudStub, db: FakeRecordsDb) -> None:
        self.cloud = cloud
        self.db = db
        self.users: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.drive_delta: dict[str, TokenFeed] = {}
        self.groups_delta = TokenFeed("$deltatoken")
        self.groups_delta.by_token[None] = page([], delta_link=groups_link("G1"))
        cloud.on("GET", "/v1.0/users", lambda _r: json_response(page(self.users)))
        cloud.on("GET", "/v1.0/groups", lambda _r: json_response(page(self.groups)))
        cloud.on("GET", GROUPS_DELTA_PATH, self.groups_delta)

    def add_user(self, user_id: str, email: str, name: str) -> TokenFeed:
        self.users.append({"id": user_id, "displayName": name, "mail": email, "userPrincipalName": email, "accountEnabled": True})
        self.db.add_active_user(email)
        drive = f"drive-{user_id.removeprefix('u-')}"
        self.cloud.on("GET", f"/v1.0/users/{user_id}/drive", {"id": drive, "webUrl": f"https://acme-my.sharepoint.com/personal/{user_id}"})
        self.cloud.on("GET", f"/v1.0/users/{user_id}", {"id": user_id, "mail": email, "userPrincipalName": email, "displayName": name})
        feed = TokenFeed()
        self.drive_delta[user_id] = feed
        self.cloud.on("GET", f"/v1.0/users/{user_id}/drive/root/delta", feed)
        return feed

    def share(self, item_id: str, grants: object, drive: str = DRIVE) -> None:
        self.cloud.on("GET", f"/v1.0/drives/{drive}/items/{item_id}/permissions", grants if not isinstance(grants, list) else page(grants))

    def download_url(self, item_id: str, drive: str = DRIVE) -> None:
        self.cloud.on("GET", f"/v1.0/drives/{drive}/items/{item_id}", {"id": item_id, "@microsoft.graph.downloadUrl": f"https://download.example/{item_id}"})

    def add_group(self, group_id: str, name: str, members: object) -> None:
        self.groups.append({"id": group_id, "displayName": name, "description": f"{name} group"})
        self.cloud.on("GET", f"/v1.0/groups/{group_id}/members", members if not isinstance(members, list) else page(members))


def member(user_id: str, email: str) -> dict[str, Any]:
    return {"@odata.type": "#microsoft.graph.user", "id": user_id, "mail": email, "displayName": email}


def nested(group_id: str) -> dict[str, Any]:
    return {"@odata.type": "#microsoft.graph.group", "id": group_id, "displayName": group_id}


@pytest.fixture
def cloud(cloud: MicrosoftCloudStub, monkeypatch: pytest.MonkeyPatch) -> MicrosoftCloudStub:
    route_microsoft_http(monkeypatch, cloud, MODULE)
    return cloud


@pytest.fixture
def tenant(cloud: MicrosoftCloudStub, db: FakeRecordsDb) -> Tenant:
    return Tenant(cloud, db)


def onedrive_config(filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "auth": {"tenantId": TENANT, "clientId": "client-1", "clientSecret": "secret-1", "hasAdminConsent": True},
        "filters": {"sync": {"values": filters or {}}},
    }


async def make_connector(db: FakeRecordsDb, checkpoints: FakeCheckpointStore, filters: Optional[dict] = None) -> OneDriveConnector:
    connector = OneDriveConnector(
        logging.getLogger("test.onedrive"), db, checkpoints, FakeConfigService(CONNECTOR_ID, onedrive_config(filters)),
        CONNECTOR_ID, "team", "creator-1",
    )
    connector._notification_service = RecordingNotifications()
    return connector


async def ready_connector(db: FakeRecordsDb, checkpoints: FakeCheckpointStore, filters: Optional[dict] = None) -> OneDriveConnector:
    connector = await make_connector(db, checkpoints, filters)
    assert await connector.init() is True
    return connector


async def notes(connector: OneDriveConnector) -> list[dict[str, Any]]:
    tasks = list(getattr(connector, "_background_tasks", ()))
    for task in tasks:
        await task
    return connector._notification_service.sent


def drive_checkpoint(checkpoints: FakeCheckpointStore, user: str = "u-ana") -> Optional[dict[str, Any]]:
    return checkpoints.values_for(f"/users/{user}")


def groups_checkpoint(checkpoints: FakeCheckpointStore) -> Optional[dict[str, Any]]:
    return checkpoints.values_for("/groups/organization/org-1")


async def shared_folder_scenario(
    cloud: MicrosoftCloudStub, tenant: Tenant, db: FakeRecordsDb, checkpoints: FakeCheckpointStore
) -> OneDriveConnector:
    """First sync stores folder d1 and file f1 (read by Ben); the next delta page shares d1."""
    feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
    feed.by_token[None] = page(
        [drive_item("d1", "Plans", folder=True), drive_item("f1", "plan.pdf", parent="d1")], delta_link=delta_link("u-ana", "D1")
    )
    feed.by_token["D1"] = page([drive_item("d1", "Plans", folder=True, shared=True, etag="v2")], delta_link=delta_link("u-ana", "D2"))
    tenant.share("f1", [user_grant("u-ben", "ben@acme.com")])
    connector = await ready_connector(db, checkpoints)
    await connector.run_sync()
    tenant.share("f1", [user_grant("u-ben", "ben@acme.com"), user_grant("u-cal", "cal@acme.com")])
    return connector


async def unshared_folder_scenario(
    cloud: MicrosoftCloudStub, tenant: Tenant, db: FakeRecordsDb, checkpoints: FakeCheckpointStore
) -> OneDriveConnector:
    """First sync stores shared folder d1 and file f1, both readable by Ben; the next delta page unshares d1."""
    feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
    feed.by_token[None] = page(
        [drive_item("d1", "Plans", folder=True, shared=True), drive_item("f1", "plan.pdf", parent="d1")],
        delta_link=delta_link("u-ana", "D1"),
    )
    feed.by_token["D1"] = page([drive_item("d1", "Plans", folder=True, etag="v2")], delta_link=delta_link("u-ana", "D2"))
    shared = [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-ben", "ben@acme.com")]
    tenant.share("d1", shared)
    tenant.share("f1", shared)
    connector = await ready_connector(db, checkpoints)
    await connector.run_sync()
    assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "f1")
    tenant.share("d1", [user_grant("u-ana", "ana@acme.com", "owner")])
    cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", page([drive_item("f1", "plan.pdf", parent="d1")]))
    return connector


async def unlistable_unshared_folder_scenario(
    cloud: MicrosoftCloudStub, tenant: Tenant, db: FakeRecordsDb, checkpoints: FakeCheckpointStore
) -> OneDriveConnector:
    """First sync stores shared folder d1 > subfolder d2 > file f2, all readable by Ben; the next
    delta page unshares d1, whose children can no longer be listed."""
    feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
    feed.by_token[None] = page(
        [
            drive_item("d1", "Plans", folder=True, shared=True),
            drive_item("d2", "Drafts", folder=True, parent="d1"),
            drive_item("f2", "draft.pdf", parent="d2"),
        ],
        delta_link=delta_link("u-ana", "D1"),
    )
    feed.by_token["D1"] = page([drive_item("d1", "Plans", folder=True, etag="v2")], delta_link=delta_link("u-ana", "D2"))
    shared = [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-ben", "ben@acme.com")]
    for item_id in ("d1", "d2", "f2"):
        tenant.share(item_id, shared)
    connector = await ready_connector(db, checkpoints)
    await connector.run_sync()
    assert db.records["d2"].mime_type == "text/directory"
    cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", graph_error(403, "accessDenied"))
    return connector


def fail_next_checkpoint_move(connector: OneDriveConnector) -> None:
    """The next write that moves the drive's delta link fails, after that page's records are saved."""
    store = connector.drive_delta_sync_point
    update = store.update_sync_point

    async def failing_once(key: str, sync_point_data: dict[str, Any]) -> dict[str, Any]:
        if "deltaLink" in sync_point_data:
            store.update_sync_point = update
            raise RuntimeError("sync point store unavailable")
        return await update(key, sync_point_data)

    store.update_sync_point = failing_once


def perms(db: FakeRecordsDb, item_id: str) -> set[tuple]:
    return {(p.entity_type, p.external_id, p.email, p.type) for p in db.record_permissions[item_id]}


class TestRealClientsAndTokens:
    async def test_setup_signs_in_with_the_client_secret_and_uses_the_real_graph_sdk(self, cloud, tenant, db, checkpoints) -> None:
        connector = await ready_connector(db, checkpoints)

        assert not isinstance(sys.modules["msgraph"], MagicMock)
        assert type(connector.credential) is ClientSecretCredential
        assert type(connector.client) is GraphServiceClient
        assert type(connector.msgraph_client) is MSGraphClient

        form = cloud.token_requests()[0]
        assert (form["grant_type"], form["client_id"]) == ("client_credentials", "client-1")
        assert form["scope"] == "https://graph.microsoft.com/.default"

        assert await connector.test_connection_and_access() is True
        issued = {f"Bearer fake-graph-token-{n}" for n in range(1, cloud.tokens_issued + 1)}
        assert {bearer(r) for r in cloud.graph_calls()} <= issued

    async def test_an_expired_token_is_replaced_before_the_next_graph_call(self, cloud, tenant, db, checkpoints) -> None:
        cloud.token_lifetime_s = 0
        connector = await ready_connector(db, checkpoints)

        await connector.test_connection_and_access()

        tokens = [bearer(r) for r in cloud.graph_calls()]
        assert len(set(tokens)) == len(tokens) == 2, "each call found the cached token expired and got a new one"

    async def test_a_rejected_secret_at_setup_is_reported_with_microsofts_reason(self, cloud, tenant, db, checkpoints) -> None:
        cloud.token_failure = json_response(
            {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."}, status=401
        )
        connector = await make_connector(db, checkpoints)

        with pytest.raises(ValueError, match="AADSTS7000215: Invalid client secret provided."):
            await connector.init()

        (note,) = await notes(connector)
        assert note["title"] == "OneDrive authentication failed"
        assert cloud.graph_calls() == []

    async def test_a_secret_that_expires_between_runs_stops_the_run_and_says_so(self, cloud, tenant, db, checkpoints) -> None:
        cloud.token_lifetime_s = 0
        connector = await ready_connector(db, checkpoints)
        cloud.token_failure = json_response({"error": "invalid_client", "error_description": "AADSTS7000222: secret expired"}, status=401)

        with pytest.raises(Exception, match="AADSTS7000222"):
            await connector.run_sync()

        assert cloud.graph_calls() == [], "nothing is read with a secret Microsoft rejected"
        (note,) = await notes(connector)
        assert "client secret may have expired" in note["message"]

    async def test_a_missing_directory_permission_is_named_in_the_error(self, cloud, tenant, db, checkpoints) -> None:
        cloud.on("GET", "/v1.0/groups", graph_error(403, "Authorization_RequestDenied", "Insufficient privileges"))
        connector = await ready_connector(db, checkpoints)

        with pytest.raises(ConnectionError, match="Group.Read.All"):
            await connector.test_connection_and_access()

        (note,) = await notes(connector)
        assert note["title"] == "OneDrive: missing API permissions"
        assert "User.Read.All" not in note["message"]


class TestDriveDeltaSync:
    async def test_first_sync_reads_every_page_saves_the_drive_and_the_delta_link(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], next_link=delta_link("u-ana", "P2"))
        feed.by_token["P2"] = page([drive_item("f2", "two.pdf")], delta_link=delta_link("u-ana", "D1"))
        for item_id in ("f1", "f2"):
            tenant.share(item_id, [user_grant("u-ana", "ana@acme.com", "owner")])
            tenant.download_url(item_id)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert sorted(db.records) == ["f1", "f2"]
        assert db.records["f1"].signed_url == "https://download.example/f1"
        assert db.records["f1"].external_record_group_id == DRIVE
        assert drive_checkpoint(checkpoints) == {
            **drive_checkpoint(checkpoints), "nextLink": None, "deltaLink": delta_link("u-ana", "D1"),
        }
        group = db.record_groups[DRIVE]
        assert group.name == "Ana's OneDrive" and group.group_type == RecordGroupType.DRIVE
        (owner,) = db.record_group_permissions[DRIVE]
        assert (owner.email, owner.type) == ("ana@acme.com", PermissionType.OWNER)
        assert cloud.unrouted == []

    async def test_the_next_sync_starts_from_the_saved_delta_link_and_adds_only_changes(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([drive_item("f3", "three.pdf")], delta_link=delta_link("u-ana", "D2"))
        for item_id in ("f1", "f3"):
            tenant.share(item_id, [user_grant("u-ana", "ana@acme.com", "owner")])
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()
        await connector.run_sync()

        assert feed.seen == [None, "D1"]
        assert sorted(db.records) == ["f1", "f3"]
        assert [r.external_record_id for batch in db.record_batches for r in batch] == ["f1", "f3"]
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")

    async def test_an_empty_page_in_the_middle_does_not_end_the_sync(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], next_link=delta_link("u-ana", "P2"))
        feed.by_token["P2"] = page([], next_link=delta_link("u-ana", "P3"))
        feed.by_token["P3"] = page([drive_item("f3", "three.pdf")], delta_link=delta_link("u-ana", "D1"))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert sorted(db.records) == ["f1", "f3"]
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")

    async def test_a_sync_with_no_changes_still_saves_the_new_delta_link(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([], delta_link=delta_link("u-ana", "D2"))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()
        await connector.run_sync()

        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")

    async def test_a_page_without_a_next_or_delta_link_keeps_the_saved_delta_link(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([])
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()

        await connector.run_sync()

        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")

    async def test_a_failed_page_keeps_the_checkpoint_on_the_last_page_that_was_saved(self, cloud, tenant, db, checkpoints, backoff_sleeps) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], next_link=delta_link("u-ana", "P2"))
        feed.by_token["P2"] = graph_error(503, "serviceNotAvailable")
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert sorted(db.records) == ["f1"]
        assert drive_checkpoint(checkpoints)["nextLink"] == delta_link("u-ana", "P2")
        assert drive_checkpoint(checkpoints).get("deltaLink") is None

        feed.by_token["P2"] = page([drive_item("f2", "two.pdf")], delta_link=delta_link("u-ana", "D1"))
        await connector.run_sync()

        assert feed.seen[-1] == "P2", "the next run picks up at the page that failed"
        assert sorted(db.records) == ["f1", "f2"]
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")

    @graph_retry_skipped
    async def test_a_throttled_page_is_retried_after_the_wait_microsoft_asks_for(self, cloud, tenant, db, checkpoints, backoff_sleeps) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = [
            graph_error(429, "activityLimitReached", headers={"Retry-After": "7"}),
            page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1")),
        ]
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert 7 in backoff_sleeps
        assert sorted(db.records) == ["f1"]
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")

    async def test_a_deleted_file_is_removed(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf"), drive_item("f2", "two.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([deleted_business_item("f1")], delta_link=delta_link("u-ana", "D2"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        stored_id = db.records["f1"].id

        await connector.run_sync()

        assert db.deleted == [stored_id]
        assert sorted(db.records) == ["f2"]

    async def test_a_deleted_file_is_removed_even_when_a_file_type_filter_is_set(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf"), drive_item("f2", "notes.txt")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([deleted_business_item("f1")], delta_link=delta_link("u-ana", "D2"))
        only_pdf = {"file_extensions": {"operator": "in", "value": ["pdf"], "type": "multiselect"}}
        connector = await ready_connector(db, checkpoints, filters=only_pdf)
        await connector.run_sync()
        assert sorted(db.records) == ["f1"], "the filter is in force"
        stored_id = db.records["f1"].id

        await connector.run_sync()

        assert db.deleted == [stored_id]
        assert db.records == {}

    async def test_a_renamed_and_moved_file_is_updated_in_place(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "draft.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page(
            [drive_item("f1", "final.pdf", etag="v2", parent="folder-archive", modified="2024-06-01T09:00:00Z")],
            delta_link=delta_link("u-ana", "D2"),
        )
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner")])
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        original = db.records["f1"]

        await connector.run_sync()

        (updated,) = db.metadata_updates
        assert updated.id == original.id
        assert (updated.record_name, updated.parent_external_record_id) == ("final.pdf", "folder-archive")
        assert updated.version == original.version + 1
        assert len(db.record_batches) == 1, "no second copy of the file was created"

    async def test_a_changed_file_body_is_sent_for_reindexing(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([drive_item("f1", "one.pdf", etag="v2", quick_xor="hash-2")], delta_link=delta_link("u-ana", "D2"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()

        await connector.run_sync()

        (changed,) = db.content_updates
        assert changed.quick_xor_hash == "hash-2"

    async def test_one_malformed_item_does_not_stop_the_rest_of_the_page(self, cloud, tenant, db, checkpoints) -> None:
        broken = drive_item("f-bad", "bad.pdf")
        del broken["createdDateTime"]
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf"), broken, drive_item("f2", "two.pdf")], delta_link=delta_link("u-ana", "D1"))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert sorted(db.records) == ["f1", "f2"]
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")

    async def test_one_users_drive_failing_does_not_stop_the_other_users(self, cloud, tenant, db, checkpoints) -> None:
        ana = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        ben = tenant.add_user("u-ben", "ben@acme.com", "Ben")
        ana.by_token[None] = graph_error(500, "generalException")
        ben.by_token[None] = page([drive_item("f-ben", "ben.pdf", drive="drive-ben")], delta_link=delta_link("u-ben", "D1"))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert sorted(db.records) == ["f-ben"]
        assert drive_checkpoint(checkpoints, "u-ana") is None
        assert drive_checkpoint(checkpoints, "u-ben")["deltaLink"] == delta_link("u-ben", "D1")

    async def test_users_without_onedrive_are_skipped_and_named_in_a_warning(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_user("u-ana", "ana@acme.com", "Ana").by_token[None] = page([], delta_link=delta_link("u-ana", "D1"))
        tenant.add_user("u-cal", "cal@acme.com", "Cal")
        cloud.on("GET", "/v1.0/users/u-cal/drive", graph_error(404, "ResourceNotFound", "User's mysite not found."))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        sent = await notes(connector)
        warning = next(n for n in sent if n["title"] == "OneDrive not provisioned for some users")
        assert "cal@acme.com" in warning["message"] and "ana@acme.com" not in warning["message"]
        assert cloud.calls("GET", "/v1.0/users/u-cal/drive/root/delta") == []


class TestSharing:
    async def test_sharing_maps_people_groups_and_links_to_access(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "plan.pdf", shared=True)], delta_link=delta_link("u-ana", "D1"))
        tenant.share("f1", {
            "value": [
                user_grant("u-ana", "ana@acme.com", "owner"),
                {"id": "p-g", "roles": ["write"], "grantedToV2": {"group": {"id": "g-eng", "displayName": "Eng", "email": "eng@acme.com"}}},
            ],
            "@odata.nextLink": f"{GRAPH}/drives/{DRIVE}/items/f1/permissions?$skiptoken=2",
        })
        second_page = page([
            {"id": "p-l1", "roles": ["read"], "link": {"scope": "anonymous", "type": "view"}},
            {"id": "p-l2", "roles": ["read"], "link": {"scope": "organization", "type": "view"}},
            {"id": "p-l3", "roles": ["read"], "link": {"scope": "users", "type": "view"},
             "grantedToIdentitiesV2": [{"user": {"id": "u-ben", "email": "ben@acme.com"}}, {"group": {"id": "g-ops"}}]},
        ])
        first_page = cloud._routes[0][2]
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/f1/permissions",
                 lambda r: json_response(second_page if "$skiptoken" in MicrosoftCloudStub.query(r) else first_page))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert perms(db, "f1") == {
            (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
            (EntityType.GROUP, "g-eng", "eng@acme.com", PermissionType.WRITE),
            (EntityType.ANYONE_WITH_LINK, "anyone_with_link", None, PermissionType.READ),
            (EntityType.ORG, "anyone_in_org", None, PermissionType.READ),
            (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ),
            (EntityType.GROUP, "g-ops", None, PermissionType.READ),
        }
        assert db.records["f1"].is_shared is True

    async def test_new_sharing_on_a_stored_file_replaces_its_access(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "plan.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([drive_item("f1", "plan.pdf", etag="v2", shared=True)], delta_link=delta_link("u-ana", "D2"))
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner")])
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-ben", "ben@acme.com", "write")])

        await connector.run_sync()

        assert perms(db, "f1") == {
            (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
            (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.WRITE),
        }

    async def test_a_failed_sharing_read_keeps_a_stored_files_access(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "plan.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([drive_item("f1", "plan-v2.pdf", etag="v2")], delta_link=delta_link("u-ana", "D2"))
        tenant.share("f1", [user_grant("u-ben", "ben@acme.com")])
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        tenant.share("f1", graph_error(403, "accessDenied"))

        await connector.run_sync()

        assert db.permission_updates == []
        assert perms(db, "f1") == {(EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ)}
        assert db.records["f1"].record_name == "plan-v2.pdf"

    async def test_a_new_file_whose_access_cannot_be_read_is_held_then_read_again_after_it_is_saved(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([drive_item("f1", "plan.pdf")], delta_link=delta_link("u-ana", "D2"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        tenant.share("f1", graph_error(503, "serviceNotAvailable"))

        for _ in range(4):
            await connector.run_sync()
            assert "f1" not in db.records
            assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")
        await connector.run_sync()

        assert "f1" in db.records
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")
        assert drive_checkpoint(checkpoints)["pendingAccessReads"] == ["f1"]

        tenant.share("f1", [user_grant("u-ben", "ben@acme.com")])
        await connector.run_sync()

        assert perms(db, "f1") == {(EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ)}
        assert drive_checkpoint(checkpoints)["pendingAccessReads"] == []

    async def test_a_new_file_saved_without_access_stays_queued_when_the_checkpoint_write_fails(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([drive_item("f1", "plan.pdf")], delta_link=delta_link("u-ana", "D2"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        tenant.share("f1", graph_error(403, "accessDenied"))
        fail_next_checkpoint_move(connector)

        await connector.run_sync()

        assert "f1" in db.records
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")
        assert drive_checkpoint(checkpoints).get("pendingAccessReads") == ["f1"]

        await connector.run_sync()

        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")
        assert drive_checkpoint(checkpoints).get("pendingAccessReads") == ["f1"]

        tenant.share("f1", [user_grant("u-ben", "ben@acme.com")])
        await connector.run_sync()

        assert perms(db, "f1") == {(EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ)}
        assert drive_checkpoint(checkpoints)["pendingAccessReads"] == []

    async def test_sharing_a_folder_updates_the_access_of_the_files_inside_it(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page(
            [drive_item("d1", "Plans", folder=True), drive_item("f1", "plan.pdf", parent="d1")], delta_link=delta_link("u-ana", "D1")
        )
        feed.by_token["D1"] = page([drive_item("d1", "Plans", folder=True, shared=True, etag="v2")], delta_link=delta_link("u-ana", "D2"))
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner")])
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", page([drive_item("f1", "plan.pdf", parent="d1")]))
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-ben", "ben@acme.com")])

        await connector.run_sync()

        assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "f1")

    async def test_a_failed_sharing_read_for_a_file_inside_a_shared_folder_keeps_its_access(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page(
            [drive_item("d1", "Plans", folder=True), drive_item("f1", "plan.pdf", parent="d1")], delta_link=delta_link("u-ana", "D1")
        )
        feed.by_token["D1"] = page([drive_item("d1", "Plans", folder=True, shared=True, etag="v2")], delta_link=delta_link("u-ana", "D2"))
        tenant.share("f1", [user_grant("u-ben", "ben@acme.com")])
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", page([drive_item("f1", "plan.pdf", parent="d1")]))
        tenant.share("f1", graph_error(503, "serviceNotAvailable"))

        await connector.run_sync()

        assert perms(db, "f1") == {(EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ)}


    async def test_a_shared_folder_whose_files_could_not_be_reread_is_tried_again_next_run(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page(
            [drive_item("d1", "Plans", folder=True), drive_item("f1", "plan.pdf", parent="d1")], delta_link=delta_link("u-ana", "D1")
        )
        feed.by_token["D1"] = page([drive_item("d1", "Plans", folder=True, shared=True, etag="v2")], delta_link=delta_link("u-ana", "D2"))
        tenant.share("f1", [user_grant("u-ben", "ben@acme.com")])
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", page([drive_item("f1", "plan.pdf", parent="d1")]))
        tenant.share("f1", graph_error(503, "serviceNotAvailable"))
        await connector.run_sync()
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1"), "the page is replayed next run"
        assert db.records["d1"].is_shared is False

        tenant.share("f1", [user_grant("u-ben", "ben@acme.com"), user_grant("u-cal", "cal@acme.com")])
        await connector.run_sync()

        assert (EntityType.USER, "u-cal", "cal@acme.com", PermissionType.READ) in perms(db, "f1")
        assert db.records["d1"].is_shared is True
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")


    async def test_a_failed_listing_of_a_shared_folders_files_is_tried_again_next_run(self, cloud, tenant, db, checkpoints) -> None:
        connector = await shared_folder_scenario(cloud, tenant, db, checkpoints)
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", graph_error(503, "serviceNotAvailable"))
        await connector.run_sync()
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")
        assert db.records["d1"].is_shared is False

        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", page([drive_item("f1", "plan.pdf", parent="d1")]))
        await connector.run_sync()

        assert (EntityType.USER, "u-cal", "cal@acme.com", PermissionType.READ) in perms(db, "f1")
        assert db.records["d1"].is_shared is True
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")

    async def test_a_file_inside_a_shared_folder_whose_access_is_forbidden_does_not_hold_the_drive(self, cloud, tenant, db, checkpoints) -> None:
        connector = await shared_folder_scenario(cloud, tenant, db, checkpoints)
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", page([drive_item("f1", "plan.pdf", parent="d1")]))
        tenant.share("f1", graph_error(403, "accessDenied"))

        await connector.run_sync()

        assert perms(db, "f1") == {(EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ)}
        assert db.records["d1"].is_shared is True
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")

    async def test_a_file_inside_a_shared_folder_that_keeps_failing_is_skipped_after_five_attempts(self, cloud, tenant, db, checkpoints) -> None:
        connector = await shared_folder_scenario(cloud, tenant, db, checkpoints)
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", page([drive_item("f1", "plan.pdf", parent="d1")]))
        tenant.share("f1", graph_error(503, "serviceNotAvailable"))

        for _ in range(4):
            await connector.run_sync()
            assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")
        await connector.run_sync()

        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")
        assert db.records["d1"].is_shared is True
        assert perms(db, "f1") == {(EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ)}


    async def test_unsharing_a_folder_removes_the_share_from_a_file_whose_access_is_forbidden(self, cloud, tenant, db, checkpoints) -> None:
        connector = await unshared_folder_scenario(cloud, tenant, db, checkpoints)
        tenant.share("f1", graph_error(403, "accessDenied"))

        await connector.run_sync()

        assert perms(db, "f1") == {(EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER)}
        assert db.records["d1"].is_shared is False
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")

    async def test_unsharing_a_folder_removes_the_share_from_a_file_that_keeps_failing(self, cloud, tenant, db, checkpoints) -> None:
        connector = await unshared_folder_scenario(cloud, tenant, db, checkpoints)
        tenant.share("f1", graph_error(503, "serviceNotAvailable"))

        for _ in range(4):
            await connector.run_sync()
            assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "f1")
        await connector.run_sync()

        assert perms(db, "f1") == {(EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER)}
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")


    async def test_unsharing_a_folder_that_cannot_be_listed_removes_the_share_below_its_subfolders(self, cloud, tenant, db, checkpoints) -> None:
        connector = await unlistable_unshared_folder_scenario(cloud, tenant, db, checkpoints)
        tenant.share("d1", [user_grant("u-ana", "ana@acme.com", "owner")])

        await connector.run_sync()

        assert db.records["d1"].is_shared is False
        for item_id in ("d2", "f2"):
            assert perms(db, item_id) == {(EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER)}, item_id

    async def test_unsharing_a_folder_whose_own_access_cannot_be_read_keeps_the_access_below_it(self, cloud, tenant, db, checkpoints) -> None:
        connector = await unlistable_unshared_folder_scenario(cloud, tenant, db, checkpoints)
        tenant.share("d1", graph_error(503, "serviceNotAvailable"))

        await connector.run_sync()

        assert perms(db, "f2") == {
            (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
            (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ),
        }
        assert db.records["d1"].is_shared is True
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")

        tenant.share("d1", [user_grant("u-ana", "ana@acme.com", "owner")])
        await connector.run_sync()

        assert db.records["d1"].is_shared is False
        assert perms(db, "f2") == {(EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER)}
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")

    async def test_items_below_a_folder_whose_access_stays_forbidden_are_read_again_later(self, cloud, tenant, db, checkpoints) -> None:
        connector = await unlistable_unshared_folder_scenario(cloud, tenant, db, checkpoints)
        for item_id in ("d1", "d2", "f2"):
            tenant.share(item_id, graph_error(403, "accessDenied"))

        await connector.run_sync()

        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")
        assert drive_checkpoint(checkpoints)["pendingAccessReads"] == ["d1", "d2", "f2"]

        await connector.run_sync()
        assert drive_checkpoint(checkpoints)["pendingAccessReads"] == ["d1", "d2", "f2"]

        owner_only = [user_grant("u-ana", "ana@acme.com", "owner")]
        for item_id in ("d1", "d2", "f2"):
            tenant.share(item_id, owner_only)
        await connector.run_sync()

        for item_id in ("d1", "d2", "f2"):
            assert perms(db, item_id) == {(EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER)}, item_id
        assert drive_checkpoint(checkpoints)["pendingAccessReads"] == []

    async def test_items_below_an_unshared_folder_stay_queued_when_the_checkpoint_write_fails(self, cloud, tenant, db, checkpoints) -> None:
        connector = await unlistable_unshared_folder_scenario(cloud, tenant, db, checkpoints)
        for item_id in ("d1", "d2", "f2"):
            tenant.share(item_id, graph_error(403, "accessDenied"))
        fail_next_checkpoint_move(connector)

        await connector.run_sync()

        assert db.records["d1"].is_shared is False
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")

        await connector.run_sync()

        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")
        assert drive_checkpoint(checkpoints).get("pendingAccessReads") == ["d1", "d2", "f2"]

        for item_id in ("d1", "d2", "f2"):
            tenant.share(item_id, [user_grant("u-ana", "ana@acme.com", "owner")])
        await connector.run_sync()

        for item_id in ("d1", "d2", "f2"):
            assert perms(db, item_id) == {(EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER)}, item_id
        assert drive_checkpoint(checkpoints)["pendingAccessReads"] == []


class TestGroups:
    async def test_first_sync_saves_every_group_with_all_member_pages_and_nested_members(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", {
            "value": [member("u-ana", "ana@acme.com"), nested("g-sre")],
            "@odata.nextLink": f"{GRAPH}/groups/g-eng/members?$skiptoken=2",
        })
        first = cloud._routes[0][2]
        cloud.on("GET", "/v1.0/groups/g-eng/members",
                 lambda r: json_response(page([member("u-ben", "ben@acme.com")]) if "$skiptoken" in MicrosoftCloudStub.query(r) else first))
        cloud.on("GET", "/v1.0/groups/g-sre/members", page([member("u-cal", "cal@acme.com"), {"@odata.type": "#microsoft.graph.device", "id": "dev-1"}]))
        connector = await ready_connector(db, checkpoints)

        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com", "cal@acme.com", "ben@acme.com"]}
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G1")

    async def test_group_changes_are_applied_from_the_delta_link(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-old", "Old", [member("u-ben", "ben@acme.com")])
        tenant.groups_delta.by_token["G1"] = page([
            {"id": "g-old", "@removed": {"reason": "deleted"}},
            {"id": "g-eng", "displayName": "Eng", "members@delta": [{"id": "u-ben"}]},
        ], delta_link=groups_link("G2"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", page([member("u-ana", "ana@acme.com"), member("u-ben", "ben@acme.com")]))
        cloud.on("GET", "/v1.0/users/u-ben", {"id": "u-ben", "mail": "ben@acme.com"})

        await connector._sync_user_groups()

        assert db.deleted_groups == ["g-old"]
        assert db.user_groups == {"g-eng": ["ana@acme.com", "ben@acme.com"]}
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")

    async def test_a_member_removed_in_the_delta_loses_group_membership(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com"), member("u-ben", "ben@acme.com")])
        tenant.groups_delta.by_token["G1"] = page(
            [{"id": "g-eng", "displayName": "Eng", "members@delta": [{"id": "u-ben", "@removed": {"reason": "deleted"}}]}],
            delta_link=groups_link("G2"),
        )
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", page([member("u-ana", "ana@acme.com")]))
        cloud.on("GET", "/v1.0/users/u-ben", {"id": "u-ben", "mail": "ben@acme.com"})

        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com"]}
        assert db.removed_members == [("g-eng", "ben@acme.com")]

    async def test_a_failed_member_read_during_delta_keeps_the_groups_stored_members(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.groups_delta.by_token["G1"] = page([{"id": "g-eng", "displayName": "Eng renamed"}], delta_link=groups_link("G2"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", graph_error(503, "serviceNotAvailable"))

        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com"]}
        assert len(db.user_group_writes) == 1

    async def test_a_failed_member_read_holds_the_group_delta_until_the_page_can_be_applied(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com"), member("u-ben", "ben@acme.com")])
        tenant.groups_delta.by_token["G1"] = page(
            [{"id": "g-eng", "displayName": "Eng", "members@delta": [{"id": "u-ben", "@removed": {"reason": "deleted"}}]}],
            delta_link=groups_link("G2"),
        )
        cloud.on("GET", "/v1.0/users/u-ben", {"id": "u-ben", "mail": "ben@acme.com"})
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", graph_error(503, "serviceNotAvailable"))

        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com"]}, "the removal needs no member read; the rest stays as stored"
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G1")

        cloud.on("GET", "/v1.0/groups/g-eng/members", page([member("u-ana", "ana@acme.com")]))
        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com"]}
        assert set(db.removed_members) == {("g-eng", "ben@acme.com")}
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")

    async def test_a_group_whose_members_are_forbidden_does_not_hold_the_group_delta(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-old", "Old", [member("u-ben", "ben@acme.com")])
        tenant.groups_delta.by_token["G1"] = page(
            [{"id": "g-eng", "displayName": "Eng"}, {"id": "g-old", "@removed": {"reason": "deleted"}}], delta_link=groups_link("G2")
        )
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", graph_error(403, "Authorization_RequestDenied", "hidden membership"))

        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com"]}
        assert db.deleted_groups == ["g-old"]
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")

    async def test_a_group_that_keeps_failing_is_skipped_after_five_attempts_keeping_its_members(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.groups_delta.by_token["G1"] = page([{"id": "g-eng", "displayName": "Eng"}], delta_link=groups_link("G2"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", graph_error(503, "serviceNotAvailable"))

        for _ in range(4):
            await connector._sync_user_groups()
            assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G1")
        await connector._sync_user_groups()

        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")
        assert db.user_groups == {"g-eng": ["ana@acme.com"]}

    async def test_a_member_removal_is_applied_when_a_failing_group_is_given_up_on(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com"), member("u-ben", "ben@acme.com")])
        tenant.groups_delta.by_token["G1"] = page(
            [{"id": "g-eng", "displayName": "Eng", "members@delta": [{"id": "u-ben", "@removed": {"reason": "deleted"}}]}],
            delta_link=groups_link("G2"),
        )
        cloud.on("GET", "/v1.0/users/u-ben", {"id": "u-ben", "mail": "ben@acme.com"})
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", graph_error(503, "serviceNotAvailable"))

        for _ in range(5):
            await connector._sync_user_groups()

        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")
        assert db.user_groups == {"g-eng": ["ana@acme.com"]}

    async def test_a_group_whose_deletion_keeps_failing_stays_queued_for_deletion_after_the_page_moves_on(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-old", "Old", [member("u-ben", "ben@acme.com")])
        tenant.groups_delta.by_token["G1"] = page([{"id": "g-old", "@removed": {"reason": "deleted"}}], delta_link=groups_link("G2"))
        tenant.groups_delta.by_token["G2"] = page([], delta_link=groups_link("G3"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        db.fail_group_delete.add("g-old")

        for _ in range(5):
            await connector._sync_user_groups()

        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")
        assert groups_checkpoint(checkpoints)["pendingGroupDeletes"] == ["g-old"]
        assert "g-old" in db.user_groups

        db.fail_group_delete.clear()
        await connector._sync_user_groups()

        assert "g-old" not in db.user_groups
        assert groups_checkpoint(checkpoints)["pendingGroupDeletes"] == []

    async def test_a_group_given_up_on_in_the_delta_is_read_again_by_a_full_sync(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.groups_delta.by_token["G1"] = page(
            [{"id": "g-eng", "displayName": "Eng", "members@delta": [{"id": "u-ben"}]}], delta_link=groups_link("G2")
        )
        tenant.groups_delta.by_token["G2"] = page([], delta_link=groups_link("G3"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", graph_error(503, "serviceNotAvailable"))
        for _ in range(5):
            await connector._sync_user_groups()
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")
        assert db.user_groups == {"g-eng": ["ana@acme.com"]}

        cloud.on("GET", "/v1.0/groups/g-eng/members", page([member("u-ana", "ana@acme.com"), member("u-ben", "ben@acme.com")]))
        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com", "ben@acme.com"]}
        assert groups_checkpoint(checkpoints)["fullSyncIncomplete"] is False

    async def test_a_group_given_up_on_while_an_earlier_full_sync_is_retried_is_still_read_again(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com"), member("u-ben", "ben@acme.com")])
        tenant.add_group("g-ops", "Ops", graph_error(503, "serviceNotAvailable"))
        tenant.groups_delta.by_token["G1"] = page(
            [{"id": "g-eng", "displayName": "Eng", "members@delta": [{"id": "u-ben", "@removed": {"reason": "deleted"}}]}],
            delta_link=groups_link("G2"),
        )
        tenant.groups_delta.by_token["G2"] = page([], delta_link=groups_link("G3"))
        cloud.on("GET", "/v1.0/users/u-ben", graph_error(503, "serviceNotAvailable"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        assert groups_checkpoint(checkpoints)["fullSyncIncomplete"] is True
        # Four earlier runs already held the G1 page, so this run gives up on g-eng.
        groups_checkpoint(checkpoints).update({"heldPage": groups_link("G1"), "heldPageAttempts": 4})
        cloud.on("GET", "/v1.0/groups/g-ops/members", page([member("u-cal", "cal@acme.com")]))

        await connector._sync_user_groups()

        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")
        assert groups_checkpoint(checkpoints)["fullSyncIncomplete"] is True
        full_reads = len(cloud.calls("GET", "/v1.0/groups"))
        await connector._sync_user_groups()
        assert len(cloud.calls("GET", "/v1.0/groups")) == full_reads + 1

    async def test_a_forbidden_group_does_not_keep_the_first_sync_incomplete(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-hidden", "Hidden", graph_error(403, "Authorization_RequestDenied", "hidden membership"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()

        await connector._sync_user_groups()

        assert len(cloud.calls("GET", "/v1.0/groups")) == 1, "the second run applies the delta instead of re-reading every group"
        assert db.user_groups == {"g-eng": ["ana@acme.com"]}

    async def test_a_first_sync_that_keeps_failing_on_one_group_stops_rereading_after_five_attempts(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-ops", "Ops", graph_error(503, "serviceNotAvailable"))
        connector = await ready_connector(db, checkpoints)

        for _ in range(6):
            await connector._sync_user_groups()

        assert len(cloud.calls("GET", "/v1.0/groups")) == 5
        assert db.user_groups == {"g-eng": ["ana@acme.com"]}

    async def test_a_failed_nested_group_read_keeps_the_parent_groups_stored_members(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com"), nested("g-sre")])
        cloud.on("GET", "/v1.0/groups/g-sre/members", page([member("u-cal", "cal@acme.com")]))
        tenant.groups_delta.by_token["G1"] = page([{"id": "g-eng", "displayName": "Eng"}], delta_link=groups_link("G2"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-sre/members", graph_error(403, "accessDenied"))

        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com", "cal@acme.com"]}

    async def test_a_group_whose_members_cannot_be_read_at_first_sync_is_not_saved_empty(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-ops", "Ops", graph_error(403, "accessDenied"))
        connector = await ready_connector(db, checkpoints)

        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com"]}

    async def test_a_group_that_could_not_be_read_at_first_sync_is_read_again_next_run(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-ops", "Ops", graph_error(503, "serviceNotAvailable"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-ops/members", page([member("u-ben", "ben@acme.com")]))

        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com"], "g-ops": ["ben@acme.com"]}
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G1")

    async def test_a_group_deleted_after_a_partial_first_sync_is_removed_while_another_still_fails(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-a", "A", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-b", "B", graph_error(503, "serviceNotAvailable"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        assert db.user_groups == {"g-a": ["ana@acme.com"]}

        tenant.groups.remove(next(g for g in tenant.groups if g["id"] == "g-a"))
        tenant.groups_delta.by_token["G1"] = page([{"id": "g-a", "@removed": {"reason": "deleted"}}], delta_link=groups_link("G2"))
        await connector._sync_user_groups()

        assert db.deleted_groups == ["g-a"]
        assert db.user_groups == {}

        cloud.on("GET", "/v1.0/groups/g-b/members", page([member("u-ben", "ben@acme.com")]))
        tenant.groups_delta.by_token["G2"] = page([], delta_link=groups_link("G3"))
        await connector._sync_user_groups()

        assert db.user_groups == {"g-b": ["ben@acme.com"]}
        assert db.deleted_groups == ["g-a"]
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G3")

    async def test_an_interrupted_group_delta_resumes_where_it_stopped(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-old", "Old", [member("u-ben", "ben@acme.com")])
        tenant.groups_delta.by_token["G1"] = page(
            [{"id": "g-eng", "displayName": "Eng"}], next_link=groups_link("G1-P2")
        )
        tenant.groups_delta.by_token["G1-P2"] = graph_error(500, "generalException")
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()

        with pytest.raises(ODataError):
            await connector._sync_user_groups()
        tenant.groups.remove(next(g for g in tenant.groups if g["id"] == "g-old"))
        tenant.groups_delta.by_token["G1-P2"] = page([{"id": "g-old", "@removed": {"reason": "deleted"}}], delta_link=groups_link("G2"))

        await connector._sync_user_groups()

        assert tenant.groups_delta.seen[-1] == "G1-P2"
        assert db.deleted_groups == ["g-old"]
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")

    async def test_a_directory_permission_error_on_groups_notifies_the_admin(self, cloud, tenant, db, checkpoints) -> None:
        cloud.on("GET", "/v1.0/groups", graph_error(403, "Authorization_RequestDenied"))
        connector = await ready_connector(db, checkpoints)

        with pytest.raises(ODataError):
            await connector._sync_user_groups()

        (note,) = await notes(connector)
        assert "Group.Read.All" in note["message"]
        assert groups_checkpoint(checkpoints) is None


class TestReindexAndDownload:
    async def test_reindex_refreshes_changed_files_and_requeues_unchanged_ones(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf"), drive_item("f2", "two.pdf")], delta_link=delta_link("u-ana", "D1"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/f1", drive_item("f1", "one-renamed.pdf", etag="v2"))
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/f2", drive_item("f2", "two.pdf"))
        stored = [db.records["f1"], db.records["f2"]]

        await connector.reindex_records(stored)

        assert db.records["f1"].record_name == "one-renamed.pdf"
        assert db.records["f1"].id == stored[0].id
        assert [r.external_record_id for r in db.reindexed] == ["f2"]

    async def test_opening_a_file_uses_a_fresh_download_link(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        tenant.download_url("f1")

        response = await connector.stream_record(db.records["f1"])

        assert response.headers["content-disposition"].endswith('one.pdf"') or "one.pdf" in response.headers["content-disposition"]
        assert cloud.calls("GET", f"/v1.0/drives/{DRIVE}/items/f1")

    async def test_opening_a_file_deleted_at_source_says_it_is_gone(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/f1", graph_error(404, "itemNotFound"))

        with pytest.raises(HTTPException) as err:
            await connector.stream_record(db.records["f1"])

        assert err.value.status_code == 404

    @graph_retry_skipped
    async def test_opening_a_file_while_throttled_is_not_reported_as_deleted(self, cloud, tenant, db, checkpoints, backoff_sleeps) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/f1", graph_error(429, "activityLimitReached", headers={"Retry-After": "2"}))

        with pytest.raises(HTTPException) as err:
            await connector.stream_record(db.records["f1"])

        assert err.value.status_code == 429
        assert backoff_sleeps.count(2) == 3, "the SDK waited as asked before each of its three retries"
