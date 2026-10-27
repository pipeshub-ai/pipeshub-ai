"""OneDrive connector, driven over a fake Microsoft 365 tenant.

The connector, ``MSGraphClient``, the Graph SDK (``GraphServiceClient``, kiota
request adapter and retry middleware) and Azure's ``ClientSecretCredential`` are
real. Every HTTP request to Graph and to the Microsoft sign-in endpoint is
answered by an in-memory stub, and our databases are in-memory fakes.
"""

import base64
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs

import httpx
import pytest
from azure.core.exceptions import ServiceRequestError
from azure.identity.aio import ClientSecretCredential
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from ms_graph_fakes import (
    GRAPH,
    TENANT,
    TOKEN_PATH,
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

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectionCheckResult,
)
from app.connectors.sources.microsoft.common.msgraph_client import MSGraphClient
from app.connectors.sources.microsoft.onedrive.connector import (
    MAX_PAGE_ATTEMPTS,
    OneDriveConnector,
    _drive_permissions_key,
    certificate_pem,
    certificate_problem,
    onedrive_config_action_items,
)
from app.models.entities import FileRecord, RecordGroupType, RecordType
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
    quick_xor: str | None = "hash-1",
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
        self.by_token: dict[str | None, Any] = {}
        self.seen: list[str | None] = []

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

    def add_user(self, user_id: str, email: str, name: str, user_type: str = "Member") -> TokenFeed:
        self.users.append({"id": user_id, "displayName": name, "mail": email, "userPrincipalName": email,
                           "accountEnabled": True, "userType": user_type})
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


def onedrive_config(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "auth": {"tenantId": TENANT, "clientId": "client-1", "clientSecret": "secret-1", "hasAdminConsent": True},
        "filters": {"sync": {"values": filters or {}}},
    }


async def make_connector(db: FakeRecordsDb, checkpoints: FakeCheckpointStore, filters: dict | None = None) -> OneDriveConnector:
    connector = OneDriveConnector(
        logging.getLogger("test.onedrive"), db, checkpoints, FakeConfigService(CONNECTOR_ID, onedrive_config(filters)),
        CONNECTOR_ID, "team", "creator-1",
    )
    connector._notification_service = RecordingNotifications()
    return connector


async def ready_connector(db: FakeRecordsDb, checkpoints: FakeCheckpointStore, filters: dict | None = None) -> OneDriveConnector:
    connector = await make_connector(db, checkpoints, filters)
    assert await connector.init() is True
    return connector


async def notes(connector: OneDriveConnector) -> list[dict[str, Any]]:
    tasks = list(getattr(connector, "_background_tasks", ()))
    for task in tasks:
        await task
    return connector._notification_service.sent


def drive_checkpoint(checkpoints: FakeCheckpointStore, user: str = "u-ana") -> dict[str, Any] | None:
    return checkpoints.values_for(f"/users/{user}")


def groups_checkpoint(checkpoints: FakeCheckpointStore) -> dict[str, Any] | None:
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
    # The file keeps the list OneDrive returned. Matching the folder is not inheritance.
    assert db.records["f1"].inherit_permissions is False
    assert perms(db, "f1") == {
        (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
        (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ),
    }
    assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "d1")
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

    async def test_a_changed_file_whose_stored_record_cannot_be_read_is_held_then_read_again(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([drive_item("f1", "one.pdf", etag="v2", quick_xor="hash-2")], delta_link=delta_link("u-ana", "D2"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        db.unreadable_file_records.add(db.records["f1"].id)

        await connector.run_sync()

        assert db.content_updates == []
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")

        db.unreadable_file_records.clear()
        await connector.run_sync()

        (changed,) = db.content_updates
        assert changed.quick_xor_hash == "hash-2"
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")

    async def test_a_file_whose_stored_record_keeps_failing_is_skipped_after_five_attempts(self, cloud, tenant, db, checkpoints) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([drive_item("f1", "one.pdf")], delta_link=delta_link("u-ana", "D1"))
        feed.by_token["D1"] = page([drive_item("f1", "one.pdf", etag="v2", quick_xor="hash-2")], delta_link=delta_link("u-ana", "D2"))
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        db.unreadable_file_records.add(db.records["f1"].id)

        for _ in range(4):
            await connector.run_sync()
            assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D1")
        await connector.run_sync()

        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "D2")
        assert db.content_updates == []
        assert db.records["f1"].external_revision_id != "v2"

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
            (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ),
            (EntityType.GROUP, "g-ops", None, PermissionType.READ),
        }, "a link grants only the people it lists: the anonymous and organization links list nobody"
        assert db.records["f1"].is_shared is True

    async def test_an_organization_link_as_graph_returns_it_grants_no_one(self, cloud, tenant, db, checkpoints) -> None:
        """Graph's organization link carries roles and scope only: organization links track no grantees."""
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        tenant.add_user("u-ben", "ben@acme.com", "Ben")
        feed.by_token[None] = page([drive_item("f1", "notes.loop", shared=True)], delta_link=delta_link("u-ana", "D1"))
        tenant.share("f1", [
            user_grant("u-ana", "ana@acme.com", "owner"),
            {"id": "p-org", "roles": ["write"], "link": {"scope": "organization", "type": "edit"}},
        ])
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert perms(db, "f1") == {(EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER)}, \
            "the link opens the file to no colleague and to no organization-wide group"
        assert "tenant-members" not in db.user_groups

    async def test_people_graph_lists_on_an_organization_link_are_granted(self, cloud, tenant, db, checkpoints) -> None:
        """Should Graph list identities on an organization link, those people, and only they, get its role."""
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        tenant.add_user("u-ben", "ben@acme.com", "Ben")
        tenant.add_user("u-gus", "gus@partner.com", "Gus", user_type="Guest")
        feed.by_token[None] = page([drive_item("f1", "notes.loop", shared=True)], delta_link=delta_link("u-ana", "D1"))
        tenant.share("f1", [
            user_grant("u-ana", "ana@acme.com", "owner"),
            {"id": "p-org", "roles": ["write"], "link": {"scope": "organization", "type": "edit"},
             "grantedToIdentitiesV2": [{"user": {"id": "u-ben", "email": "ben@acme.com"}}]},
        ])
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert perms(db, "f1") == {
            (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
            (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.WRITE),
        }, "an edit link carries write access, for the person it lists only"
        assert "tenant-members" not in db.user_groups

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
            assert perms(db, "f1") == {
                (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
                (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ),
            }
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

        # A failed folder read must not replace the file's stored grants.
        assert perms(db, "f2") == {
            (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
            (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ),
        }
        assert db.records["f2"].inherit_permissions is False
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
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2"), "the saved member list already applied the removal"
        assert not groups_checkpoint(checkpoints).get("fullSyncIncomplete")

    async def test_a_failed_member_removal_for_a_group_whose_members_are_forbidden_holds_the_group_delta(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com"), member("u-ben", "ben@acme.com")])
        tenant.groups_delta.by_token["G1"] = page(
            [{"id": "g-eng", "displayName": "Eng", "members@delta": [{"id": "u-ben", "@removed": {"reason": "deleted"}}]}],
            delta_link=groups_link("G2"),
        )
        cloud.on("GET", "/v1.0/users/u-ben", {"id": "u-ben", "mail": "ben@acme.com"})
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", graph_error(403, "Authorization_RequestDenied", "hidden membership"))
        db.fail_member_removal.add(("g-eng", "ben@acme.com"))

        await connector._sync_user_groups()

        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G1")
        assert db.user_groups == {"g-eng": ["ana@acme.com", "ben@acme.com"]}

        db.fail_member_removal.clear()
        await connector._sync_user_groups()

        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")
        assert db.user_groups == {"g-eng": ["ana@acme.com"]}

    async def test_a_member_removal_the_database_refuses_is_tried_again_while_the_rest_of_the_page_is_applied(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com"), member("u-ben", "ben@acme.com")])
        tenant.add_group("g-old", "Old", [member("u-cal", "cal@acme.com")])
        tenant.groups_delta.by_token["G1"] = page([
            {"id": "g-eng", "displayName": "Eng", "members@delta": [{"id": "u-ben", "@removed": {"reason": "deleted"}}]},
            {"id": "g-old", "@removed": {"reason": "deleted"}},
        ], delta_link=groups_link("G2"))
        cloud.on("GET", "/v1.0/users/u-ben", {"id": "u-ben", "mail": "ben@acme.com"})
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", graph_error(403, "Authorization_RequestDenied", "hidden membership"))
        db.fail_member_removal.add(("g-eng", "ben@acme.com"))

        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com", "ben@acme.com"]}, "Ben is still a member, and the group after his was deleted"
        assert db.deleted_groups == ["g-old"]
        held = groups_checkpoint(checkpoints)
        assert (held["deltaLink"], held["heldPage"], held["heldPageAttempts"]) == (groups_link("G1"), groups_link("G1"), 1)

        db.fail_member_removal.clear()
        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com"]}
        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")

    async def test_a_member_removal_the_database_keeps_refusing_leaves_a_full_group_sync_owed(self, cloud, tenant, db, checkpoints) -> None:
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com"), member("u-ben", "ben@acme.com")])
        tenant.groups_delta.by_token["G1"] = page(
            [{"id": "g-eng", "displayName": "Eng", "members@delta": [{"id": "u-ben", "@removed": {"reason": "deleted"}}]}],
            delta_link=groups_link("G2"),
        )
        tenant.groups_delta.by_token["G2"] = page([], delta_link=groups_link("G3"))
        cloud.on("GET", "/v1.0/users/u-ben", {"id": "u-ben", "mail": "ben@acme.com"})
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        cloud.on("GET", "/v1.0/groups/g-eng/members", graph_error(403, "Authorization_RequestDenied", "hidden membership"))
        db.fail_member_removal.add(("g-eng", "ben@acme.com"))

        for _ in range(4):
            await connector._sync_user_groups()
            assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G1")
        await connector._sync_user_groups()

        assert groups_checkpoint(checkpoints)["deltaLink"] == groups_link("G2")
        assert groups_checkpoint(checkpoints)["fullSyncIncomplete"] is True
        assert db.user_groups == {"g-eng": ["ana@acme.com", "ben@acme.com"]}

        # The full sync that is owed reads the members again, which takes Ben out.
        cloud.on("GET", "/v1.0/groups/g-eng/members", page([member("u-ana", "ana@acme.com")]))
        await connector._sync_user_groups()

        assert db.user_groups == {"g-eng": ["ana@acme.com"]}
        assert groups_checkpoint(checkpoints)["fullSyncIncomplete"] is False

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
        tenant.add_group("g-eng", "Eng", [member("u-ana", "ana@acme.com")])
        tenant.add_group("g-ops", "Ops", graph_error(503, "serviceNotAvailable"))
        # g-new is only in the delta, so the full sync never reads it.
        tenant.groups_delta.by_token["G1"] = page([{"id": "g-new", "displayName": "New"}], delta_link=groups_link("G2"))
        tenant.groups_delta.by_token["G2"] = page([], delta_link=groups_link("G3"))
        cloud.on("GET", "/v1.0/groups/g-new/members", graph_error(503, "serviceNotAvailable"))
        connector = await ready_connector(db, checkpoints)
        await connector._sync_user_groups()
        assert groups_checkpoint(checkpoints)["fullSyncIncomplete"] is True
        # Four earlier runs already held the G1 page, so this run gives up on g-new.
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


@pytest.fixture(autouse=True)
def fresh_notification_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """``notify`` backs off per message across the process; each test starts without that history."""
    monkeypatch.setattr(BaseConnector, "_notification_cache", {})


def self_signed_certificate(*, valid_days: int = 365, start_days_ago: int = 1) -> tuple[str, str]:
    """A certificate and its PKCS#8 private key, as an admin would upload them."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pipeshub-onedrive-test")])
    start = datetime.now(timezone.utc) - timedelta(days=start_days_ago)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(start)
        .not_valid_after(start + timedelta(days=valid_days)).sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM).decode().strip(),
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode().strip(),
    )


CERTIFICATE, PRIVATE_KEY = self_signed_certificate()
SCOPE_MODULE = "app.connectors.sources.microsoft.common.sharepoint_permission_scope"
LIST_ID = "6f1c2a8e-0b3d-4c5e-9f7a-1b2c3d4e5f60"
PERSONAL_SITE = "https://acme-my.sharepoint.com/personal/ana_acme_com"
LIST_REST = f"/personal/ana_acme_com/_api/web/lists(guid'{LIST_ID}')"
ROOT_UNIQUE_ID = "00000000-0000-0000-0000-00000000000a"
FOLDER_UNIQUE_ID = "11111111-1111-1111-1111-111111111111"
INHERITING_UNIQUE_ID = "22222222-2222-2222-2222-222222222222"
SHARED_UNIQUE_ID = "33333333-3333-3333-3333-333333333333"


def guid_etag(unique_id: str, version: int = 1) -> str:
    """A drive item eTag, which carries the list item's UniqueId: ``"{GUID},n"``."""
    return f'"{{{unique_id.upper()}}},{version}"'


def root_item() -> dict[str, Any]:
    item = drive_item("root-ana", "root", folder=True, etag=guid_etag(ROOT_UNIQUE_ID))
    item["root"] = {}
    item["parentReference"] = {"driveId": DRIVE}
    return item


@pytest.fixture
def sharepoint_rest(cloud: MicrosoftCloudStub, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """SharePoint REST and certificate sign-in, both answered by the stub; returns each certificate credential's arguments."""
    real_async_client = httpx.AsyncClient

    def _async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(cloud)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(f"{SCOPE_MODULE}.httpx", SimpleNamespace(**{**vars(httpx), "AsyncClient": _async_client}))

    stubbed_secret_credential = sys.modules[MODULE].ClientSecretCredential
    certificate_sign_ins: list[dict[str, object]] = []

    def _certificate_credential(**kwargs: object) -> ClientSecretCredential:
        certificate_sign_ins.append(kwargs)
        return stubbed_secret_credential(tenant_id=kwargs["tenant_id"], client_id=kwargs["client_id"], client_secret="cert")

    monkeypatch.setattr(f"{MODULE}.CertificateCredential", _certificate_credential)
    return certificate_sign_ins


def serve_unique_items(
    cloud: MicrosoftCloudStub,
    *unique_ids: str,
    inheriting: tuple[str, ...] = (),
    unlisted: tuple[str, ...] = (),
    item_count: int | None = None,
) -> None:
    """The drive's list on SharePoint REST: the given items broke inheritance, ``inheriting`` didn't.

    Answered the three ways the connector asks: the list itself (its size), every
    item at once, and one item by its UniqueId; an item in neither is unknown.
    ``unlisted`` items are left out of the full read (added after it, say) but
    still answer on their own.
    """
    flags = dict.fromkeys(unique_ids, True) | dict.fromkeys(inheriting, False)
    cloud.on("GET", f"/v1.0/drives/{DRIVE}", {"id": DRIVE, "sharePointIds": {"listId": LIST_ID, "siteUrl": PERSONAL_SITE}})
    cloud.on("GET", LIST_REST, {"d": {
        "Id": LIST_ID, "ItemCount": len(flags) if item_count is None else item_count, "HasUniqueRoleAssignments": False,
    }})
    cloud.on("GET", f"{LIST_REST}/items", {"d": {"results": [
        {"Id": n, "UniqueId": unique_id, "HasUniqueRoleAssignments": flag}
        for n, (unique_id, flag) in enumerate(flags.items(), 1) if unique_id not in unlisted
    ]}})
    for n, (unique_id, flag) in enumerate(flags.items(), 1):
        cloud.on("GET", item_lookup_path(unique_id), {"d": {"Id": n, "HasUniqueRoleAssignments": flag}})


def item_lookup_path(unique_id: str) -> str:
    return f"{LIST_REST}/GetItemByUniqueId(guid'{unique_id}')"


def sharepoint_calls(cloud: MicrosoftCloudStub) -> list[str]:
    """SharePoint REST requests, by kind: "list", "items" or "item <UniqueId>"."""
    kinds = []
    for request in cloud.requests:
        if request.url.host != "acme-my.sharepoint.com":
            continue
        path = MicrosoftCloudStub.path_of(request)
        lookup = re.search(r"GetItemByUniqueId\(guid'([^']+)'\)", path)
        kinds.append(f"item {lookup.group(1)}" if lookup else "items" if path.endswith("/items") else "list")
    return kinds


def certificate_config(**auth: str | None) -> dict[str, Any]:
    """The connector config with the certificate uploaded; an ``auth`` value of None removes that field."""
    config = onedrive_config()
    config["auth"].update({"certificate": CERTIFICATE, "privateKey": PRIVATE_KEY, **auth})
    config["auth"] = {k: v for k, v in config["auth"].items() if v is not None}
    return config


async def certificate_connector(db: FakeRecordsDb, checkpoints: FakeCheckpointStore, **auth: str | None) -> OneDriveConnector:
    connector = OneDriveConnector(
        logging.getLogger("test.onedrive"), db, checkpoints, FakeConfigService(CONNECTOR_ID, certificate_config(**auth)),
        CONNECTOR_ID, "team", "creator-1",
    )
    connector._notification_service = RecordingNotifications()
    assert await connector.init() is True
    return connector


def permission_state(checkpoints: FakeCheckpointStore) -> dict[str, Any]:
    return checkpoints.values_for(f"drivePermissions/drives/{DRIVE}") or {}


def changed(item_id: str, name: str, unique_id: str, version: int, **kwargs: object) -> dict[str, Any]:
    """A drive item as a later delta page reports it once it changed."""
    return drive_item(item_id, name, etag=guid_etag(unique_id, version), modified=f"2024-06-0{version}T10:00:00Z", **kwargs)


def inheritance_drive(tenant: Tenant) -> TokenFeed:
    """Root > folder d1 (shared with Ben) > file f1 (inherits) and file f2 (shared with Cal)."""
    feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
    feed.by_token[None] = page([
        root_item(),
        drive_item("d1", "Plans", folder=True, shared=True, etag=guid_etag(FOLDER_UNIQUE_ID)),
        drive_item("f1", "plan.pdf", parent="d1", etag=guid_etag(INHERITING_UNIQUE_ID)),
        drive_item("f2", "budget.pdf", parent="d1", shared=True, etag=guid_etag(SHARED_UNIQUE_ID)),
    ], delta_link=delta_link("u-ana", "D1"))
    owner = user_grant("u-ana", "ana@acme.com", "owner")
    tenant.share("root-ana", [owner])
    tenant.share("d1", [owner, user_grant("u-ben", "ben@acme.com")])
    tenant.share("f1", [owner, user_grant("u-ben", "ben@acme.com")])
    tenant.share("f2", [owner, user_grant("u-cal", "cal@acme.com")])
    return feed


class TestInheritedAccess:
    """With a client certificate, SharePoint's unique-permission flag decides which items inherit."""

    async def test_an_item_that_inherits_takes_its_folders_access_and_a_unique_one_keeps_its_own(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert db.records["f1"].inherit_permissions is True
        assert db.record_permissions["f1"] == [], "Graph lists the folder's grants for it; they are not its own"
        assert not cloud.calls("GET", f"/v1.0/drives/{DRIVE}/items/f1/permissions")
        assert db.records["f2"].inherit_permissions is False
        assert perms(db, "f2") == {
            (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
            (EntityType.USER, "u-cal", "cal@acme.com", PermissionType.READ),
        }
        assert db.records["d1"].inherit_permissions is False
        assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "d1")
        assert db.records["root-ana"].inherit_permissions is False, "the root takes the drive's access from Graph"
        assert perms(db, "root-ana") == {(EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER)}

    async def test_sharepoint_is_signed_in_to_with_the_certificate_for_the_personal_site(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert sharepoint_rest and all(
            kwargs["certificate_data"] == f"{PRIVATE_KEY}\n{CERTIFICATE}\n".encode() for kwargs in sharepoint_rest
        )
        scopes = {form["scope"] for form in cloud.token_requests()}
        assert "https://acme-my.sharepoint.com/.default" in scopes
        assert sharepoint_calls(cloud) == [
            "list", f"item {FOLDER_UNIQUE_ID}", f"item {INHERITING_UNIQUE_ID}", "list", "items",
        ], "two lookups cost as much as reading the 3-item list, so the third item is answered by one full read"

    async def test_unreadable_unique_permissions_leave_every_item_its_own_access(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", LIST_REST, json_response({"error": "Unsupported app only token."}, status=401))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert db.records["f1"].inherit_permissions is False
        assert perms(db, "f1") == {
            (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
            (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ),
        }
        rest_calls = [r for r in cloud.requests if r.url.host == "acme-my.sharepoint.com"]
        assert len(rest_calls) == 1, "a failed read is not retried for every item of the drive"

    async def test_without_a_certificate_sharepoint_is_never_called(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert connector.scope_reader is None
        assert not [r for r in cloud.requests if r.url.host == "acme-my.sharepoint.com"]
        assert db.records["f1"].inherit_permissions is False
        assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "f1")

    async def test_a_file_that_breaks_inheritance_later_gets_its_own_access(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        feed = inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        feed.by_token["D1"] = page(
            [drive_item("f1", "plan.pdf", parent="d1", shared=True, etag=guid_etag(INHERITING_UNIQUE_ID, 2))],
            delta_link=delta_link("u-ana", "D2"),
        )
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, INHERITING_UNIQUE_ID)
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-dee", "dee@acme.com")])

        await connector.run_sync()

        assert db.records["f1"].inherit_permissions is False
        assert perms(db, "f1") == {
            (EntityType.USER, "u-ana", "ana@acme.com", PermissionType.OWNER),
            (EntityType.USER, "u-dee", "dee@acme.com", PermissionType.READ),
        }, "the scope is read again each run, so the new unique permissions are seen"

    async def test_sharing_a_folder_moves_its_stored_inheriting_children_onto_inheritance(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        """Children stored before the certificate was added carry a copy of the folder's grants."""
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page(
            [drive_item("d1", "Plans", folder=True, etag=guid_etag(FOLDER_UNIQUE_ID)),
             drive_item("f1", "plan.pdf", parent="d1", etag=guid_etag(INHERITING_UNIQUE_ID))],
            delta_link=delta_link("u-ana", "D1"),
        )
        tenant.share("d1", [user_grant("u-ana", "ana@acme.com", "owner")])
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner")])
        await (await ready_connector(db, checkpoints)).run_sync()
        assert db.records["f1"].inherit_permissions is False
        feed.by_token["D1"] = page(
            [drive_item("d1", "Plans", folder=True, shared=True, etag=guid_etag(FOLDER_UNIQUE_ID, 2))],
            delta_link=delta_link("u-ana", "D2"),
        )
        tenant.share("d1", [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-ben", "ben@acme.com")])
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children",
                 page([drive_item("f1", "plan.pdf", parent="d1", etag=guid_etag(INHERITING_UNIQUE_ID))]))
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        updated = {record.external_record_id: (record, grants) for record, grants in db.permission_updates}
        assert updated["f1"][0].inherit_permissions is True
        assert updated["f1"][1] == []
        assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "d1")


class TestCertificatePem:
    def test_the_private_key_comes_first_then_the_certificate(self) -> None:
        assert certificate_pem({"certificate": CERTIFICATE, "privateKey": PRIVATE_KEY}) == (
            f"{PRIVATE_KEY}\n{CERTIFICATE}\n".encode()
        )

    def test_base64_uploads_are_decoded(self) -> None:
        encoded = {k: base64.b64encode(v.encode()).decode() for k, v in
                   {"certificate": CERTIFICATE, "privateKey": PRIVATE_KEY}.items()}
        assert certificate_pem(encoded) == f"{PRIVATE_KEY}\n{CERTIFICATE}\n".encode()

    @pytest.mark.parametrize("auth", [
        {"certificate": CERTIFICATE},
        {"privateKey": PRIVATE_KEY},
        {"certificate": CERTIFICATE, "privateKey": "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"},
        {"certificate": CERTIFICATE, "privateKey": "-----BEGIN ENCRYPTED PRIVATE KEY-----\nx\n-----END ENCRYPTED PRIVATE KEY-----"},
        {"certificate": "not a certificate", "privateKey": PRIVATE_KEY},
    ])
    def test_anything_but_a_pkcs8_key_and_a_certificate_is_refused(self, auth: dict[str, str]) -> None:
        assert certificate_pem(auth) is None

    def test_crlf_line_endings_and_surrounding_space_are_accepted(self) -> None:
        crlf = {"certificate": f"  {CERTIFICATE}\n".replace("\n", "\r\n"), "privateKey": PRIVATE_KEY.replace("\n", "\r\n")}
        assert certificate_pem(crlf) == f"{PRIVATE_KEY}\n{CERTIFICATE}\n".encode()


class TestCertificateProblems:
    """What an admin is told about an upload that can't be used, before any sign-in."""

    def test_a_usable_pair_has_no_problem(self) -> None:
        assert certificate_problem({"certificate": CERTIFICATE, "privateKey": PRIVATE_KEY}) is None

    def test_a_key_of_another_certificate_is_named(self) -> None:
        _, other_key = self_signed_certificate()
        assert "does not belong to the client certificate" in certificate_problem(
            {"certificate": CERTIFICATE, "privateKey": other_key}
        )

    def test_an_expired_certificate_is_named_with_its_date(self) -> None:
        expired, key = self_signed_certificate(valid_days=1, start_days_ago=10)
        assert "expired on" in certificate_problem({"certificate": expired, "privateKey": key})

    def test_a_certificate_not_yet_valid_is_named(self) -> None:
        future, key = self_signed_certificate(start_days_ago=-5)
        assert "not valid until" in certificate_problem({"certificate": future, "privateKey": key})

    def test_an_rsa_key_comes_with_the_command_that_converts_it(self) -> None:
        problem = certificate_problem({"certificate": CERTIFICATE, "privateKey": "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"})
        assert "PKCS#1" in problem and "openssl pkcs8 -topk8 -nocrypt" in problem

    def test_an_encrypted_key_is_named(self) -> None:
        problem = certificate_problem({"certificate": CERTIFICATE, "privateKey": "-----BEGIN ENCRYPTED PRIVATE KEY-----\nx\n-----END ENCRYPTED PRIVATE KEY-----"})
        assert "encrypted" in problem

    def test_a_missing_certificate_is_named(self) -> None:
        assert "client certificate is missing" in certificate_problem({"privateKey": PRIVATE_KEY})


def failing_certificate_sign_in(cloud: MicrosoftCloudStub, code: str = "AADSTS700027") -> None:
    """Sign-in with the certificate is refused; with the secret it still works.

    The ``sharepoint_rest`` fixture stands the certificate credential in with the secret "cert".
    """
    def sign_in(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("client_secret") == "cert":
            return json_response({"error": "invalid_client", "error_description": f"{code}: The certificate is not registered on application."}, status=401)
        return cloud._issue_token(request)

    cloud.on("POST", TOKEN_PATH, sign_in)


def notification_titles(sent: list[dict[str, Any]]) -> list[str]:
    return [n.get("title") for n in sent]


INHERITANCE_WARNING = "OneDrive can't read inherited permissions"


class TestCertificateSignIn:
    """The secret stays Graph's credential where there is one; the certificate alone is enough too."""

    async def test_with_a_secret_graph_keeps_signing_in_with_it(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        await certificate_connector(db, checkpoints)

        assert not sharepoint_rest, "the certificate is only for SharePoint while a secret is configured"
        assert [form.get("client_secret") for form in cloud.token_requests()] == ["secret-1"]

    async def test_a_certificate_alone_signs_in_to_graph_and_sharepoint(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints, clientSecret=None)

        await connector.run_sync()

        assert sharepoint_rest, "Graph signed in with the certificate"
        assert {form.get("client_secret") for form in cloud.token_requests()} == {"cert"}
        assert db.inherit_edges["f1"] is True

    async def test_neither_a_secret_nor_a_certificate_stops_init(self, db, checkpoints, sharepoint_rest) -> None:
        with pytest.raises(ValueError, match="Configure a client secret, or a client certificate"):
            await certificate_connector(db, checkpoints, clientSecret=None, certificate=None, privateKey=None)

    async def test_an_unusable_upload_without_a_secret_stops_init_with_the_reason(self, db, checkpoints, sharepoint_rest) -> None:
        with pytest.raises(ValueError, match="private key is missing"):
            await certificate_connector(db, checkpoints, clientSecret=None, privateKey=None)

    @pytest.mark.parametrize("upload", [
        pytest.param({"privateKey": None}, id="only-the-certificate"),
        pytest.param({"certificate": None}, id="only-the-key"),
        pytest.param({"privateKey": self_signed_certificate()[1]}, id="key-of-another-certificate"),
        pytest.param({"certificate": "not a certificate"}, id="not-pem"),
        pytest.param({"privateKey": "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"}, id="rsa-key"),
        pytest.param(dict(zip(("certificate", "privateKey"), self_signed_certificate(valid_days=1, start_days_ago=10))), id="expired"),
    ])
    async def test_an_unusable_upload_beside_a_secret_leaves_the_sync_as_it_was(
        self, cloud, tenant, db, checkpoints, sharepoint_rest, upload
    ) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints, **upload)

        await connector.run_sync()

        assert connector.scope_reader is None
        assert not sharepoint_calls(cloud)
        assert db.inherit_edges["f1"] is False
        assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "f1")
        assert permission_state(checkpoints) == {}

    async def test_re_initialising_a_certificate_only_connector_signs_in_with_the_certificate_again(
        self, cloud, db, checkpoints, sharepoint_rest
    ) -> None:
        connector = await certificate_connector(db, checkpoints, clientSecret=None)
        sign_ins = len(sharepoint_rest)

        async def closed_transport(*_: object, **__: object) -> None:
            raise RuntimeError("HTTP transport has already been closed")

        connector.credential.get_token = closed_transport
        await connector._reinitialize_credential_if_needed()

        assert len(sharepoint_rest) == sign_ins + 1


class TestConnectionCheck:
    """The setup form's check: it blocks a save only on an answer that settles it."""

    @staticmethod
    def auth(**values: str | None) -> dict[str, Any]:
        auth = {"tenantId": TENANT, "clientId": "client-1", "clientSecret": "secret-1", **values}
        return {k: v for k, v in auth.items() if v is not None}

    async def check(self, auth: dict[str, Any]) -> ConnectionCheckResult:
        return await OneDriveConnector.check_connection(auth, logging.getLogger("test.onedrive.check"))

    async def test_a_working_secret_passes_and_recommends_a_certificate(self, cloud) -> None:
        result = await self.check(self.auth())

        assert result.success is True
        assert "upload a client certificate" in result.message and "Sites.Read.All" in result.message
        assert not [r for r in cloud.requests if r.url.host.endswith(".sharepoint.com")]

    async def test_a_rejected_secret_is_named(self, cloud) -> None:
        cloud.token_failure = json_response({"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."}, status=401)

        result = await self.check(self.auth())

        assert result.success is False
        assert result.message.startswith("Microsoft Entra ID rejected the client secret: AADSTS7000215")

    async def test_an_unknown_tenant_names_the_tenant_id_not_the_secret(self, cloud) -> None:
        cloud.token_failure = json_response({"error": "invalid_request", "error_description": "AADSTS90002: Tenant not found."}, status=400)

        result = await self.check(self.auth())

        assert result.success is False
        assert "Directory (Tenant) ID" in result.message

    async def test_a_certificate_not_registered_on_the_app_is_named(self, cloud, sharepoint_rest) -> None:
        failing_certificate_sign_in(cloud)

        result = await self.check(self.auth(certificate=CERTIFICATE, privateKey=PRIVATE_KEY))

        assert result.success is False
        assert "rejected the client certificate: AADSTS700027" in result.message

    async def test_a_certificate_alone_passes(self, cloud, sharepoint_rest) -> None:
        result = await self.check(self.auth(clientSecret=None, certificate=CERTIFICATE, privateKey=PRIVATE_KEY))

        assert result.success is True
        assert {form.get("client_secret") for form in cloud.token_requests()} == {"cert"}

    async def test_an_unusable_upload_fails_before_any_sign_in(self, cloud, sharepoint_rest) -> None:
        result = await self.check(self.auth(certificate=CERTIFICATE, privateKey=self_signed_certificate()[1]))

        assert result.success is False
        assert "does not belong to the client certificate" in result.message
        assert "remove the certificate and key" in result.message
        assert not cloud.token_requests()

    async def test_microsoft_out_of_reach_lets_the_save_go_ahead(self, cloud, monkeypatch) -> None:
        class Unreachable:
            def __init__(self, **_: object) -> None:
                pass

            async def __aenter__(self) -> "Unreachable":
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def get_token(self, *_: object) -> None:
                raise ServiceRequestError("connection refused")

        monkeypatch.setattr(f"{MODULE}.ClientSecretCredential", Unreachable)

        result = await self.check(self.auth())

        assert result.success is True, "before this check existed the save went ahead; a network error can't settle anything"


class TestSharePointRefusals:
    """SharePoint refusing the app: the run carries on with each item's own access list, and says why once."""

    async def test_a_refused_certificate_sign_in_stops_sharepoint_reads_for_the_run_and_warns_once(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        inheritance_drive(tenant)
        ben = tenant.add_user("u-ben", "ben@acme.com", "Ben")
        ben.by_token[None] = page([drive_item("b1", "notes.pdf", parent="root-ben", drive="drive-ben", etag=guid_etag(SHARED_UNIQUE_ID))],
                                  delta_link=delta_link("u-ben", "B1"))
        tenant.share("b1", [user_grant("u-ben", "ben@acme.com", "owner")], drive="drive-ben")
        cloud.on("GET", "/v1.0/drives/drive-ben", {"id": "drive-ben", "sharePointIds": {
            "listId": LIST_ID, "siteUrl": "https://acme-my.sharepoint.com/personal/ben_acme_com"}})
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        failing_certificate_sign_in(cloud)
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert not sharepoint_calls(cloud)
        assert len([f for f in cloud.token_requests() if f.get("client_secret") == "cert"]) == 1, (
            "not tried again for every item, nor for Ben's drive"
        )
        assert "b1" in db.records
        assert db.inherit_edges["f1"] is False
        assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "f1")
        assert notification_titles(await notes(connector)).count(INHERITANCE_WARNING) == 1

    async def test_a_401_from_sharepoint_warns_about_the_certificate(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", LIST_REST, json_response({"error": {"message": {"value": "Unsupported app only token."}}}, status=401))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        sent = [n for n in await notes(connector) if n.get("title") == INHERITANCE_WARNING]
        assert len(sent) == 1 and "certificate" in sent[0]["message"]

    async def test_a_403_asks_for_the_sharepoint_permission(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", LIST_REST, json_response({"error": {"message": {"value": "Access denied."}}}, status=403))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        sent = [n for n in await notes(connector) if n.get("title") == INHERITANCE_WARNING]
        assert len(sent) == 1 and "SharePoint API (not Microsoft Graph)" in sent[0]["message"]
        assert db.inherit_edges["f1"] is False

    async def test_a_404_on_one_drive_sends_no_notification(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", LIST_REST, json_response({"error": {"message": {"value": "List does not exist."}}}, status=404))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert INHERITANCE_WARNING not in notification_titles(await notes(connector))
        assert db.inherit_edges["f1"] is False


def resync_links_made_without_sharing_changes(cloud: MicrosoftCloudStub, feed: TokenFeed) -> list[str]:
    """Graph's answer to a link made without the sharing-changes preference asked with it: 410.

    Links whose token starts with D were made without it. Returns the links asked wrongly.
    """
    wrong: list[str] = []

    def delta(request: httpx.Request) -> httpx.Response:
        token = MicrosoftCloudStub.query(request).get("token")
        if request.headers.get("Prefer") and token and token.startswith("D"):
            wrong.append(token)
            return graph_error(410, "resyncRequired", "Resync required.")
        return feed(request)

    cloud.on("GET", DELTA_PATH, delta)
    return wrong


def reordered_drive(tenant: Tenant) -> TokenFeed:
    """``inheritance_drive`` with f1 last: two lookups (d1, f2) reach the cost of a full read, so f1 is answered by it."""
    feed = inheritance_drive(tenant)
    root, d1, f1, f2 = feed.by_token[None]["value"]
    feed.by_token[None] = page([root, d1, f2, f1], delta_link=delta_link("u-ana", "D1"))
    return feed


class TestLookups:
    """How SharePoint is asked: one item at a time for a few, the whole list once that costs less."""

    async def test_one_change_in_a_large_drive_is_looked_up_on_its_own(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,), item_count=50_000)
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert "items" not in sharepoint_calls(cloud), "three lookups cost far less than 10 pages of 5000 items"
        assert db.inherit_edges["f1"] is True

    async def test_a_drive_without_changes_asks_sharepoint_nothing(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed = inheritance_drive(tenant)
        feed.by_token["D1"] = page([], delta_link=delta_link("u-ana", "D2"))
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        before = len(sharepoint_calls(cloud))

        await connector.run_sync()

        assert len(sharepoint_calls(cloud)) == before

    async def test_an_item_the_full_read_did_not_see_is_asked_on_its_own_not_taken_to_inherit(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        reordered_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,), unlisted=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", item_lookup_path(INHERITING_UNIQUE_ID), {"d": {"Id": 9, "HasUniqueRoleAssignments": True}})
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-dee", "dee@acme.com")])
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert sharepoint_calls(cloud)[:6] == [
            "list", f"item {FOLDER_UNIQUE_ID}", f"item {SHARED_UNIQUE_ID}", "list", "items", f"item {INHERITING_UNIQUE_ID}",
        ], "asked on its own while the pass writes it, not found unique only by the check after the pass"
        assert db.inherit_edges["f1"] is False
        assert (EntityType.USER, "u-dee", "dee@acme.com", PermissionType.READ) in perms(db, "f1")

    async def test_a_listed_item_without_the_flag_is_asked_on_its_own(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        reordered_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", f"{LIST_REST}/items", {"d": {"results": [
            {"Id": 1, "UniqueId": FOLDER_UNIQUE_ID, "HasUniqueRoleAssignments": True},
            {"Id": 2, "UniqueId": SHARED_UNIQUE_ID, "HasUniqueRoleAssignments": True},
            {"Id": 3, "UniqueId": INHERITING_UNIQUE_ID},
        ]}})
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        calls = sharepoint_calls(cloud)
        assert calls.index(f"item {INHERITING_UNIQUE_ID}") > calls.index("items")
        assert db.inherit_edges["f1"] is True

    async def test_an_item_that_broke_inheritance_during_the_pass_is_corrected_before_the_run_ends(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        reordered_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        listing = lambda f1_unique: {"d": {"results": [  # noqa: E731
            {"Id": 1, "UniqueId": FOLDER_UNIQUE_ID, "HasUniqueRoleAssignments": True},
            {"Id": 2, "UniqueId": SHARED_UNIQUE_ID, "HasUniqueRoleAssignments": True},
            {"Id": 3, "UniqueId": INHERITING_UNIQUE_ID, "HasUniqueRoleAssignments": f1_unique},
        ]}}
        # The first read, then the check after the pass: Ana shared f1 with Dee in between.
        cloud.on("GET", f"{LIST_REST}/items", [listing(False), listing(True)])
        cloud.on("GET", item_lookup_path(INHERITING_UNIQUE_ID), {"d": {"Id": 3, "HasUniqueRoleAssignments": True}})
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-dee", "dee@acme.com")])
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert db.inherit_edges["f1"] is False, "an item written as inheriting from a stale read would keep the folder's audience"
        assert (EntityType.USER, "u-dee", "dee@acme.com", PermissionType.READ) in perms(db, "f1")
        assert not permission_state(checkpoints).get("recheck")

    async def test_a_list_longer_than_a_page_is_read_to_the_end(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        reordered_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", f"{LIST_REST}/items", [
            {"d": {"results": [{"Id": 1, "UniqueId": FOLDER_UNIQUE_ID, "HasUniqueRoleAssignments": True},
                               {"Id": 2, "UniqueId": SHARED_UNIQUE_ID, "HasUniqueRoleAssignments": True}],
                   "__next": f"{PERSONAL_SITE}/_api/web/lists(guid'{LIST_ID}')/items?$skiptoken=Paged%3dTRUE%26p_ID%3d2"}},
            {"d": {"results": [{"Id": 3, "UniqueId": INHERITING_UNIQUE_ID, "HasUniqueRoleAssignments": True}]}},
        ])
        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-dee", "dee@acme.com")])
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert db.inherit_edges["f1"] is False, "f1 is unique on the second page"

    async def test_throttled_reads_are_retried(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", item_lookup_path(INHERITING_UNIQUE_ID), [
            json_response({"error": "throttled"}, status=429, headers={"Retry-After": "0"}),
            {"d": {"Id": 3, "HasUniqueRoleAssignments": False}},
        ])
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert db.inherit_edges["f1"] is True

    @pytest.mark.parametrize("retry_after", [
        lambda: "120",
        lambda: format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True),
    ], ids=["seconds", "http-date"])
    async def test_a_longer_retry_after_than_the_cap_is_not_retried_early(
        self, cloud, tenant, db, checkpoints, sharepoint_rest, retry_after
    ) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", item_lookup_path(INHERITING_UNIQUE_ID), [
            json_response({"error": "throttled"}, status=429, headers={"Retry-After": retry_after()}),
            {"d": {"Id": 3, "HasUniqueRoleAssignments": False}},
        ])
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert db.inherit_edges["f1"] is False, "unanswered this run: it keeps the access list OneDrive reports for it"
        assert sharepoint_calls(cloud).count(f"item {INHERITING_UNIQUE_ID}") == 1, "asked again next run, not sooner"

    async def test_one_sharepoint_token_serves_the_whole_run(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert len(sharepoint_calls(cloud)) > 2
        assert len([f for f in cloud.token_requests() if f.get("scope") == "https://acme-my.sharepoint.com/.default"]) == 1


class TestMigration:
    """A drive stored before the certificate is rewritten onto inheritance once, without losing changes."""

    async def secret_only_then_certificate(self, cloud, tenant, db, checkpoints) -> tuple[TokenFeed, OneDriveConnector]:
        """Links D* are made without the sharing-changes preference, S* with it (the rewrite's)."""
        feed = inheritance_drive(tenant)
        first = feed.by_token[None]
        feed.by_token[None] = [first, page(first["value"], delta_link=delta_link("u-ana", "S1"))]
        for n in range(1, 6):
            feed.by_token[f"D{n}"] = page([], delta_link=delta_link("u-ana", f"D{n + 1}"))
            feed.by_token[f"S{n}"] = page([], delta_link=delta_link("u-ana", f"S{n + 1}"))
        resync_links_made_without_sharing_changes(cloud, feed)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        await (await ready_connector(db, checkpoints)).run_sync()
        assert db.inherit_edges["f1"] is False and perms(db, "f1")
        return feed, await certificate_connector(db, checkpoints)

    async def test_without_a_certificate_no_permission_state_is_kept(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        await (await ready_connector(db, checkpoints)).run_sync()

        assert permission_state(checkpoints) == {}

    async def test_a_drive_synced_before_the_certificate_is_rewritten_onto_inheritance(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        feed, connector = await self.secret_only_then_certificate(cloud, tenant, db, checkpoints)
        content_updates = len(db.content_updates)

        await connector.run_sync()

        assert db.inherit_edges["f1"] is True and db.record_permissions["f1"] == []
        assert db.inherit_edges["f2"] is False
        assert (EntityType.USER, "u-cal", "cal@acme.com", PermissionType.READ) in perms(db, "f2")
        assert db.inherit_edges["d1"] is False and db.inherit_edges["root-ana"] is False
        assert permission_state(checkpoints)["mode"] == "inheritance"
        assert feed.seen == [None, "D1", None, "D2"], (
            "the normal delta, one full enumeration, then the old link once more so nothing deleted meanwhile is missed"
        )
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "S1")
        assert drive_checkpoint(checkpoints)["sharingChanges"] is True
        assert len(db.content_updates) == content_updates, "a rewrite of access re-indexes nothing"

    async def test_the_rewrite_happens_once(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed, connector = await self.secret_only_then_certificate(cloud, tenant, db, checkpoints)
        await connector.run_sync()
        writes = len(db.permission_updates)

        await connector.run_sync()

        assert feed.seen == [None, "D1", None, "D2", "S1"]
        assert len(db.permission_updates) == writes

    async def test_a_first_sync_with_the_certificate_needs_no_rewrite(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed = inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert feed.seen == [None]
        assert permission_state(checkpoints)["mode"] == "inheritance"

    async def test_an_interrupted_rewrite_resumes_where_it_stopped(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed, connector = await self.secret_only_then_certificate(cloud, tenant, db, checkpoints)
        root, d1, f1, f2 = feed.by_token[None][-1]["value"]
        feed.by_token[None] = page([root, d1], next_link=delta_link("u-ana", "M2"))
        feed.by_token["M2"] = [graph_error(400, "invalidRequest", "page unavailable"), page([f1, f2], delta_link=delta_link("u-ana", "S1"))]

        await connector.run_sync()
        assert permission_state(checkpoints)["migrationNextLink"] == delta_link("u-ana", "M2")
        assert permission_state(checkpoints).get("mode") is None
        await connector.run_sync()

        assert feed.seen == [None, "D1", None, "M2", "D2", "M2", "D3"], "resumed from its own cursor, not started over"
        assert drive_checkpoint(checkpoints)["deltaLink"] == delta_link("u-ana", "S1")
        assert permission_state(checkpoints)["mode"] == "inheritance"
        assert db.inherit_edges["f1"] is True and db.record_permissions["f1"] == []

    async def test_a_rewrite_that_keeps_failing_starts_over(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed, connector = await self.secret_only_then_certificate(cloud, tenant, db, checkpoints)
        root, d1, *_ = feed.by_token[None][-1]["value"]
        feed.by_token[None] = page([root, d1], next_link=delta_link("u-ana", "M2"))
        feed.by_token["M2"] = graph_error(400, "invalidRequest", "page unavailable")
        for token in ("D2", "D3", "D4", "D5"):
            feed.by_token[token] = page([], delta_link=delta_link("u-ana", f"D{int(token[1]) + 1}"))

        for _ in range(MAX_PAGE_ATTEMPTS):
            await connector.run_sync()

        assert permission_state(checkpoints)["migrationNextLink"] is None
        assert permission_state(checkpoints)["migrationFailures"] == 0

    async def test_a_transient_failure_in_inheritance_mode_queues_only_the_items_it_touched(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        feed = inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        feed.by_token["D1"] = page([changed("f1", "plan.pdf", INHERITING_UNIQUE_ID, 2, parent="d1")], delta_link=delta_link("u-ana", "D2"))
        feed.by_token["D2"] = page([], delta_link=delta_link("u-ana", "D3"))
        unavailable = json_response({"error": "busy"}, status=503, headers={"Retry-After": "0"})
        cloud.on("GET", item_lookup_path(INHERITING_UNIQUE_ID), unavailable)

        await connector.run_sync()

        assert db.inherit_edges["f1"] is False, "unknown: it keeps the access list OneDrive reports for it"
        assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "f1")
        assert permission_state(checkpoints)["recheck"] == ["f1"]
        assert permission_state(checkpoints)["mode"] == "inheritance"

        cloud.on("GET", item_lookup_path(INHERITING_UNIQUE_ID), {"d": {"Id": 3, "HasUniqueRoleAssignments": False}})
        await connector.run_sync()

        assert db.inherit_edges["f1"] is True and db.record_permissions["f1"] == []
        assert permission_state(checkpoints)["recheck"] == []
        assert feed.seen == [None, "D1", "D2"], "no full rewrite for a transient failure"

    async def test_too_many_unanswered_items_rewrite_the_drive_once_sharepoint_answers_again(
        self, cloud, tenant, db, checkpoints, sharepoint_rest, monkeypatch
    ) -> None:
        monkeypatch.setattr(f"{MODULE}.MAX_RECHECK_ITEMS", 1)
        feed = inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        feed.by_token["D1"] = page([
            changed("f1", "plan.pdf", INHERITING_UNIQUE_ID, 2, parent="d1"),
            changed("f2", "budget.pdf", SHARED_UNIQUE_ID, 2, parent="d1", shared=True),
        ], delta_link=delta_link("u-ana", "D2"))
        feed.by_token["D2"] = page([], delta_link=delta_link("u-ana", "D3"))
        cloud.on("GET", f"/v1.0/drives/{DRIVE}", graph_error(400, "invalidRequest"))

        await connector.run_sync()
        assert permission_state(checkpoints)["mode"] == "itemAcl"

        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        await connector.run_sync()

        assert feed.seen == [None, "D1", "D2", None]
        assert permission_state(checkpoints)["mode"] == "inheritance"
        assert db.inherit_edges["f1"] is True

    async def test_removing_the_certificate_keeps_inheritance_and_queues_what_changed(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        feed = inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        await (await certificate_connector(db, checkpoints)).run_sync()
        feed.by_token["D1"] = page([changed("f1", "plan.pdf", INHERITING_UNIQUE_ID, 2, parent="d1")], delta_link=delta_link("u-ana", "D2"))
        before = len(sharepoint_calls(cloud))

        await (await ready_connector(db, checkpoints)).run_sync()

        assert len(sharepoint_calls(cloud)) == before
        assert db.inherit_edges["f1"] is False
        assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) in perms(db, "f1")
        assert permission_state(checkpoints)["recheck"] == ["f1"]
        assert permission_state(checkpoints)["mode"] == "inheritance"


class TestFailClosed:
    """An item SharePoint calls unique never keeps its folder's audience through a stale inherit edge."""

    async def test_a_file_that_turned_unique_with_an_unreadable_list_loses_its_inherit_edge(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        feed = inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        assert db.inherit_edges["f1"] is True
        feed.by_token["D1"] = page([changed("f1", "plan.pdf", INHERITING_UNIQUE_ID, 2, parent="d1")], delta_link=delta_link("u-ana", "D2"))
        cloud.on("GET", item_lookup_path(INHERITING_UNIQUE_ID), {"d": {"Id": 3, "HasUniqueRoleAssignments": True}})
        tenant.share("f1", graph_error(403, "accessDenied"))

        await connector.run_sync()

        assert db.inherit_edges["f1"] is False
        assert db.record_permissions["f1"] == [], "its stored grants (none) stand until its list can be read"

    async def test_the_folder_walk_drops_the_inherit_edge_of_a_unique_child_it_cannot_read(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        feed = tenant.add_user("u-ana", "ana@acme.com", "Ana")
        feed.by_token[None] = page([
            root_item(),
            drive_item("d1", "Plans", folder=True, etag=guid_etag(FOLDER_UNIQUE_ID)),
            drive_item("f1", "plan.pdf", parent="d1", etag=guid_etag(INHERITING_UNIQUE_ID)),
        ], delta_link=delta_link("u-ana", "D1"))
        owner = user_grant("u-ana", "ana@acme.com", "owner")
        tenant.share("root-ana", [owner])
        tenant.share("d1", [owner])
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        assert db.inherit_edges["f1"] is True
        # d1 is shared with Ben; f1 broke inheritance and its list can't be read.
        feed.by_token["D1"] = page([changed("d1", "Plans", FOLDER_UNIQUE_ID, 2, folder=True, shared=True)], delta_link=delta_link("u-ana", "D2"))
        tenant.share("d1", [owner, user_grant("u-ben", "ben@acme.com")])
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/d1/children", page([drive_item("f1", "plan.pdf", parent="d1", etag=guid_etag(INHERITING_UNIQUE_ID))]))
        cloud.on("GET", item_lookup_path(INHERITING_UNIQUE_ID), {"d": {"Id": 3, "HasUniqueRoleAssignments": True}})
        tenant.share("f1", graph_error(403, "accessDenied"))

        await connector.run_sync()

        assert db.inherit_edges["f1"] is False, "through the edge, Ben would have reached a file that broke inheritance"
        assert (EntityType.USER, "u-ben", "ben@acme.com", PermissionType.READ) not in perms(db, "f1")


class TestOtherPaths:
    async def test_a_pending_access_read_of_an_inheriting_item_needs_no_graph_read(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        feed = inheritance_drive(tenant)
        feed.by_token["D1"] = page([], delta_link=delta_link("u-ana", "D2"))
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        await checkpoints.update_sync_point(next(k for k in checkpoints.sync_points if k.endswith("/users/u-ana")), {"pendingAccessReads": ["f1"]})
        graph_reads = len(cloud.calls("GET", f"/v1.0/drives/{DRIVE}/items/f1/permissions"))

        await connector.run_sync()

        assert len(cloud.calls("GET", f"/v1.0/drives/{DRIVE}/items/f1/permissions")) == graph_reads
        assert db.permission_updates[-1][0].external_record_id == "f1"
        assert db.inherit_edges["f1"] is True and db.record_permissions["f1"] == []
        assert drive_checkpoint(checkpoints)["pendingAccessReads"] == []

    async def test_reindexing_one_record_asks_sharepoint_about_that_item_only(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        cloud.on("GET", f"/v1.0/drives/{DRIVE}/items/f1",
                 changed("f1", "plan.pdf", INHERITING_UNIQUE_ID, 2, parent="d1"))
        before = len(sharepoint_calls(cloud))

        await connector.reindex_records([await db.get_record_by_external_id(CONNECTOR_ID, "f1")])

        assert sharepoint_calls(cloud)[before:] == ["list", f"item {INHERITING_UNIQUE_ID}"]

    async def test_a_reindex_keeps_the_runs_refusal(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        connector = await certificate_connector(db, checkpoints)
        connector._sharepoint_refusal = "refused earlier this run"

        await connector.reindex_records([])
        await connector.reindex_records([FileRecord(
            id="r1", record_name="x.pdf", record_type=RecordType.FILE, external_record_id="missing",
            external_record_group_id=DRIVE, version=0, origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.ONEDRIVE, connector_id=CONNECTOR_ID, is_file=True, extension="pdf",
        )])

        assert connector._sharepoint_refusal == "refused earlier this run"


SUGGESTION = "OneDrive: switch to certificate sign-in"


def suggestions(sent: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in sent if n.get("title") == SUGGESTION]


class TestCertificateSuggestion:
    """A connector still signing in with a client secret reminds its admin, from any run, at most once a week."""

    async def test_scheduled_runs_remind_once_and_a_restart_does_not_repeat_it(self, cloud, tenant, db, checkpoints) -> None:
        feed = inheritance_drive(tenant)
        feed.by_token["D1"] = page([], delta_link=delta_link("u-ana", "D2"))
        connector = await ready_connector(db, checkpoints)

        await connector.run_incremental_sync()
        await connector.run_incremental_sync()
        restarted = await ready_connector(db, checkpoints)
        restarted._notification_service = connector._notification_service
        # The backoff of notify() lives in the process; a restart forgets it, the sync point doesn't.
        BaseConnector._notification_cache.clear()
        await restarted.run_sync()
        await notes(restarted)

        sent = suggestions(await notes(connector))
        assert len(sent) == 1, "a scheduled (incremental) run reminds too, but not on every run"
        assert "Recommended" in sent[0]["message"] and "Sites.Read.All" in sent[0]["message"]
        assert "inherit" not in sent[0]["message"], "it says what to do, not how inheritance works"
        assert sent[0]["recipient_user_ids"] == ["creator-1"]

    async def test_a_week_later_it_reminds_again(self, cloud, tenant, db, checkpoints) -> None:
        inheritance_drive(tenant)
        connector = await ready_connector(db, checkpoints)
        await connector.run_sync()
        key = next(k for k in checkpoints.sync_points if k.endswith("permissionNotices/connector/certificate"))
        sent_at = checkpoints.sync_points[key]["lastSentAt"]
        checkpoints.sync_points[key]["lastSentAt"] = sent_at - (7 * 24 * 3600 * 1000 + 1)

        await connector.run_sync()

        assert len(suggestions(await notes(connector))) == 2

    async def test_a_connector_with_a_certificate_is_not_reminded(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert not suggestions(await notes(connector))


class TestReviewFindings:
    """Paths an independent review found could lose an answer or keep a stale inherit edge."""

    async def test_with_a_certificate_delta_reports_sharing_only_changes(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        await (await certificate_connector(db, checkpoints)).run_sync()

        assert {r.headers.get("Prefer") for r in cloud.calls("GET", DELTA_PATH)} == {"deltashowsharingchanges"}, (
            "an item that breaks inheritance changes only its sharing; unreported, it keeps its folder's audience"
        )

    async def test_without_a_certificate_delta_is_asked_as_before(self, cloud, tenant, db, checkpoints) -> None:
        inheritance_drive(tenant)
        await (await ready_connector(db, checkpoints)).run_sync()

        assert {r.headers.get("Prefer") for r in cloud.calls("GET", DELTA_PATH)} == {None}

    async def test_full_read_answers_of_a_pass_that_failed_are_queued(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed = reordered_drive(tenant)
        feed.by_token[None] = page(feed.by_token[None]["value"], next_link=delta_link("u-ana", "P2"))
        feed.by_token["P2"] = graph_error(400, "invalidRequest", "page unavailable")
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)

        with pytest.raises(ODataError):
            await connector._run_sync_with_yield("u-ana")

        assert db.inherit_edges["f1"] is True
        assert "f1" in permission_state(checkpoints)["recheck"], "its answer was never checked against a second read"

    async def test_marking_the_drive_keeps_items_queued_while_the_run_went_on(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        _, connector = await TestMigration().secret_only_then_certificate(cloud, tenant, db, checkpoints)
        verify = connector._verify_scanned_inheritance

        async def reindex_queues_an_item_meanwhile(drive_id: str) -> None:
            await connector.drive_delta_sync_point.update_sync_point(_drive_permissions_key(DRIVE), {"recheck": ["queued-by-reindex"]})
            await verify(drive_id)

        connector._verify_scanned_inheritance = reindex_queues_an_item_meanwhile
        await connector.run_sync()

        assert permission_state(checkpoints)["mode"] == "inheritance"
        assert permission_state(checkpoints)["recheck"] == ["queued-by-reindex"]

    async def test_an_item_that_failed_during_the_rewrite_is_queued(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        _, connector = await TestMigration().secret_only_then_certificate(cloud, tenant, db, checkpoints)
        inherits = connector._inherits_parent_access

        async def fails_for_f1(item: object) -> bool | None:
            if getattr(item, "id", None) == "f1":
                raise RuntimeError("database unavailable")
            return await inherits(item)

        connector._inherits_parent_access = fails_for_f1
        await connector.run_sync()

        assert permission_state(checkpoints)["mode"] == "inheritance"
        assert permission_state(checkpoints)["recheck"] == ["f1"], "it still carries the copy written before the certificate"

    async def test_a_hidden_unique_item_is_read_again_next_run(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed = inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        feed.by_token["D1"] = page([changed("f1", "plan.pdf", INHERITING_UNIQUE_ID, 2, parent="d1")], delta_link=delta_link("u-ana", "D2"))
        feed.by_token["D2"] = page([], delta_link=delta_link("u-ana", "D3"))
        cloud.on("GET", item_lookup_path(INHERITING_UNIQUE_ID), {"d": {"Id": 3, "HasUniqueRoleAssignments": True}})
        tenant.share("f1", graph_error(403, "accessDenied"))
        await connector.run_sync()
        assert db.inherit_edges["f1"] is False and db.record_permissions["f1"] == []
        assert drive_checkpoint(checkpoints)["pendingAccessReads"] == ["f1"]

        tenant.share("f1", [user_grant("u-ana", "ana@acme.com", "owner"), user_grant("u-dee", "dee@acme.com")])
        await connector.run_sync()

        assert (EntityType.USER, "u-dee", "dee@acme.com", PermissionType.READ) in perms(db, "f1")
        assert db.inherit_edges["f1"] is False

    async def test_progress_through_a_long_rewrite_resets_its_failure_count(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed, connector = await TestMigration().secret_only_then_certificate(cloud, tenant, db, checkpoints)
        root, d1, f1, f2 = feed.by_token[None][-1]["value"]
        feed.by_token[None] = page([root, d1], next_link=delta_link("u-ana", "M2"))
        feed.by_token["M2"] = [graph_error(400, "invalidRequest", "page unavailable"), page([f1], next_link=delta_link("u-ana", "M3"))]
        feed.by_token["M3"] = graph_error(400, "invalidRequest", "page unavailable")
        await connector.run_sync()
        assert permission_state(checkpoints)["migrationFailures"] == 1

        await connector.run_sync()

        assert permission_state(checkpoints)["migrationFailures"] == 1, (
            "M2 committed before M3 failed: failures spread over a long rewrite don't add up to a restart"
        )

    async def test_a_network_error_signing_in_to_sharepoint_is_not_a_refused_certificate(
        self, cloud, tenant, db, checkpoints, sharepoint_rest, monkeypatch
    ) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)

        async def unreachable(_resource: str) -> object:
            raise ServiceRequestError("connection reset")

        connector.scope_reader._get_token = unreachable
        await connector.run_sync()

        assert connector._sharepoint_refusal is None
        assert INHERITANCE_WARNING not in notification_titles(await notes(connector))
        assert db.inherit_edges["f1"] is False

    async def test_an_unreadable_permission_state_does_not_stop_the_sync(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)
        read = checkpoints.get_sync_point

        async def failing_for_permissions(key: str, raise_on_error: bool = False) -> dict[str, Any] | None:
            if "drivePermissions" in key:
                raise RuntimeError("database unavailable")
            return await read(key, raise_on_error)

        checkpoints.get_sync_point = failing_for_permissions
        await connector.run_sync()

        assert {"root-ana", "d1", "f1", "f2"} <= set(db.records)

    async def test_a_rejected_certificate_beside_a_secret_says_how_to_save_without_it(self, cloud, sharepoint_rest) -> None:
        failing_certificate_sign_in(cloud)

        result = await OneDriveConnector.check_connection(
            TestConnectionCheck.auth(certificate=CERTIFICATE, privateKey=PRIVATE_KEY), logging.getLogger("test")
        )

        assert result.success is False and "remove the certificate and key" in result.message


class TestSharingChangesLink:
    """The sharing-changes preference follows the delta link it was made with; Graph refuses it otherwise."""

    async def test_adding_a_certificate_never_asks_an_old_link_for_sharing_changes(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed, connector = await TestMigration().secret_only_then_certificate(cloud, tenant, db, checkpoints)
        wrong = resync_links_made_without_sharing_changes(cloud, feed)

        await connector.run_sync()
        await connector.run_sync()

        assert wrong == []
        assert [r.headers.get("Prefer") for r in cloud.calls("GET", DELTA_PATH)][-1] == "deltashowsharingchanges"
        assert permission_state(checkpoints)["mode"] == "inheritance"

    async def test_a_first_sync_with_a_certificate_makes_its_link_with_sharing_changes(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        await (await certificate_connector(db, checkpoints)).run_sync()

        assert drive_checkpoint(checkpoints)["sharingChanges"] is True
        assert {r.headers.get("Prefer") for r in cloud.calls("GET", DELTA_PATH)} == {"deltashowsharingchanges"}

    async def test_removing_the_certificate_keeps_asking_the_link_as_it_was_made(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        feed = inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        await (await certificate_connector(db, checkpoints)).run_sync()
        feed.by_token["D1"] = page([], delta_link=delta_link("u-ana", "D2"))

        await (await ready_connector(db, checkpoints)).run_sync()

        assert cloud.calls("GET", DELTA_PATH)[-1].headers.get("Prefer") == "deltashowsharingchanges"


def drive_with_sharepoint_ids(cloud: MicrosoftCloudStub) -> None:
    cloud.on("GET", "/v1.0/users/u-ana/drive", {"id": DRIVE, "sharePointIds": {"listId": LIST_ID, "siteUrl": PERSONAL_SITE}})


class TestConnectionCheckReadsSharePoint:
    """With a certificate, the setup form's check also asks SharePoint; a refusal there blocks the save."""

    async def check(self, **auth: str | None) -> ConnectionCheckResult:
        return await OneDriveConnector.check_connection(TestConnectionCheck.auth(**auth), logging.getLogger("test"))

    async def test_sharepoint_answering_passes(self, cloud, tenant, sharepoint_rest) -> None:
        tenant.add_user("u-ana", "ana@acme.com", "Ana")
        drive_with_sharepoint_ids(cloud)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID)

        result = await self.check(clientSecret=None, certificate=CERTIFICATE, privateKey=PRIVATE_KEY)

        assert result.success is True and "SharePoint lets the app read" in result.message
        assert sharepoint_calls(cloud) == ["list"]

    @pytest.mark.parametrize("status", [401, 403])
    async def test_sharepoint_refusing_the_app_fails_and_names_the_permission(self, cloud, tenant, sharepoint_rest, status) -> None:
        tenant.add_user("u-ana", "ana@acme.com", "Ana")
        drive_with_sharepoint_ids(cloud)
        cloud.on("GET", LIST_REST, json_response({"error": {"message": {"value": "Access denied."}}}, status=status))

        result = await self.check(clientSecret=None, certificate=CERTIFICATE, privateKey=PRIVATE_KEY)

        assert result.success is False
        assert "Sites.Read.All application permission of the SharePoint API (not Microsoft Graph)" in result.message

    async def test_a_legacy_secret_with_a_new_certificate_is_checked_against_sharepoint_too(self, cloud, tenant, sharepoint_rest) -> None:
        tenant.add_user("u-ana", "ana@acme.com", "Ana")
        drive_with_sharepoint_ids(cloud)
        cloud.on("GET", LIST_REST, json_response({"error": "denied"}, status=403))

        result = await self.check(certificate=CERTIFICATE, privateKey=PRIVATE_KEY)

        assert result.success is False

    async def test_sharepoint_out_of_reach_lets_the_save_go_ahead(self, cloud, tenant, sharepoint_rest) -> None:
        tenant.add_user("u-ana", "ana@acme.com", "Ana")
        drive_with_sharepoint_ids(cloud)

        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        cloud.on("GET", LIST_REST, unreachable)

        result = await self.check(clientSecret=None, certificate=CERTIFICATE, privateKey=PRIVATE_KEY)

        assert result.success is True and "could not be checked now" in result.message
        assert sharepoint_calls(cloud) == ["list"]

    async def test_no_onedrive_to_check_with_lets_the_save_go_ahead(self, cloud, tenant, sharepoint_rest) -> None:
        tenant.add_user("u-ana", "ana@acme.com", "Ana")
        cloud.on("GET", "/v1.0/users/u-ana/drive", graph_error(404, "ResourceNotFound"))

        result = await self.check(clientSecret=None, certificate=CERTIFICATE, privateKey=PRIVATE_KEY)

        assert result.success is True and "No user has a OneDrive yet" in result.message
        assert not sharepoint_calls(cloud)

    async def test_a_secret_alone_never_asks_sharepoint(self, cloud, tenant, sharepoint_rest) -> None:
        tenant.add_user("u-ana", "ana@acme.com", "Ana")

        result = await self.check()

        assert result.success is True
        assert not sharepoint_calls(cloud)
        assert not cloud.calls("GET", "/v1.0/users"), "a legacy secret check is a sign-in only, as before"


def codes(items: list[Any] | None) -> list[str] | None:
    return None if items is None else [item.code for item in items]


class TestConfigActionItems:
    def test_a_secret_without_a_certificate_asks_to_switch(self) -> None:
        items = onedrive_config_action_items({"clientSecret": "s"})

        assert codes(items) == ["certificate_sign_in"] and items[0].action == "authenticate"
        assert items[0].severity == "warning"
        assert items[0].docs_url is not None and items[0].docs_url.endswith("#switch-to-certificate-sign-in")
        assert items[0].model_dump(by_alias=True)["docsUrl"] == items[0].docs_url, "stored as the UI reads it"

    def test_a_usable_certificate_needs_nothing(self) -> None:
        assert onedrive_config_action_items({"certificate": CERTIFICATE, "privateKey": PRIVATE_KEY}) == []

    def test_a_certificate_expiring_within_30_days_asks_for_a_new_one(self) -> None:
        soon, key = self_signed_certificate(valid_days=20)

        items = onedrive_config_action_items({"certificate": soon, "privateKey": key})

        assert codes(items) == ["certificate_expiring"]

    def test_an_expired_certificate_is_an_error(self) -> None:
        expired, key = self_signed_certificate(valid_days=1, start_days_ago=10)

        items = onedrive_config_action_items({"clientSecret": "s", "certificate": expired, "privateKey": key})

        assert codes(items) == ["certificate_unusable"] and items[0].severity == "error"

    def test_nothing_saved_needs_nothing(self) -> None:
        assert onedrive_config_action_items({}) == []


class TestRunActionItems:
    """What a run leaves for the instance card; what SharePoint said stays until it says otherwise."""

    async def test_a_secret_only_run_asks_to_switch(self, cloud, tenant, db, checkpoints) -> None:
        inheritance_drive(tenant)
        connector = await ready_connector(db, checkpoints)

        await connector.run_sync()

        assert codes(connector.last_action_items) == ["certificate_sign_in"]

    async def test_a_working_certificate_leaves_nothing(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert connector.last_action_items == []

    async def test_a_refused_certificate_stays_on_the_card_through_quiet_runs_until_sharepoint_answers(
        self, cloud, tenant, db, checkpoints, sharepoint_rest
    ) -> None:
        feed = inheritance_drive(tenant)
        feed.by_token["D1"] = page([], delta_link=delta_link("u-ana", "D2"))
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", LIST_REST, json_response({"error": {"message": {"value": "Unsupported app only token."}}}, status=401))
        connector = await certificate_connector(db, checkpoints)
        await connector.run_sync()
        assert codes(connector.last_action_items) == ["sharepoint_refused"]

        await connector.run_incremental_sync()
        assert codes(connector.last_action_items) == ["sharepoint_refused"], "a run that didn't ask SharePoint knows nothing new"

        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        feed.by_token["D2"] = page([changed("f1", "plan.pdf", INHERITING_UNIQUE_ID, 2, parent="d1")], delta_link=delta_link("u-ana", "D3"))
        await connector.run_incremental_sync()
        assert connector.last_action_items == []

    async def test_a_missing_sharepoint_permission_is_named(self, cloud, tenant, db, checkpoints, sharepoint_rest) -> None:
        inheritance_drive(tenant)
        serve_unique_items(cloud, FOLDER_UNIQUE_ID, SHARED_UNIQUE_ID, inheriting=(INHERITING_UNIQUE_ID,))
        cloud.on("GET", LIST_REST, json_response({"error": "denied"}, status=403))
        connector = await certificate_connector(db, checkpoints)

        await connector.run_sync()

        assert codes(connector.last_action_items) == ["sharepoint_permission_missing"]
        assert connector.last_action_items[0].severity == "error"
