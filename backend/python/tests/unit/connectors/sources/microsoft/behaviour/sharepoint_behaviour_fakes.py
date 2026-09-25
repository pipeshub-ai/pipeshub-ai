"""Extra fakes for the SharePoint Online behaviour tests.

Besides the Graph SDK, the connector calls SharePoint's own REST API with a
bare ``httpx.AsyncClient`` (site groups) and ``aiohttp.ClientSession`` (site
permissions), and builds a fresh ``ClientSecretCredential`` from
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
              parent_id: str = ROOT_ITEM_ID, path: str = "/drive/root:") -> dict[str, Any]:
    return {
        "id": item_id, "name": name, "eTag": etag, "cTag": f"c-{etag}", "size": 42,
        "webUrl": f"{SITE_URL}/Documents/{name}",
        "createdDateTime": CREATED, "lastModifiedDateTime": MODIFIED,
        "file": {"mimeType": "application/pdf", "hashes": {"quickXorHash": xor}},
        "parentReference": {"driveId": DRIVE_ID, "id": parent_id, "path": path},
    }


def deleted_item(item_id: str) -> dict[str, Any]:
    return {"id": item_id, "deleted": {"state": "deleted"}, "file": {}, "parentReference": {"driveId": DRIVE_ID}}


def user_grant(user_id: str, email: str, role: str = "read") -> dict[str, Any]:
    return {"id": f"p-{user_id}", "roles": [role], "grantedToV2": {"user": {"id": user_id, "displayName": email, "email": email}}}


def group_grant(group_id: str, role: str = "read") -> dict[str, Any]:
    return {"id": f"p-{group_id}", "roles": [role], "grantedToV2": {"group": {"id": group_id, "displayName": group_id}}}


def link_grant(scope: str, link_type: str = "view") -> dict[str, Any]:
    return {"id": f"p-link-{scope}", "roles": ["read"], "link": {"scope": scope, "type": link_type}}


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
