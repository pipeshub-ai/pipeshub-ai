"""Fakes for behaviour tests of the Atlassian connectors.

Only two things are faked: the Atlassian REST API (an ``httpx.MockTransport``
behind the connector's real HTTP client, so request building, auth headers and
response parsing all run for real) and our own databases (records, record
groups and sync checkpoints, kept in memory so a second sync sees what the
first one wrote).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx

if TYPE_CHECKING:
    from app.models.entities import Record
    from app.sources.client.http.http_client import HTTPClient

Handler = Callable[[httpx.Request], httpx.Response]


def json_response(payload: object, status: int = 200, headers: Optional[dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode(), headers={"content-type": "application/json", **(headers or {})})


class AtlassianApiStub:
    """Routes requests by method and path; records every request it sees.

    A route's response may be a JSON payload, an ``httpx.Response``, a callable
    taking the request, or a list consumed one item per call (the last item
    repeats), which is how pagination and "fail once, then succeed" are staged.
    Unrouted requests get a 404 and are kept in ``unrouted`` so a test that
    silently hit an unexpected endpoint can be spotted.
    """

    def __init__(self) -> None:
        self._routes: list[tuple[str, str, object]] = []
        self._suffix_routes: list[tuple[str, str, object]] = []
        self.requests: list[httpx.Request] = []
        self.unrouted: list[str] = []

    def on(self, method: str, path: str, response: object) -> "AtlassianApiStub":
        self._routes.insert(0, (method.upper(), path, response))
        return self

    def on_suffix(self, method: str, suffix: str, response: object) -> "AtlassianApiStub":
        """Fallback for any path ending in ``suffix``; exact routes win."""
        self._suffix_routes.insert(0, (method.upper(), suffix, response))
        return self

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method.upper() and r.url.path == path]

    @staticmethod
    def query(request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query, keep_blank_values=True).items()}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for method, path, response in self._routes:
            if method == request.method and path == request.url.path:
                return self._render(response, request)
        for method, suffix, response in self._suffix_routes:
            if method == request.method and request.url.path.endswith(suffix):
                return self._render(response, request)
        self.unrouted.append(f"{request.method} {request.url}")
        return json_response({"message": "not stubbed"}, status=404)

    def _render(self, response: object, request: httpx.Request) -> httpx.Response:
        if isinstance(response, list):
            item = response.pop(0) if len(response) > 1 else response[0]
            return self._render(item, request)
        if isinstance(response, httpx.Response):
            return httpx.Response(response.status_code, headers=response.headers, content=response.content)
        if callable(response):
            return response(request)
        return json_response(response)

    def install(self, http_client: HTTPClient) -> None:
        """Point a real ``HTTPClient`` at this stub instead of the network."""
        http_client.client = httpx.AsyncClient(
            transport=httpx.MockTransport(self),
            headers=http_client.headers,
            follow_redirects=True,
        )


class FakeRecordsDb:
    """In-memory stand-in for ``DataSourceEntitiesProcessor``.

    Stores what the connector writes and answers its lookups from that store,
    so a second sync run sees records written by the first. Only the methods
    the Atlassian connectors call are implemented; anything else raises.
    """

    def __init__(self, org_id: str = "org-1", creator_email: Optional[str] = "owner@example.com") -> None:
        self.org_id = org_id
        self.creator_email = creator_email
        self.records: dict[str, Any] = {}
        self.record_permissions: dict[str, list[Any]] = {}
        self.record_groups: dict[str, Any] = {}
        self.record_group_permissions: dict[str, list[Any]] = {}
        self.record_batches: list[list[Any]] = []
        self.app_users: list[Any] = []
        self.user_groups: list[tuple[Any, list[Any]]] = []
        self.deleted: list[str] = []
        self.content_updates: list[Any] = []
        self.permission_updates: list[tuple[Any, list[Any]]] = []
        self.reindexed: list[Any] = []
        self.fail_lookup_for: set[str] = set()

    async def get_user_by_user_id(self, user_id: str) -> Optional[SimpleNamespace]:
        if not self.creator_email:
            return None
        return SimpleNamespace(email=self.creator_email, id=user_id)

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Optional[Record]:
        if external_record_id in self.fail_lookup_for:
            raise RuntimeError(f"database unavailable for {external_record_id}")
        return self.records.get(external_record_id)

    async def get_records_by_parent(
        self, connector_id: str, parent_external_record_id: str, record_type: Optional[str] = None
    ) -> list[Record]:
        """Copies, as a real read would return: changing them does not change what is stored."""
        return [
            r.model_copy() for r in self.records.values()
            if r.parent_external_record_id == parent_external_record_id and record_type in (None, r.record_type)
        ]

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        self.record_batches.append([rec for rec, _ in records_with_permissions])
        for record, permissions in records_with_permissions:
            self.records[record.external_record_id] = record
            self.record_permissions[record.external_record_id] = list(permissions)

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in groups:
            self.record_groups[group.external_group_id] = group
            self.record_group_permissions[group.external_group_id] = list(permissions)

    async def on_new_app_users(self, users: list[Any]) -> None:
        self.app_users.extend(users)

    async def on_new_user_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        self.user_groups.extend(groups)

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        self.deleted.append(record_id)

    async def on_record_content_update(self, record: Record) -> None:
        self.content_updates.append(record)

    async def on_updated_record_permissions(self, record: Record, permissions: list[Any]) -> None:
        self.permission_updates.append((record, permissions))

    async def reindex_existing_records(self, records: list[Any]) -> None:
        self.reindexed.extend(records)


class FakeCheckpointStore:
    """In-memory sync-point collection behind ``DataStoreProvider.transaction()``."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> Optional[dict[str, Any]]:
        return self.sync_points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.sync_points[key] = dict(data)

    async def delete_sync_point(self, key: str) -> None:
        self.sync_points.pop(key, None)

    def values_for(self, key_suffix: str) -> Optional[dict[str, Any]]:
        for key, value in self.sync_points.items():
            if key.endswith(key_suffix):
                return value
        return None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["FakeCheckpointStore"]:
        yield self


class FakeConfigService:
    """Serves one connector's etcd config document (auth + filters)."""

    def __init__(self, connector_id: str, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = config

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        if path == f"/services/connectors/{self.connector_id}/config":
            return self.config
        return default
