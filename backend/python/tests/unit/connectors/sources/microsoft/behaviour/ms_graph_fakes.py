"""Fakes for behaviour tests of the Microsoft 365 connectors.

Only Microsoft's HTTP endpoints and our own databases are faked. The Graph SDK
(``GraphServiceClient``, its kiota request adapter and retry middleware) and
the Azure ``ClientSecretCredential`` are real; their HTTP traffic is answered by
``MicrosoftCloudStub``, one in-memory server for both ``graph.microsoft.com``
and ``login.microsoftonline.com``. Records, groups and sync points live in
memory so a second sync sees what the first one wrote.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import io
import json
import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import requests
from azure.core.pipeline.transport import AsyncioRequestsTransport
from azure.identity.aio import ClientSecretCredential
from kiota_http.kiota_client_factory import KiotaClientFactory
from msgraph_core.middleware.async_graph_transport import AsyncGraphTransport
from packaging.version import Version
from requests.structures import CaseInsensitiveDict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.models.entities import Record

GRAPH = "https://graph.microsoft.com/v1.0"
TENANT = "tenant-1"
TOKEN_PATH = f"/{TENANT}/oauth2/v2.0/token"

# kiota-http 1.13 moved request options from ``request.options`` to ``request.extensions``,
# but msgraph-core 1.5.1 still runs its middleware (retry, redirect, URL replace) only
# when ``request.options`` exists, so with both installed no Graph request is retried.
GRAPH_MIDDLEWARE_SKIPPED = Version(importlib.metadata.version("microsoft-kiota-http")) >= Version("1.13") and (
    "hasattr(request, 'options')" in inspect.getsource(AsyncGraphTransport.handle_async_request)
)
graph_retry_skipped = pytest.mark.xfail(
    GRAPH_MIDDLEWARE_SKIPPED,
    strict=True,
    reason=(
        "With microsoft-kiota-http 1.13 or later next to msgraph-core 1.5.1, the Graph SDK skips its "
        "retry middleware, so a throttled (429) or unavailable (503) request fails at once instead of "
        "waiting and retrying. pyproject.toml does not pin kiota-http."
    ),
)


def json_response(payload: object, status: int = 200, headers: Optional[dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(payload).encode(), headers={"content-type": "application/json", **(headers or {})}
    )


def graph_error(status: int, code: str, message: str = "error", headers: Optional[dict[str, str]] = None) -> httpx.Response:
    return json_response({"error": {"code": code, "message": message}}, status=status, headers=headers)


def page(values: list, *, next_link: Optional[str] = None, delta_link: Optional[str] = None) -> dict[str, Any]:
    body: dict[str, Any] = {"value": values}
    if next_link:
        body["@odata.nextLink"] = next_link
    if delta_link:
        body["@odata.deltaLink"] = delta_link
    return body


class MicrosoftCloudStub:
    """Routes requests by method and path (host ignored); records every request.

    A route's response may be a JSON payload, an ``httpx.Response``, a callable
    taking the request, or a list consumed one item per call (the last item
    repeats), which is how "fail once, then succeed" is staged. Unrouted
    requests get a 404 and are kept in ``unrouted``.
    """

    def __init__(self) -> None:
        self._routes: list[tuple[str, str, object]] = []
        self.requests: list[httpx.Request] = []
        self.unrouted: list[str] = []
        self.tokens_issued = 0
        self.token_lifetime_s = 3600
        self.token_failure: Optional[httpx.Response] = None
        self.on("POST", TOKEN_PATH, self._issue_token)

    def on(self, method: str, path: str, response: object) -> "MicrosoftCloudStub":
        self._routes.insert(0, (method.upper(), path, response))
        return self

    @staticmethod
    def path_of(request: httpx.Request) -> str:
        # The SDK joins its base URL and path templates with a doubled slash; Graph accepts both.
        return re.sub("/+", "/", request.url.path)

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method.upper() and self.path_of(r) == path]

    def token_requests(self) -> list[dict[str, str]]:
        """The form fields of every sign-in request, parsed rather than searched as text."""
        return [
            {k: v[0] for k, v in parse_qs(r.content.decode(), keep_blank_values=True).items()}
            for r in self.calls("POST", TOKEN_PATH)
        ]

    def graph_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "graph.microsoft.com"]

    @staticmethod
    def query(request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query, keep_blank_values=True).items()}

    def _issue_token(self, request: httpx.Request) -> httpx.Response:
        if self.token_failure is not None:
            return self.token_failure
        self.tokens_issued += 1
        return json_response({
            "token_type": "Bearer",
            "expires_in": self.token_lifetime_s,
            "ext_expires_in": self.token_lifetime_s,
            "access_token": f"fake-graph-token-{self.tokens_issued}",
        })

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for method, path, response in self._routes:
            if method == request.method and path == self.path_of(request):
                return self._render(response, request)
        self.unrouted.append(f"{request.method} {request.url}")
        return graph_error(404, "itemNotFound", "not stubbed")

    def _render(self, response: object, request: httpx.Request) -> httpx.Response:
        if isinstance(response, list):
            item = response.pop(0) if len(response) > 1 else response[0]
            return self._render(item, request)
        if isinstance(response, httpx.Response):
            return httpx.Response(response.status_code, headers=response.headers, content=response.content)
        if callable(response):
            return response(request)
        return json_response(response)


class _StubAdapter(requests.adapters.BaseAdapter):
    """Lets azure-core's real requests transport talk to the stub."""

    def __init__(self, stub: MicrosoftCloudStub) -> None:
        super().__init__()
        self.stub = stub

    def send(self, request: requests.PreparedRequest, **_: object) -> requests.Response:
        body = request.body or b""
        answer = self.stub(httpx.Request(
            request.method or "GET", request.url or "", headers=dict(request.headers),
            content=body.encode() if isinstance(body, str) else body,
        ))
        response = requests.Response()
        response.status_code = answer.status_code
        response.headers = CaseInsensitiveDict(answer.headers)
        response._content = answer.content
        response.raw = io.BytesIO(answer.content)
        response.encoding = "utf-8"
        response.url = request.url or ""
        response.request = request
        return response

    def close(self) -> None:
        return None


def route_microsoft_http(monkeypatch: pytest.MonkeyPatch, stub: MicrosoftCloudStub, *credential_modules: str) -> None:
    """Point every Graph SDK client, and the credential in each module, at the stub."""

    def _default_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(stub))

    monkeypatch.setattr(KiotaClientFactory, "get_default_client", staticmethod(_default_client))

    def _credential(**kwargs: str) -> ClientSecretCredential:
        session = requests.Session()
        session.mount("https://", _StubAdapter(stub))
        return ClientSecretCredential(**kwargs, transport=AsyncioRequestsTransport(session=session))

    for module in credential_modules:
        monkeypatch.setattr(f"{module}.ClientSecretCredential", _credential)


def bearer(request: httpx.Request) -> str:
    return request.headers.get("Authorization", "")


class FakeCheckpointStore:
    """In-memory sync points with ArangoDB ``UPDATE`` (merge) semantics."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> Optional[dict[str, Any]]:
        stored = self.sync_points.get(key)
        return dict(stored) if stored is not None else None

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.writes.append((key, dict(data)))
        self.sync_points.setdefault(key, {}).update(data)

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
    def __init__(self, connector_id: str, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = config

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        if path == f"/services/connectors/{self.connector_id}/config":
            return self.config
        return default


class RecordingNotifications:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def publish_notification(self, **kwargs: object) -> None:
        self.sent.append(kwargs)



class FakeRecordsDb:
    """In-memory stand-in for ``DataSourceEntitiesProcessor``.

    Stores what the connector writes and answers its lookups from that store.
    Only the methods the Microsoft connectors call are implemented.
    """

    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.records: dict[str, Any] = {}
        self.record_permissions: dict[str, list[Any]] = {}
        self.record_batches: list[list[Any]] = []
        self.record_groups: dict[str, Any] = {}
        self.record_group_permissions: dict[str, list[Any]] = {}
        self.app_users: list[Any] = []
        self.active_users: list[Any] = []
        self.user_groups: dict[str, list[Any]] = {}
        self.user_group_writes: list[tuple[Any, list[Any]]] = []
        self.deleted_groups: list[str] = []
        self.fail_group_delete: set[str] = set()
        self.removed_members: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.metadata_updates: list[Any] = []
        self.content_updates: list[Any] = []
        self.permission_updates: list[tuple[Any, list[Any]]] = []
        self.reindexed: list[Any] = []

    def add_active_user(self, email: str) -> None:
        self.active_users.append(type("ActiveUser", (), {"email": email})())

    def seed_record(self, record: Record, permissions: Optional[list[Any]] = None) -> None:
        self.records[record.external_record_id] = record
        self.record_permissions[record.external_record_id] = list(permissions or [])

    def by_name(self, name: str) -> Record:
        matches = [r for r in self.records.values() if r.record_name == name]
        assert len(matches) == 1, f"expected one record named {name!r}, found {len(matches)}"
        return matches[0]

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Optional[Record]:
        return self.records.get(external_record_id)

    async def get_records_by_parent(self, connector_id: str, parent_external_record_id: str, record_type: Optional[str] = None) -> list[Record]:
        """Children as base ``Record`` objects, the way ``Record.from_arango_base_record`` builds them.

        Production returns no subclass fields (no ``is_file``), so neither does this.
        """
        from app.models.entities import Record

        children = []
        for stored in self.records.values():
            if stored.parent_external_record_id != parent_external_record_id:
                continue
            stored_type = getattr(stored.record_type, "value", stored.record_type)
            if record_type and stored_type != record_type:
                continue
            children.append(Record.model_validate(stored.model_dump(include=set(Record.model_fields))))
        return children

    async def get_file_record_by_id(self, record_id: str) -> Optional[Record]:
        return next((r for r in self.records.values() if r.id == record_id), None)

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        self.record_batches.append([rec for rec, _ in records_with_permissions])
        for record, permissions in records_with_permissions:
            self.records[record.external_record_id] = record
            self.record_permissions[record.external_record_id] = list(permissions)

    async def on_record_metadata_update(self, record: Record) -> None:
        self.metadata_updates.append(record)
        self.records[record.external_record_id] = record

    async def on_record_content_update(self, record: Record) -> None:
        self.content_updates.append(record)
        self.records[record.external_record_id] = record

    async def on_updated_record_permissions(self, record: Record, permissions: list[Any]) -> None:
        self.permission_updates.append((record, list(permissions)))
        self.record_permissions[record.external_record_id] = list(permissions)

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        self.deleted.append(record_id)
        for external_id, record in list(self.records.items()):
            if record.id == record_id:
                del self.records[external_id]
                self.record_permissions.pop(external_id, None)

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in groups:
            self.record_groups[group.external_group_id] = group
            self.record_group_permissions[group.external_group_id] = list(permissions)

    async def on_new_app_users(self, users: list[Any]) -> None:
        self.app_users.extend(users)

    async def get_all_active_users(self) -> list[Any]:
        return list(self.active_users)

    async def on_new_user_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, members in groups:
            self.user_group_writes.append((group, list(members)))
            self.user_groups[group.source_user_group_id] = [m.email for m in members]

    async def on_user_group_deleted(self, external_group_id: str, connector_id: str) -> bool:
        if external_group_id in self.fail_group_delete:
            return False
        self.deleted_groups.append(external_group_id)
        self.user_groups.pop(external_group_id, None)
        return True

    async def on_user_group_member_removed(self, external_group_id: str, user_email: str, connector_id: str) -> bool:
        self.removed_members.append((external_group_id, user_email))
        members = self.user_groups.get(external_group_id, [])
        if user_email in members:
            members.remove(user_email)
        return True

    async def reindex_existing_records(self, records: list[Any]) -> None:
        self.reindexed.extend(records)
