"""Extra fakes for the SharePoint Online behaviour tests.

Besides the Graph SDK, the connector calls SharePoint's own REST API with a
bare ``httpx.AsyncClient`` (site groups, unique permissions) and
``aiohttp.ClientSession`` (site permissions), and builds a fresh ``ClientSecretCredential`` from
``azure.identity.aio`` for every SharePoint token. All of those are pointed at
the same ``MicrosoftCloudStub``; the connector module sees a copy of ``httpx``
and ``aiohttp`` whose client classes use it, so nothing else is affected.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional

import aiohttp
import httpx
from ms_graph_fakes import (
    GRAPH,
    TENANT,
    FakeCheckpointStore,
    FakeConfigService,
    FakeRecordsDb,
    MicrosoftCloudStub,
    RecordingNotifications,
    route_microsoft_http,
)

from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)

if TYPE_CHECKING:
    import pytest

    from app.models.entities import RecordGroup

MODULE = "app.connectors.sources.microsoft.sharepoint_online.connector"
CONNECTOR_ID = "sharepoint-1"
SP_HOST = "contoso.sharepoint.com"
SITE_URL = f"https://{SP_HOST}/sites/eng"
SITE_ID = f"{SP_HOST},{'1' * 32},{'2' * 32}"
DRIVE_ID = "b!drive-1"
ROOT_ITEM_ID = "root-item"
CREATED = "2024-01-01T00:00:00Z"
MODIFIED = "2024-05-01T10:00:00Z"


class _AiohttpResponse:
    def __init__(self, answer: httpx.Response) -> None:
        self._answer = answer
        self.status = answer.status_code

    async def __aenter__(self) -> "_AiohttpResponse":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def json(self, **_: object) -> object:
        return json.loads(self._answer.content or b"null")

    async def text(self) -> str:
        return self._answer.text


class _AiohttpSession:
    def __init__(self, stub: MicrosoftCloudStub) -> None:
        self._stub = stub

    async def __aenter__(self) -> "_AiohttpSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def get(self, url: str, headers: Optional[dict[str, str]] = None, **_: object) -> _AiohttpResponse:
        return _AiohttpResponse(self._stub(httpx.Request("GET", url, headers=headers or {})))


def route_sharepoint_http(monkeypatch: pytest.MonkeyPatch, stub: MicrosoftCloudStub) -> None:
    route_microsoft_http(monkeypatch, stub, MODULE, "azure.identity.aio")
    real_async_client = httpx.AsyncClient

    def _async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(stub)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(f"{MODULE}.httpx", SimpleNamespace(**{**vars(httpx), "AsyncClient": _async_client}))
    monkeypatch.setattr(
        f"{MODULE}.aiohttp", SimpleNamespace(**{**vars(aiohttp), "ClientSession": lambda *a, **k: _AiohttpSession(stub)})
    )


def site_payload(site_id: str = SITE_ID, web_url: str = SITE_URL, name: str = "eng") -> dict[str, Any]:
    return {
        "id": site_id, "name": name, "displayName": name.title(), "webUrl": web_url,
        "createdDateTime": CREATED, "lastModifiedDateTime": MODIFIED,
        "siteCollection": {"dataLocationCode": "NAM", "hostname": SP_HOST},
    }


def sharepoint_config() -> dict[str, Any]:
    return {"auth": {"tenantId": TENANT, "clientId": "client-1", "clientSecret": "secret-1", "sharepointDomain": f"https://{SP_HOST}"}}


async def ready_connector(
    stub: MicrosoftCloudStub, db: FakeRecordsDb, checkpoints: FakeCheckpointStore
) -> SharePointConnector:
    stub.on("GET", "/v1.0/sites/root", site_payload())
    db.edge_store = checkpoints
    connector = SharePointConnector(
        logging.getLogger("test.sharepoint"), db, checkpoints,
        FakeConfigService(CONNECTOR_ID, sharepoint_config()), CONNECTOR_ID, "team", "creator-1",
    )
    connector._notification_service = RecordingNotifications()
    assert await connector.init() is True
    return connector


def drive_path(site_id: str = SITE_ID) -> str:
    return f"/v1.0/sites/{site_id}/drives"


def delta_path(drive_id: str = DRIVE_ID, site_id: str = SITE_ID) -> str:
    return f"/v1.0/sites/{site_id}/drives/{drive_id}/root/delta"


def delta_url(token: str, drive_id: str = DRIVE_ID) -> str:
    return f"{GRAPH}/sites/{SITE_ID}/drives/{drive_id}/root/delta?token={token}"


def drive_payload(drive_id: str = DRIVE_ID, name: str = "Documents") -> dict[str, Any]:
    return {"id": drive_id, "name": name, "driveType": "documentLibrary", "webUrl": f"{SITE_URL}/{name}",
            "createdDateTime": CREATED, "lastModifiedDateTime": MODIFIED}


def root_item() -> dict[str, Any]:
    return {"id": ROOT_ITEM_ID, "name": "root", "root": {}, "folder": {"childCount": 1},
            "eTag": "root-etag", "createdDateTime": CREATED, "lastModifiedDateTime": MODIFIED,
            "parentReference": {"driveId": DRIVE_ID}}


def file_item(item_id: str, name: str, *, etag: str = "e1", xor: str = "h1",
              parent_id: str = ROOT_ITEM_ID, path: str = "/drive/root:",
              list_item_unique_id: str | None = None) -> dict[str, Any]:
    item = {
        "id": item_id, "name": name, "eTag": etag, "cTag": f"c-{etag}", "size": 42,
        "webUrl": f"{SITE_URL}/Documents/{name}",
        "createdDateTime": CREATED, "lastModifiedDateTime": MODIFIED,
        "file": {"mimeType": "application/pdf", "hashes": {"quickXorHash": xor}},
        "parentReference": {"driveId": DRIVE_ID, "id": parent_id, "path": path},
    }
    if list_item_unique_id:
        item["sharepointIds"] = {"listItemUniqueId": list_item_unique_id}
    return item


def deleted_item(item_id: str) -> dict[str, Any]:
    return {"id": item_id, "deleted": {"state": "deleted"}, "file": {}, "parentReference": {"driveId": DRIVE_ID}}


def user_grant(user_id: str, email: str, role: str = "read") -> dict[str, Any]:
    return {"id": f"p-{user_id}", "roles": [role], "grantedToV2": {"user": {"id": user_id, "displayName": email, "email": email}}}


def group_grant(group_id: str, role: str = "read") -> dict[str, Any]:
    return {"id": f"p-{group_id}", "roles": [role], "grantedToV2": {"group": {"id": group_id, "displayName": group_id}}}


def link_grant(scope: str, link_type: str = "view") -> dict[str, Any]:
    return {"id": f"p-link-{scope}", "roles": ["read"], "link": {"scope": scope, "type": link_type}}


def site_group_grant(group_id: str, name: str, role: str = "read") -> dict[str, Any]:
    """A SharePoint site group as Graph lists it on a drive item: ``grantedToV2.siteGroup``."""
    return {"id": f"p-sg-{group_id}", "roles": [role],
            "grantedToV2": {"siteGroup": {"id": group_id, "displayName": name, "loginName": name}}}


def m365_grant(group_id: str, name: str, role: str = "read", *, owners_claim: bool | None = None) -> dict[str, Any]:
    """An M365 group grant; ``owners_claim`` adds the SharePoint claim Graph sends alongside (``_o`` for owners)."""
    granted: dict[str, Any] = {"group": {"id": group_id, "displayName": name}}
    if owners_claim is not None:
        suffix = "_o" if owners_claim else ""
        granted["siteUser"] = {"id": "9", "displayName": name,
                               "loginName": f"c:0o.c|federateddirectoryclaimprovider|{group_id}{suffix}"}
    return {"id": f"p-m365-{group_id}", "roles": [role], "grantedToV2": granted}


def guid_etag(unique_id: str, version: int = 1) -> str:
    """A drive item eTag, which carries the list item's UniqueId: ``"{GUID},n"``."""
    return f'"{{{unique_id.upper()}}},{version}"'


LIST_ID = "bae38f78-8067-48b8-814b-9dc0e7b47dca"
WEB_REST = "/sites/eng/_api/web"
LIBRARY_REST = f"{WEB_REST}/lists(guid'{LIST_ID}')"
SITE_PAGES_REST = f"{WEB_REST}/lists/getbytitle('Site Pages')"

ROLE_TYPE_KINDS = {"Limited Access": 1, "Read": 2, "Contribute": 3, "Design": 4, "Full Control": 5, "Edit": 6}


def rest_body(payload: dict[str, Any]) -> dict[str, Any]:
    return {"d": payload}


def rest_results(*entries: dict[str, Any], next_url: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"results": list(entries)}
    if next_url:
        body["__next"] = next_url
    return {"d": body}


def role_assignment(member: dict[str, Any], *role_names: str) -> dict[str, Any]:
    bindings = [{"Name": name, "RoleTypeKind": ROLE_TYPE_KINDS.get(name, 0)} for name in role_names]
    return {"Member": member, "RoleDefinitionBindings": {"results": bindings}}


def sp_group(group_id: int, title: str) -> dict[str, Any]:
    return {"PrincipalType": 8, "Id": group_id, "Title": title, "LoginName": title}


def sp_user(user_id: int, email: str) -> dict[str, Any]:
    return {"PrincipalType": 1, "Id": user_id, "Title": email, "Email": email,
            "LoginName": f"i:0#.f|membership|{email}"}


def unique_list_items(*items: tuple[int, str]) -> dict[str, Any]:
    return rest_results(*[{"Id": item_id, "UniqueId": unique_id, "HasUniqueRoleAssignments": True}
                          for item_id, unique_id in items])


def serve_library_permissions(
    stub: MicrosoftCloudStub, *, unique: bool = False, unique_items: tuple[tuple[int, str], ...] = (),
    role_assignments: tuple[dict[str, Any], ...] = (), drive_id: str = DRIVE_ID,
) -> None:
    """The drive's SharePoint ids on Graph, and its list on SharePoint REST."""
    stub.on("GET", f"/v1.0/drives/{drive_id}",
            {"id": drive_id, "sharePointIds": {"listId": LIST_ID, "siteUrl": SITE_URL}})
    stub.on("GET", LIBRARY_REST, rest_body({"Id": LIST_ID, "HasUniqueRoleAssignments": unique}))
    stub.on("GET", f"{LIBRARY_REST}/items", unique_list_items(*unique_items))
    stub.on("GET", f"{LIBRARY_REST}/roleassignments", rest_results(*role_assignments))


def serve_site_pages_permissions(
    stub: MicrosoftCloudStub, unique_items: tuple[tuple[int, str], ...] = (),
    role_assignments_by_item: dict[int, tuple[dict[str, Any], ...]] | None = None,
) -> None:
    stub.on("GET", SITE_PAGES_REST, rest_body({"HasUniqueRoleAssignments": False}))
    stub.on("GET", f"{SITE_PAGES_REST}/items", unique_list_items(*unique_items))
    for item_id, assignments in (role_assignments_by_item or {}).items():
        stub.on("GET", f"{SITE_PAGES_REST}/items({item_id})/roleassignments", rest_results(*assignments))


def serve_item(stub: MicrosoftCloudStub, item_id: str, permissions: object, drive_id: str = DRIVE_ID) -> None:
    stub.on("GET", f"/v1.0/drives/{drive_id}/items/{item_id}",
            {"id": item_id, "@microsoft.graph.downloadUrl": f"https://download.example/{item_id}"})
    stub.on("GET", f"/v1.0/drives/{drive_id}/items/{item_id}/permissions",
            permissions if not isinstance(permissions, list) else {"value": permissions})


def site_record_group(connector: SharePointConnector) -> RecordGroup:
    from app.models.entities import RecordGroup, RecordGroupType

    return RecordGroup(
        id="site-group-1", name="Eng", external_group_id=SITE_ID, connector_name=connector.connector_name,
        connector_id=CONNECTOR_ID, group_type=RecordGroupType.SHAREPOINT_SITE, web_url=SITE_URL,
    )
