"""Fakes for behaviour tests of the Box connector.

Only two things are faked: Box's HTTP API and our own databases. The connector,
``BoxDataSource`` and the Box SDK (``box_sdk_gen``) all run for real, including
the SDK's retry strategy, its client-credentials token fetch and its refresh on
401. The SDK's ``requests`` session is handed a transport adapter that answers
from a small in-memory Box enterprise, so no request can reach the network.
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.connectors.sources.box.connector import BoxConnector
    from app.models.entities import AppUser, Record

ROOT_ID = "0"
SERVICE_ACCOUNT_ID = "svc-1"


@dataclass
class SeenRequest:
    method: str
    path: str
    query: dict[str, str]
    as_user: str | None
    token: str | None


@dataclass
class Fault:
    method: str
    path: str
    status: int
    remaining: int
    headers: dict[str, str]
    as_user: str | None = None
    query: dict[str, str] | None = None


class FakeBoxApi(BaseAdapter):
    """A tiny Box enterprise behind the Box REST paths the connector uses.

    Content is owned by users and is visible only to its owner or to a
    collaborator on it or on a folder above it, which is how Box answers an
    ``As-User`` request. Listing endpoints page the way Box does (offset/limit
    with Box's default page of 100, or a marker for file collaborations), and
    ``fail`` queues error responses for the next matching requests.
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.users: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.memberships: dict[str, list[str]] = {}
        self.items: dict[str, dict[str, Any]] = {}
        self.collaborations: dict[str, list[dict[str, Any]]] = {}
        self.events: list[dict[str, Any]] = []
        self.faults: list[Fault] = []
        self.requests: list[SeenRequest] = []
        self.token_forms: list[dict[str, str]] = []
        self.valid_tokens: set[str] = set()
        self._collab_seq = 0
        # Box's page sizes; tests shrink them to reach later pages with a handful of items.
        self.default_page = 100
        self.max_page = 1000
        self.page_cap: dict[str, int] = {}
        self._cap = self.max_page

    # ---- building the enterprise -------------------------------------------------

    def add_user(self, user_id: str, login: str, name: str = "", status: str = "active") -> dict[str, Any]:
        user = {"type": "user", "id": user_id, "login": login, "name": name or login.split("@")[0], "status": status}
        self.users[user_id] = user
        return user

    def add_group(self, group_id: str, name: str, member_ids: tuple[str, ...] = ()) -> None:
        self.groups[group_id] = {"type": "group", "id": group_id, "name": name, "group_type": "managed_group"}
        self.memberships[group_id] = list(member_ids)

    def add_folder(self, folder_id: str, name: str, owner: str, parent: str = ROOT_ID) -> None:
        self.items[folder_id] = {
            "type": "folder", "id": folder_id, "name": name, "owner": owner, "parent": parent,
            "created_at": "2024-01-01T00:00:00Z", "modified_at": "2024-01-02T00:00:00Z", "etag": "0",
        }

    def add_file(
        self,
        file_id: str,
        name: str,
        owner: str,
        parent: str = ROOT_ID,
        shared_link_access: str | None = None,
        modified_at: str = "2024-02-01T00:00:00Z",
        etag: str = "0",
    ) -> None:
        self.items[file_id] = {
            "type": "file", "id": file_id, "name": name, "owner": owner, "parent": parent, "size": 1024,
            "sha1": f"sha-{file_id}", "etag": etag, "created_at": "2024-01-01T00:00:00Z", "modified_at": modified_at,
            "shared_link_access": shared_link_access,
        }

    def collaborate(self, item_id: str, user_id: str, role: str = "viewer", kind: str = "user") -> str:
        self._collab_seq += 1
        collab_id = f"collab-{self._collab_seq}"
        if kind == "group":
            accessible_by = {"type": "group", "id": user_id, "name": self.groups[user_id]["name"]}
        else:
            accessible_by = {"type": "user", "id": user_id, "login": self.users[user_id]["login"], "name": self.users[user_id]["name"]}
        self.collaborations.setdefault(item_id, []).append(
            {"type": "collaboration", "id": collab_id, "role": role, "status": "accepted", "accessible_by": accessible_by}
        )
        return collab_id

    def add_event(self, event_type: str, source: dict[str, Any], **extra: object) -> None:
        event_id = f"evt-{len(self.events) + 1}"
        self.events.append({
            "type": "event", "event_id": event_id, "event_type": event_type,
            "created_at": f"2024-03-01T00:{len(self.events):02d}:00Z", "source": source, **extra,
        })

    def fail(
        self, method: str, path: str, status: int, times: int = 1, headers: dict[str, str] | None = None,
        as_user: str | None = None, query: dict[str, str] | None = None,
    ) -> None:
        """Answer the next ``times`` matching requests with ``status`` (then behave normally)."""
        self.faults.append(Fault(method.upper(), path, status, times, headers or {}, as_user, query))

    def expire(self, token: str) -> None:
        """Box stops accepting ``token``; the SDK must fetch a new one."""
        self.valid_tokens.discard(token)

    # ---- inspecting traffic ------------------------------------------------------

    def calls(self, method: str, path: str) -> list[SeenRequest]:
        return [r for r in self.requests if r.method == method.upper() and r.path == path]

    @property
    def stream_head(self) -> str:
        return str(len(self.events))

    # ---- transport ---------------------------------------------------------------

    def close(self) -> None:
        return None

    def send(self, request: requests.PreparedRequest, **_: object) -> requests.Response:
        url = urlparse(request.url)
        query = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        auth = request.headers.get("Authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
        as_user = request.headers.get("As-User")
        with self._lock:
            self.requests.append(SeenRequest(request.method, url.path, query, as_user, token))
            if url.path != "/oauth2/token" and token not in self.valid_tokens:
                return self._error(request, 401, "unauthorized")
            for fault in self.faults:
                if (
                    fault.remaining > 0
                    and fault.method == request.method
                    and fault.path == url.path
                    and (fault.as_user is None or fault.as_user == as_user)
                    and all(query.get(k) == v for k, v in (fault.query or {}).items())
                ):
                    fault.remaining -= 1
                    return self._error(request, fault.status, "staged failure", fault.headers)
            if url.path == "/oauth2/token":
                return self._issue_token(request)
            return self._route(request, url.path, query, as_user or SERVICE_ACCOUNT_ID)

    def _issue_token(self, request: requests.PreparedRequest) -> requests.Response:
        body = request.body.decode() if isinstance(request.body, bytes) else (request.body or "")
        form = {k: v[0] for k, v in parse_qs(body).items()}
        self.token_forms.append(form)
        token = f"tok-{len(self.token_forms)}"
        self.valid_tokens.add(token)
        return self._json(request, {"access_token": token, "expires_in": 3600, "token_type": "bearer", "restricted_to": []})

    def _route(self, request: requests.PreparedRequest, path: str, q: dict[str, str], viewer: str) -> requests.Response:
        self._cap = self.page_cap.get(path, self.max_page)
        parts = path.strip("/").split("/")
        if parts[0] != "2.0" or request.method != "GET":
            return self._error(request, 404, "not_found")
        rest = parts[1:]
        match rest:
            case ["users", "me"]:
                return self._json(request, {"type": "user", "id": SERVICE_ACCOUNT_ID, "login": "svc@boxdevedition.com", "name": "PipesHub"})
            case ["users"]:
                return self._json(request, self._offset_page(list(self.users.values()), q))
            case ["users", user_id]:
                user = self.users.get(user_id)
                return self._json(request, user) if user else self._error(request, 404, "not_found")
            case ["groups"]:
                return self._json(request, self._offset_page(list(self.groups.values()), q))
            case ["groups", group_id, "memberships"]:
                members = [
                    {"type": "group_membership", "id": f"m-{group_id}-{uid}", "role": "member",
                     "user": {"type": "user", "id": uid, "login": self.users[uid]["login"], "name": self.users[uid]["name"]},
                     "group": {"type": "group", "id": group_id, "name": self.groups[group_id]["name"]}}
                    for uid in self.memberships.get(group_id, [])
                ]
                return self._json(request, self._offset_page(members, q))
            case ["folders", folder_id]:
                if folder_id == ROOT_ID:
                    return self._json(request, self._root(viewer))
                return self._item_or_404(request, folder_id, "folder", viewer)
            case ["folders", folder_id, "items"]:
                if folder_id != ROOT_ID and not self._visible(folder_id, viewer):
                    return self._error(request, 404, "not_found")
                owner = viewer if folder_id == ROOT_ID else None
                children = [
                    self._render(i) for i in self.items.values()
                    if i["parent"] == folder_id and (owner is None or i["owner"] == owner)
                ]
                return self._json(request, self._offset_page(children, q))
            case ["files", file_id]:
                return self._item_or_404(request, file_id, "file", viewer)
            case ["files", file_id, "content"]:
                if not self._visible(file_id, viewer):
                    return self._error(request, 404, "not_found")
                return self._response(request, 302, None, {"Location": f"https://dl.boxcloud.test/d/{file_id}"})
            case ["files" | "folders", item_id, "collaborations"]:
                if not self._visible(item_id, viewer):
                    return self._error(request, 404, "not_found")
                return self._json(request, self._marker_page(self.collaborations.get(item_id, []), q))
            case ["collaborations", collab_id]:
                for item_id, collabs in self.collaborations.items():
                    for c in collabs:
                        if c["id"] == collab_id:
                            return self._json(request, {**c, "item": {"type": self.items[item_id]["type"], "id": item_id}})
                return self._error(request, 404, "not_found")
            case ["events"]:
                return self._json(request, self._events(q))
        return self._error(request, 404, "not_found")

    # ---- Box semantics -----------------------------------------------------------

    def _visible(self, item_id: str, viewer: str) -> bool:
        item = self.items.get(item_id)
        if item is None:
            return False
        node: dict[str, Any] | None = item
        while node is not None:
            if node["owner"] == viewer:
                return True
            if any(c["accessible_by"]["id"] == viewer for c in self.collaborations.get(node["id"], [])):
                return True
            node = self.items.get(node["parent"])
        return False

    def _root(self, viewer: str) -> dict[str, Any]:
        return {
            "type": "folder", "id": ROOT_ID, "name": "All Files", "etag": None,
            "created_at": "2023-12-01T00:00:00Z", "modified_at": "2024-01-05T00:00:00Z",
            "path_collection": {"total_count": 0, "entries": []},
            "owned_by": self._mini_user(viewer),
        }

    def _mini_user(self, user_id: str) -> dict[str, Any]:
        user = self.users.get(user_id, {"id": user_id, "login": f"{user_id}@box.test", "name": user_id})
        return {"type": "user", "id": user["id"], "login": user["login"], "name": user["name"]}

    def _path(self, item: dict[str, Any]) -> dict[str, Any]:
        chain = []
        parent = item["parent"]
        while parent != ROOT_ID and parent in self.items:
            folder = self.items[parent]
            chain.insert(0, {"type": "folder", "id": folder["id"], "name": folder["name"], "etag": "0"})
            parent = folder["parent"]
        chain.insert(0, {"type": "folder", "id": ROOT_ID, "name": "All Files"})
        return {"total_count": len(chain), "entries": chain}

    def _render(self, item: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in item.items() if k not in {"owner", "parent", "shared_link_access"}}
        out["path_collection"] = self._path(item)
        out["owned_by"] = self._mini_user(item["owner"])
        access = item.get("shared_link_access")
        out["shared_link"] = (
            {"url": f"https://app.box.test/s/{item['id']}", "access": access, "effective_access": access,
             "effective_permission": "can_download", "is_password_enabled": False, "download_count": 0, "preview_count": 0}
            if access else None
        )
        return out

    def _item_or_404(self, request: requests.PreparedRequest, item_id: str, kind: str, viewer: str) -> requests.Response:
        item = self.items.get(item_id)
        if item is None or item["type"] != kind or not self._visible(item_id, viewer):
            return self._error(request, 404, "not_found")
        return self._json(request, self._render(item))

    def _offset_page(self, entries: list[dict[str, Any]], q: dict[str, str]) -> dict[str, Any]:
        limit = min(int(q.get("limit", self.default_page)), self._cap)
        offset = int(q.get("offset", 0))
        return {"total_count": len(entries), "limit": limit, "offset": offset, "entries": entries[offset:offset + limit]}

    def _marker_page(self, entries: list[dict[str, Any]], q: dict[str, str]) -> dict[str, Any]:
        limit = min(int(q.get("limit", self.default_page)), self._cap)
        start = int(q.get("marker") or 0)
        end = start + limit
        return {"limit": limit, "entries": entries[start:end], "next_marker": str(end) if end < len(entries) else None}

    def _events(self, q: dict[str, str]) -> dict[str, Any]:
        position = q.get("stream_position", "0")
        if position == "now":
            return {"chunk_size": 0, "next_stream_position": self.stream_head, "entries": []}
        start = int(position)
        wanted = set(q["event_type"].split(",")) if q.get("event_type") else None
        matching = [
            (i, e) for i, e in enumerate(self.events)
            if i >= start and (wanted is None or e["event_type"] in wanted)
        ][: min(int(q.get("limit", self.default_page)), self._cap)]
        entries = [e for _, e in matching]
        next_position = str(matching[-1][0] + 1) if matching else self.stream_head
        return {"chunk_size": len(entries), "next_stream_position": next_position, "entries": entries}

    # ---- responses ---------------------------------------------------------------

    def _json(self, request: requests.PreparedRequest, payload: object) -> requests.Response:
        return self._response(request, 200, payload)

    def _error(self, request: requests.PreparedRequest, status: int, code: str, headers: dict[str, str] | None = None) -> requests.Response:
        body = {"type": "error", "status": status, "code": code, "message": code.replace("_", " "), "request_id": "req-1"}
        return self._response(request, status, body, headers)

    @staticmethod
    def _response(request: requests.PreparedRequest, status: int, payload: object, headers: dict[str, str] | None = None) -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response.headers = CaseInsensitiveDict({"Content-Type": "application/json", **(headers or {})})
        response._content = json.dumps(payload).encode() if payload is not None else b""
        response.encoding = "utf-8"
        response.url = request.url
        response.request = request
        response.reason = str(status)
        return response


class FakeBoxRecordsDb:
    """In-memory stand-in for ``DataSourceEntitiesProcessor``.

    Mirrors the real processor's write semantics where they matter: record
    permissions are added to what is stored (``on_new_records`` never removes an
    edge) and a group permission is dropped when that group is not stored yet,
    while ``on_new_user_groups`` replaces a stored group's members with the list
    it is given. Only the methods the Box connector calls are implemented.
    """

    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.records: dict[str, Any] = {}
        self.permissions: dict[str, dict[str, Any]] = {}
        self.record_groups: dict[str, Any] = {}
        self.app_users: dict[str, Any] = {}
        self.active_emails: set[str] = set()
        self.user_groups: dict[str, Any] = {}
        self.group_members: dict[str, list[str]] = {}
        self.deleted_groups: list[str] = []
        self.deleted_records: list[str] = []
        self.removed_access: list[tuple[str, str]] = []
        self.record_batches: list[list[Any]] = []
        self.reindexed: list[Any] = []
        self.fail_lookup_for: set[str] = set()
        self.fail_active_users = False

    def access(self, external_id: str) -> set[str]:
        """Who can reach a record directly: user emails and group ids."""
        return {p.email if p.entity_type.value == "USER" else p.external_id for p in self.permissions.get(external_id, {}).values()}

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        if external_record_id in self.fail_lookup_for:
            raise RuntimeError(f"database unavailable for {external_record_id}")
        return self.records.get(external_record_id)

    async def get_records_by_parent(self, connector_id: str, parent_external_record_id: str) -> list[Any]:
        return [r for r in self.records.values() if r.parent_external_record_id == parent_external_record_id]

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        self.record_batches.append([rec for rec, _ in records_with_permissions])
        for record, permissions in records_with_permissions:
            self.records[record.external_record_id] = record
            stored = self.permissions.setdefault(record.external_record_id, {})
            for p in permissions or []:
                if p.entity_type.value == "GROUP" and p.external_id not in self.user_groups:
                    continue
                stored[f"{p.entity_type.value}:{p.external_id}"] = p

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, _ in groups:
            self.record_groups[group.external_group_id] = group

    async def on_new_app_users(self, users: list[Any]) -> None:
        for user in users:
            stored = self.app_users.get(user.email.lower())
            self.app_users[user.email.lower()] = user.model_copy(update={"id": stored.id}) if stored else user

    async def get_all_app_users(self, connector_id: str) -> list[Any]:
        return list(self.app_users.values())

    async def get_app_user_by_email(self, email: str, connector_id: str) -> AppUser | None:
        return self.app_users.get(email.lower())

    async def get_all_active_users(self) -> list[Any]:
        if self.fail_active_users:
            raise RuntimeError("database unavailable")
        return [u for e, u in self.app_users.items() if e in self.active_emails]

    async def on_new_user_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, members in groups:
            self.user_groups[group.source_user_group_id] = group
            self.group_members[group.source_user_group_id] = sorted(m.email for m in members)

    async def get_all_user_groups(self, connector_id: str) -> list[Any]:
        return list(self.user_groups.values())

    async def on_user_group_deleted(self, external_group_id: str, connector_id: str) -> None:
        self.deleted_groups.append(external_group_id)
        self.user_groups.pop(external_group_id, None)
        self.group_members.pop(external_group_id, None)

    async def remove_user_access_to_record(self, connector_id: str, external_id: str, user_id: str) -> None:
        self.removed_access.append((external_id, user_id))
        user = next((u for u in self.app_users.values() if u.id == user_id), None)
        if user:
            stored = self.permissions.get(external_id, {})
            for key in [k for k, p in stored.items() if p.email == user.email]:
                stored.pop(key)

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        self.deleted_records.append(record_id)

    async def reindex_existing_records(self, records: list[Any]) -> None:
        self.reindexed.extend(records)


class FakeCheckpointStore:
    """In-memory sync-point collection behind ``DataStoreProvider.transaction()``."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}
        self.fail_reads = False

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> dict[str, Any] | None:
        if self.fail_reads:
            raise RuntimeError("checkpoint store unavailable")
        return self.sync_points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.sync_points[key] = dict(data)

    async def delete_sync_point(self, key: str) -> None:
        self.sync_points.pop(key, None)

    def cursor(self) -> dict[str, Any] | None:
        for key, value in self.sync_points.items():
            if key.endswith("/records/event_stream_cursor"):
                return value
        return None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["FakeCheckpointStore"]:
        yield self


class FakeConfigService:
    """Serves one connector's etcd config document (CCG auth + filters)."""

    def __init__(self, connector_id: str, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = config

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        if path == f"/services/connectors/{self.connector_id}/config":
            return self.config
        return default


CONNECTOR_ID = "box-1"


def ccg_config(**filters: object) -> dict[str, Any]:
    config: dict[str, Any] = {"auth": {"clientId": "cid", "clientSecret": "csecret", "enterpriseId": "ent-1"}}
    if filters:
        config["filters"] = filters
    return config


async def ready_connector(db: FakeBoxRecordsDb, checkpoints: FakeCheckpointStore, config: dict[str, Any] | None = None) -> BoxConnector:
    from app.connectors.sources.box.connector import BoxConnector

    connector = BoxConnector(
        logging.getLogger("test.box"), db, checkpoints,
        FakeConfigService(CONNECTOR_ID, config or ccg_config()), CONNECTOR_ID, "team", "creator-1",
    )
    assert await connector.init() is True
    return connector
