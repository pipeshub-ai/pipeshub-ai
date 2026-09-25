"""Extra fakes for the Atlassian Cloud (OAuth) behaviour tests.

Cloud clients are built during ``init()`` and also make a one-off call to
``api.atlassian.com`` to find the site, so instead of installing the stub on one
client, every ``HTTPClient`` created during the test is pointed at it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional

import httpx
from atlassian_behaviour_fakes import AtlassianApiStub, FakeRecordsDb, json_response

if TYPE_CHECKING:
    import pytest

from app.sources.client.http.http_client import HTTPClient

CLOUD_ID = "cloud-123"
SITE = "https://acme.atlassian.net"
RESOURCES_PATH = "/oauth/token/accessible-resources"


def route_every_http_client(monkeypatch: pytest.MonkeyPatch, api: AtlassianApiStub) -> None:
    async def _ensure_client(self: HTTPClient) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(
                transport=httpx.MockTransport(api), headers=self.headers, follow_redirects=True
            )
        return self.client

    monkeypatch.setattr(HTTPClient, "_ensure_client", _ensure_client)


def one_site(api: AtlassianApiStub) -> None:
    api.on("GET", RESOURCES_PATH, json_response([{"id": CLOUD_ID, "name": "acme", "url": SITE, "scopes": []}]))


def oauth_config(access_token: str = "fake-access-1", base_url: Optional[str] = SITE) -> dict[str, Any]:
    auth: dict[str, Any] = {"authType": "OAUTH"}
    if base_url:
        auth["baseUrl"] = base_url
    return {"auth": auth, "credentials": {"access_token": access_token, "refresh_token": "fake-refresh"}, "filters": {}}


def bearer(request: httpx.Request) -> str:
    return request.headers.get("Authorization", "")


class CloudRecordsDb(FakeRecordsDb):
    """Adds the user/group lookups and bulk calls the Cloud connectors make."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.users_by_source_id: dict[str, Any] = {}
        self.groups_by_external_id: dict[str, Any] = {}
        self.placeholders: list[Any] = []
        self.active_users: list[Any] = []
        self.cascade_deleted: list[str] = []
        self.batch_upserted: list[Any] = []
        self.fail_permission_lookup = False

    def add_user(self, source_id: str, email: str) -> None:
        self.users_by_source_id[source_id] = type("AppUserRow", (), {"email": email, "source_user_id": source_id})()

    def add_group(self, external_id: str) -> None:
        self.groups_by_external_id[external_id] = type("GroupRow", (), {"source_user_group_id": external_id})()

    async def get_user_by_source_id(self, source_user_id: str, connector_id: str) -> object:
        return self.users_by_source_id.get(source_user_id)

    async def get_user_group_by_external_id(self, connector_id: str, external_id: str) -> object:
        return self.groups_by_external_id.get(external_id)

    async def get_placeholder_records(self, connector_id: str) -> list[Any]:
        return list(self.placeholders)

    async def get_all_active_users(self) -> list[Any]:
        return list(self.active_users)

    async def on_records_deleted_cascade(self, record_ids: list[str], connector_id: str, **_: object) -> None:
        self.cascade_deleted.extend(record_ids)

    async def batch_upsert_records(self, records: list[Any]) -> None:
        self.batch_upserted.extend(records)


class RecordingNotifications:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def publish_notification(self, **kwargs: object) -> None:
        self.sent.append(kwargs)


async def drain_notifications(connector: object) -> None:
    tasks = list(getattr(connector, "_background_tasks", ()))
    if tasks:
        await asyncio.gather(*tasks)
