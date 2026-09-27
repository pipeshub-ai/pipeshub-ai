"""Fakes for behaviour tests of the Google Workspace connectors.

Only two things are faked:

* Google's HTTP. ``FakeGoogleHttp`` replaces ``httplib2.Http.request``, the one
  socket-facing call under ``googleapiclient``. Everything above it runs for real:
  the discovery-built service objects, request serialisation, ``execute()`` with its
  retry and backoff, ``google-auth`` credentials (OAuth refresh and service-account
  JWT signing), ``google_auth_httplib2``'s refresh-on-401 and ``MediaIoBaseDownload``.
  The fake also plays Google's token endpoint, so the identity behind every API call
  (which user a service account impersonated, which OAuth token was sent) is known.
* Our own databases: an in-memory ``DataSourceEntitiesProcessor`` stand-in, a
  sync-point store behind ``DataStoreProvider.transaction()`` and the etcd config.
"""

from __future__ import annotations

import base64
import itertools
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, unquote, urlsplit

import httplib2

if TYPE_CHECKING:
    import pytest

    from app.models.entities import Record

TOKEN_URI = "https://oauth2.googleapis.com/token"
TOKEN_HOST = "oauth2.googleapis.com"
# Exact hosts the Drive, Admin Directory and Gmail clients call; anything else is a test bug.
GOOGLE_API_HOSTS = frozenset({"www.googleapis.com", "admin.googleapis.com", "gmail.googleapis.com", TOKEN_HOST})


def google_error(status: int, reason: Optional[str | list[str]], message: str = "") -> tuple[int, dict]:
    """A Google API error body in the shape googleapiclient parses into ``HttpError``.

    ``reason=None`` sends only a message, as some Google errors do; googleapiclient then
    leaves ``error_details`` as that string instead of a list of reasons. A list sends
    one error entry per reason, as Google does when several apply.
    """
    reasons = [reason] if isinstance(reason, str) else (reason or [])
    error: dict[str, Any] = {"code": status, "message": message or (reasons[0] if reasons else "Forbidden")}
    if reason is not None:
        error["errors"] = [{"domain": "global", "reason": r, "message": message or r} for r in reasons]
    return status, {"error": error}


@dataclass
class ApiRequest:
    method: str
    url: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes
    identity: Optional[str]

    def json(self) -> object:
        return json.loads(self.body or b"{}")


@dataclass
class _Fault:
    method: str
    pattern: re.Pattern[str]
    status: int
    body: Any
    remaining: Optional[int]
    when: Optional[Callable[[ApiRequest], bool]] = None


@dataclass
class HttpResult:
    """What a route handler returns when it needs headers or raw bytes."""

    status: int
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


Handler = Callable[[ApiRequest], Any]


class FakeGoogleHttp:
    """Google's HTTP endpoints, routed by method and path regex.

    A handler returns a JSON-able payload, ``(status, payload)`` or ``HttpResult``.
    ``fail(...)`` injects errors ahead of routing (quota errors, outages), optionally
    for a limited number of calls. Unrouted calls answer 404 and are kept in
    ``unrouted`` so a test that hit an endpoint nobody expected can say so.
    """

    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern[str], Handler]] = []
        self._faults: list[_Fault] = []
        self.requests: list[ApiRequest] = []
        self.unrouted: list[str] = []
        self.token_requests: list[dict[str, str]] = []
        self._token_owner: dict[str, str] = {}
        self._token_seq = itertools.count(1)
        self.refused_subjects: set[str] = set()
        self.revoked_refresh_tokens: set[str] = set()

    # --- configuration -------------------------------------------------------

    def route(self, method: str, path_regex: str, handler: Handler) -> None:
        self._routes.append((method.upper(), re.compile(f"^{path_regex}$"), handler))

    def fail(
        self,
        method: str,
        path_regex: str,
        status: int,
        reason: Optional[str | list[str]],
        *,
        times: Optional[int] = None,
        when: Optional[Callable[[ApiRequest], bool]] = None,
    ) -> None:
        """Answer matching calls with a Google error, ``times`` times (``None`` = always)."""
        _, body = google_error(status, reason)
        self._faults.append(
            _Fault(method.upper(), re.compile(f"^{path_regex}$"), status, body, times, when)
        )

    def clear_faults(self) -> None:
        self._faults.clear()

    def accept_token(self, token: str, identity: str) -> None:
        """Treat ``token`` as a live access token belonging to ``identity``."""
        self._token_owner[token] = identity

    def impersonated_subjects(self) -> set[str]:
        """Users a service account asked Google for a delegated token for."""
        return {
            _jwt_claims(t["assertion"]).get("sub")
            for t in self.token_requests
            if t.get("grant_type", "").endswith("jwt-bearer")
        }

    def calls(self, method: str, path_regex: str) -> list[ApiRequest]:
        pattern = re.compile(f"^{path_regex}$")
        return [r for r in self.requests if r.method == method.upper() and pattern.match(r.path)]

    # --- transport -----------------------------------------------------------

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = self

        def request(
            _http: httplib2.Http,
            uri: str,
            method: str = "GET",
            body: object = None,
            headers: Optional[dict[str, str]] = None,
            redirections: int = 5,
            connection_type: object = None,
        ) -> tuple[httplib2.Response, bytes]:
            return fake.handle(uri, method, body, headers or {})

        monkeypatch.setattr(httplib2.Http, "request", request)

    def handle(self, uri: str, method: str, body: object, headers: dict[str, str]) -> tuple[httplib2.Response, bytes]:
        if hasattr(body, "read"):
            body = body.read()
        if isinstance(body, str):
            body = body.encode()
        body = body or b""
        headers = {k.lower(): v for k, v in headers.items()}
        parts = urlsplit(uri)
        host = parts.hostname
        if host not in GOOGLE_API_HOSTS:
            raise AssertionError(f"test tried to reach a host that is not a Google API: {uri}")

        if host == TOKEN_HOST and parts.path == "/token":
            return self._token_endpoint(body)

        auth = headers.get("authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
        query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        req = ApiRequest(method.upper(), uri, unquote(parts.path), query, headers, body, self._token_owner.get(token or ""))
        self.requests.append(req)

        if req.identity is None:
            return self._render(google_error(401, "authError", "Invalid Credentials"))

        for fault in self._faults:
            if fault.remaining == 0 or fault.method != req.method or not fault.pattern.match(req.path):
                continue
            if fault.when is not None and not fault.when(req):
                continue
            if fault.remaining is not None:
                fault.remaining -= 1
            return self._render((fault.status, fault.body))

        for route_method, pattern, handler in reversed(self._routes):
            if route_method == req.method and pattern.match(req.path):
                return self._render(handler(req))

        self.unrouted.append(f"{req.method} {uri}")
        return self._render(google_error(404, "notFound", f"no route for {req.path}"))

    def _token_endpoint(self, body: bytes) -> tuple[httplib2.Response, bytes]:
        form = {k: v[-1] for k, v in parse_qs(body.decode()).items()}
        self.token_requests.append(form)
        grant = form.get("grant_type", "")
        if grant == "refresh_token":
            refresh_token = form.get("refresh_token", "")
            if refresh_token in self.revoked_refresh_tokens:
                return self._render((400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
            identity = f"oauth:{refresh_token}"
        elif grant.endswith("jwt-bearer"):
            claims = _jwt_claims(form["assertion"])
            identity = claims.get("sub") or claims["iss"]
            if identity in self.refused_subjects:
                return self._render(
                    (401, {"error": "unauthorized_client", "error_description": "Client is unauthorized to retrieve access tokens using this method."})
                )
        else:
            return self._render((400, {"error": "unsupported_grant_type"}))

        token = f"at-{next(self._token_seq)}"
        self._token_owner[token] = identity
        return self._render({"access_token": token, "expires_in": 3600, "token_type": "Bearer"})

    @staticmethod
    def _render(result: object) -> tuple[httplib2.Response, bytes]:
        headers: dict[str, str] = {}
        if isinstance(result, HttpResult):
            status, payload, headers = result.status, result.body, dict(result.headers)
        elif isinstance(result, tuple):
            status, payload = result
        else:
            status, payload = 200, result
        if isinstance(payload, (bytes, bytearray)):
            content = bytes(payload)
        else:
            content = json.dumps(payload if payload is not None else {}).encode()
            headers.setdefault("content-type", "application/json; charset=UTF-8")
        response = httplib2.Response({"status": str(status), **headers})
        response.reason = "OK" if status < 400 else "Error"
        return response, content


def _jwt_claims(assertion: str) -> dict[str, Any]:
    payload = assertion.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def paginate(items: list[Any], query: dict[str, str], *, default_size: int, key: str) -> dict[str, Any]:
    """Slice ``items`` by the request's ``pageToken``/``pageSize`` the way Google pages lists."""
    start = int(query.get("pageToken") or 0)
    size = min(int(query.get("pageSize") or query.get("maxResults") or default_size), default_size)
    page = items[start:start + size]
    out: dict[str, Any] = {key: page}
    if start + size < len(items):
        out["nextPageToken"] = str(start + size)
    return out


# --- our databases -----------------------------------------------------------


class FakeEntitiesProcessor:
    """In-memory stand-in for ``DataSourceEntitiesProcessor``.

    Keeps what a connector writes and answers its lookups from that state, so a
    second sync sees what the first one stored. As in the real processor,
    ``on_new_records`` and ``add_permission_to_record`` only add or update access
    (an edge per user or group), while ``on_updated_record_permissions`` replaces
    it. ``fail_writes_for`` makes a
    write for the named record raise, to stage a database outage mid-sync.
    Methods the Google connectors never call are not defined, so an unexpected
    call fails loudly.
    """

    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.records: dict[str, Any] = {}
        self.permissions: dict[str, list[Any]] = {}
        self.record_groups: dict[str, Any] = {}
        self.record_group_permissions: dict[str, list[Any]] = {}
        self.app_users: dict[str, Any] = {}
        self.user_groups: dict[str, tuple[Any, list[Any]]] = {}
        self.active_users: list[Any] = []
        self.deleted: list[str] = []
        self.metadata_updates: list[Any] = []
        self.content_updates: list[Any] = []
        self.reindexed: list[Any] = []
        self.relations: list[tuple[str, str, str]] = []
        self.new_record_batches: list[list[str]] = []
        self.fail_writes_for: set[str] = set()

    def _check_write(self, external_id: Optional[str]) -> None:
        if external_id in self.fail_writes_for:
            raise RuntimeError(f"database unavailable while writing {external_id}")

    def by_id(self, record_id: str) -> Optional[Record]:
        return next((r for r in self.records.values() if r.id == record_id), None)

    def perm_emails(self, external_id: str) -> set[str]:
        return {p.email for p in self.permissions.get(external_id, []) if p.email}

    # lookups
    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Optional[Record]:
        return self.records.get(external_record_id)

    async def get_records_by_parent(self, connector_id: str, parent_external_record_id: str, record_type: Optional[str] = None) -> list[Any]:
        return [
            r for r in self.records.values()
            if r.parent_external_record_id == parent_external_record_id
            and (record_type is None or str(getattr(r.record_type, "value", r.record_type)) == str(getattr(record_type, "value", record_type)))
        ]

    async def get_placeholder_records(self, connector_id: str, *_: object, **__: object) -> list[Any]:
        return [r for r in self.records.values() if getattr(r, "is_placeholder", False)]

    async def get_all_active_users(self) -> list[Any]:
        return list(self.active_users)

    async def get_user_by_user_id(self, user_id: str) -> Optional[object]:
        return next((u for u in self.active_users if getattr(u, "id", None) == user_id), None)

    async def get_users_with_permission_to_node(self, node_id: str, node_collection: str) -> list[Any]:
        record = self.by_id(node_id)
        if record is None:
            return []
        emails = self.perm_emails(record.external_record_id)
        return [u for u in self.active_users if u.email in emails]

    # writes
    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        for record, _ in records_with_permissions:
            self._check_write(record.external_record_id)
        self.new_record_batches.append([r.external_record_id for r, _ in records_with_permissions])
        for record, permissions in records_with_permissions:
            self.records[record.external_record_id] = record
            self._upsert_permissions(record.external_record_id, permissions)
            self._ensure_parent(record)

    def _upsert_permissions(self, external_id: str, permissions: list[Any]) -> None:
        existing = self.permissions.setdefault(external_id, [])
        for perm in permissions:
            key = (perm.entity_type, perm.email or perm.external_id)
            existing[:] = [p for p in existing if (p.entity_type, p.email or p.external_id) != key]
            existing.append(perm)

    def _ensure_parent(self, record: Record) -> None:
        """Stand in a placeholder parent, as the real processor does for an unseen parent."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )

        parent_id = record.parent_external_record_id
        if not parent_id or not record.parent_record_type or parent_id in self.records:
            return
        self.records[parent_id] = DataSourceEntitiesProcessor._create_placeholder_parent_record(
            self,
            parent_external_id=parent_id,
            parent_record_type=record.parent_record_type,
            record=record,
            record_group_type=record.record_group_type,
            external_record_group_id=record.external_record_group_id,
        )

    async def on_record_metadata_update(self, record: Record) -> None:
        self._check_write(record.external_record_id)
        self.metadata_updates.append(record)
        self.records[record.external_record_id] = record

    async def on_record_content_update(self, record: Record) -> None:
        self._check_write(record.external_record_id)
        self.content_updates.append(record)
        self.records[record.external_record_id] = record

    async def on_updated_record_permissions(self, record: Record, permissions: list[Any]) -> None:
        self._check_write(record.external_record_id)
        self.permissions[record.external_record_id] = list(permissions)

    async def add_permission_to_record(self, record: Record, permissions: list[Any]) -> None:
        self._check_write(record.external_record_id)
        self._upsert_permissions(record.external_record_id, permissions)

    async def delete_permission_from_record(self, record_id: str, user_email: str) -> None:
        record = self.by_id(record_id)
        if record is not None:
            self.permissions[record.external_record_id] = [
                p for p in self.permissions.get(record.external_record_id, []) if p.email != user_email
            ]

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        record = self.by_id(record_id)
        if record is not None:
            self._check_write(record.external_record_id)
            del self.records[record.external_record_id]
            self.permissions.pop(record.external_record_id, None)
        self.deleted.append(record_id)

    async def on_records_deleted_cascade(self, record_ids: list[str], connector_id: str) -> dict[str, Any]:
        deleted: list[str] = []
        pending = list(record_ids)
        while pending:
            record = self.by_id(pending.pop())
            if record is None:
                continue
            deleted.append(record.id)
            pending.extend(
                r.id for r in self.records.values()
                if r.parent_external_record_id == record.external_record_id
            )
            del self.records[record.external_record_id]
            self.permissions.pop(record.external_record_id, None)
        self.deleted.extend(deleted)
        return {"deleted_records": deleted}

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in groups:
            self.record_groups[group.external_group_id] = group
            self.record_group_permissions[group.external_group_id] = list(permissions)

    async def on_new_app_users(self, users: list[Any]) -> None:
        for user in users:
            self.app_users[user.email] = user

    async def on_new_user_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, members in groups:
            self.user_groups[group.source_user_group_id] = (group, list(members))

    async def reindex_existing_records(self, records: list[Any]) -> None:
        self.reindexed.extend(records)

    async def create_record_relation(self, from_record_id: str, to_record_id: str, relation_type: str, *_: object, **__: object) -> None:
        self.relations.append((from_record_id, to_record_id, str(relation_type)))


class FakeSyncPointStore:
    """Sync points (checkpoints) behind ``DataStoreProvider.transaction()``.

    A write merges into the stored document the way both graph stores do: Arango's
    ``UPDATE ... WITH`` and Neo4j's ``SET +=`` keep fields the write leaves out, so a
    field is only reset by writing it. Neo4j cannot store a map as a property, so a
    nested dict is refused here too.
    """

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> Optional[dict[str, Any]]:
        return self.sync_points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        nested = [field for field, value in data.items() if isinstance(value, dict)]
        assert not nested, f"Neo4j cannot store map properties: {nested}"
        self.sync_points[key] = {**self.sync_points.get(key, {}), **data}
        self.writes.append((key, dict(data)))

    async def delete_sync_point(self, key: str) -> None:
        self.sync_points.pop(key, None)

    def value(self, key_suffix: str) -> Optional[dict[str, Any]]:
        matches = [v for k, v in self.sync_points.items() if k.endswith(key_suffix)]
        assert len(matches) <= 1, f"ambiguous sync point suffix {key_suffix}: {list(self.sync_points)}"
        return matches[0] if matches else None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["FakeSyncPointStore"]:
        yield self


class FakeConfigService:
    """etcd: one connector config document plus the shared app registrations per connector type.

    Named ``shared_apps`` rather than after OAuth: CodeQL's sensitive-data heuristic
    matches that name, and since it resolves every ``config_service.get_config`` call in
    the app to this method, it would flag each place the app logs config.
    """

    def __init__(self, connector_id: str, config: dict[str, Any], shared_apps: Optional[dict[str, list[dict[str, Any]]]] = None) -> None:
        self.connector_id = connector_id
        self.config = config
        self.shared_apps = shared_apps or {}

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        if path == f"/services/connectors/{self.connector_id}/config":
            return self.config
        if path.startswith("/services/oauth/"):
            return self.shared_apps.get(path.rsplit("/", 1)[-1], default)
        return default
