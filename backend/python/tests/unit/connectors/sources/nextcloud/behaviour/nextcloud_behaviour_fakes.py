"""Fakes for behaviour tests of the Nextcloud connector.

Only two things are faked. The Nextcloud server is an in-memory file tree that
answers at httpx's network hop, below the connector's real HTTP client, so
request building, Basic auth, WebDAV XML parsing and OCS JSON parsing all run
for real. Our databases (records, parent links, record groups and sync
checkpoints) are kept in memory, so a second sync sees what the first wrote.
"""

from __future__ import annotations

import base64
import itertools
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape

import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from app.models.entities import FileRecord, Record, RecordGroup

WEBDAV_PREFIX = "/remote.php/dav/files/"
ACTIVITY_PATH = "/ocs/v2.php/apps/activity/api/v2/activity/files"
SHARES_PATH = "/ocs/v2.php/apps/files_sharing/api/v1/shares"
CAPABILITIES_PATH = "/ocs/v1.php/cloud/capabilities"
USERS_PREFIX = "/ocs/v1.php/cloud/users/"

HTTP_DATE = "%a, %d %b %Y %H:%M:%S GMT"


def ocs(data: object, status: int = 200) -> httpx.Response:
    body = {"ocs": {"meta": {"status": "ok", "statuscode": status, "message": "OK"}, "data": data}}
    return httpx.Response(status, content=json.dumps(body).encode(), headers={"content-type": "application/json"})


@dataclass
class Node:
    file_id: str
    path: str  # relative to the user's home, no leading or trailing slash
    is_dir: bool
    etag: str
    modified: datetime
    content: bytes = b""
    content_type: str = "application/octet-stream"

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class Fault:
    method: str
    matches: Callable[[str], bool]
    responses: list[httpx.Response | Exception]
    hits: int = 0


@dataclass
class FakeNextcloud:
    """One user's Nextcloud: a file tree, an activity feed and the OCS endpoints the connector uses.

    Every change made through the helpers is logged to the activity feed the way
    Nextcloud logs it, so incremental sync can be driven by editing the tree.
    """

    user: str = "alice"
    app_password: str = "Xk3p9-QmT7r-Ab2Cd-Ef4Gh-Ij5Kl"
    email: Optional[str] = "alice@example.com"
    base_path: str = ""
    nodes: dict[str, Node] = field(default_factory=dict)
    activities: list[dict[str, Any]] = field(default_factory=list)
    shares: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._ids = itertools.count(100)
        self._activity_ids = itertools.count(1)
        self._clock = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
        self.nodes[""] = Node(str(next(self._ids)), "", True, "root-etag", self._clock)

    # ---- editing the tree (each edit is logged like Nextcloud logs it) ----

    def _tick(self) -> datetime:
        self._clock += timedelta(minutes=1)
        return self._clock

    def _log(self, kind: str, subject: str, targets: dict[str, str]) -> int:
        activity_id = next(self._activity_ids)
        first_id, first_path = next(iter(targets.items()))
        self.activities.append({
            "activity_id": activity_id,
            "app": "files",
            "type": kind,
            "subject": subject,
            "object_type": "files",
            "object_id": int(first_id),
            "object_name": first_path,
            "objects": dict(targets),
            "datetime": self._clock.isoformat(),
        })
        return activity_id

    def _ensure_parents(self, path: str) -> None:
        parts = path.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            folder = "/".join(parts[:depth])
            if folder not in self.nodes:
                self.add_folder(folder)

    def add_folder(self, path: str) -> Node:
        self._ensure_parents(path)
        node = Node(str(next(self._ids)), path, True, f"e{next(self._ids)}", self._tick())
        self.nodes[path] = node
        self._log("file_created", "created_self", {node.file_id: f"/{path}"})
        return node

    def add_file(self, path: str, content: bytes = b"hello", content_type: str = "text/plain",
                 modified: Optional[datetime] = None, log: bool = True) -> Node:
        self._ensure_parents(path)
        node = Node(str(next(self._ids)), path, False, f"e{next(self._ids)}", modified or self._tick(),
                    content, content_type)
        self.nodes[path] = node
        if log:
            self._log("file_created", "created_self", {node.file_id: f"/{path}"})
        return node

    def add_files_in_one_upload(self, paths: list[str]) -> list[Node]:
        """Nextcloud merges a multi-file upload into one activity naming every file in ``objects``."""
        made = [self.add_file(p, log=False) for p in paths]
        self._log("file_created", "created_self", {n.file_id: f"/{n.path}" for n in made})
        return made

    def change(self, path: str, content: bytes = b"changed") -> Node:
        node = self.nodes[path]
        node.content = content
        node.etag = f"e{next(self._ids)}"
        node.modified = self._tick()
        self._log("file_changed", "changed_self", {node.file_id: f"/{path}"})
        return node

    def move(self, old: str, new: str) -> Node:
        """Rename or move; a folder takes its whole subtree along, keeping every file id."""
        self._ensure_parents(new)
        moved = {p: n for p, n in self.nodes.items() if p == old or p.startswith(old + "/")}
        for p, n in moved.items():
            del self.nodes[p]
            n.path = new + p[len(old):]
            self.nodes[n.path] = n
        node = self.nodes[new]
        node.modified = self._tick()
        subject = "renamed_self" if old.rsplit("/", 1)[0] == new.rsplit("/", 1)[0] else "moved_self"
        self._log("file_changed", subject, {node.file_id: f"/{new}"})
        return node

    def delete(self, path: str) -> Node:
        """Deleting a folder logs one activity for the folder only, as Nextcloud does."""
        node = self.nodes[path]
        for p in [p for p in self.nodes if p == path or p.startswith(path + "/")]:
            del self.nodes[p]
        self._tick()
        self._log("file_deleted", "deleted_self", {node.file_id: f"/{path}"})
        return node

    def restore(self, node: Node) -> None:
        self._ensure_parents(node.path)
        self.nodes[node.path] = node
        self._tick()
        self._log("file_restored", "restored_self", {node.file_id: f"/{node.path}"})

    def fail(self, method: str, matches: Callable[[str], bool], *responses: httpx.Response | Exception) -> Fault:
        """Answer the next matching requests with ``responses`` (one each; an exception is raised), then behave normally."""
        fault = Fault(method.upper(), matches, list(responses))
        self.faults.append(fault)
        return fault

    @property
    def latest_activity_id(self) -> int:
        return self.activities[-1]["activity_id"] if self.activities else 0

    def webdav_url_path(self, rel: str) -> str:
        return f"{self.base_path}{WEBDAV_PREFIX}{self.user}/{rel}"

    # ---- serving requests ----

    def _authorised(self, request: httpx.Request) -> bool:
        expected = base64.b64encode(f"{self.user}:{self.app_password}".encode()).decode()
        return request.headers.get("authorization") == f"Basic {expected}"

    def calls(self, method: str, path_prefix: str = "") -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method.upper()
                and self._decoded_path(r).startswith(self.base_path + path_prefix)]

    @staticmethod
    def _decoded_path(request: httpx.Request) -> str:
        return unquote(request.url.raw_path.decode("ascii").split("?", 1)[0])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = self._decoded_path(request)
        for fault in self.faults:
            if fault.responses and fault.method == request.method and fault.matches(path):
                fault.hits += 1
                answer = fault.responses.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer
        if not self._authorised(request):
            return httpx.Response(401, content=b'{"message":"Current user is not logged in"}')
        if not path.startswith(self.base_path):
            return self._unrouted(request)
        path = path[len(self.base_path):]
        if path.startswith(WEBDAV_PREFIX + self.user):
            rel = path[len(WEBDAV_PREFIX + self.user):].strip("/")
            if request.method == "PROPFIND":
                return self._propfind(rel, request.headers.get("depth", "1"))
            if request.method == "GET":
                node = self.nodes.get(rel)
                if node is None or node.is_dir:
                    return httpx.Response(404)
                return httpx.Response(200, content=node.content, headers={"content-type": node.content_type})
        if request.method == "GET" and path == ACTIVITY_PATH:
            return self._activity(request.url.params)
        if request.method == "GET" and path == USERS_PREFIX + self.user:
            return ocs({"id": self.user, "email": self.email, "displayname": self.user.title()})
        if request.method == "GET" and path == CAPABILITIES_PATH:
            return ocs({"version": {"major": 30}, "capabilities": {}})
        if request.method == "GET" and path == SHARES_PATH:
            return ocs(self.shares.get(request.url.params.get("path", ""), []))
        return self._unrouted(request)

    def _unrouted(self, request: httpx.Request) -> httpx.Response:
        self.unrouted.append(f"{request.method} {request.url}")
        return httpx.Response(404)

    def _propfind(self, rel: str, depth_header: str) -> httpx.Response:
        root = self.nodes.get(rel)
        if root is None:
            return httpx.Response(404, content=b"<d:error xmlns:d='DAV:'/>")
        depth = 10**6 if depth_header == "infinity" else int(depth_header)
        prefix = f"{rel}/" if rel else ""
        below = sorted(
            (n for p, n in self.nodes.items()
             if p and p.startswith(prefix) and p != rel and p[len(prefix):].count("/") < depth),
            key=lambda n: n.path,
        )
        body = "".join(self._response_xml(n) for n in [root, *below])
        xml = (
            '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns" '
            'xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">'
            f"{body}</d:multistatus>"
        )
        return httpx.Response(207, content=xml.encode(), headers={"content-type": "application/xml; charset=utf-8"})

    def _size(self, node: Node) -> int:
        if not node.is_dir:
            return len(node.content)
        prefix = f"{node.path}/" if node.path else ""
        return sum(len(n.content) for p, n in self.nodes.items() if not n.is_dir and p.startswith(prefix))

    def _response_xml(self, node: Node) -> str:
        href = quote(self.webdav_url_path(node.path))
        if node.is_dir and not href.endswith("/"):
            href += "/"
        found = [
            f"<d:getlastmodified>{node.modified.strftime(HTTP_DATE)}</d:getlastmodified>",
            f"<d:getetag>&quot;{node.etag}&quot;</d:getetag>",
            f"<oc:fileid>{node.file_id}</oc:fileid>",
            "<oc:permissions>RGDNVW</oc:permissions>",
            f"<oc:size>{self._size(node)}</oc:size>",
            f"<d:displayname>{escape(node.name)}</d:displayname>",
            "<d:resourcetype><d:collection/></d:resourcetype>" if node.is_dir else "<d:resourcetype/>",
        ]
        missing = ["<nc:is-encrypted/>", "<oc:checksums/>"]
        if node.is_dir:
            missing += ["<d:getcontenttype/>", "<d:getcontentlength/>"]
        else:
            found += [
                f"<d:getcontenttype>{node.content_type}</d:getcontenttype>",
                f"<d:getcontentlength>{len(node.content)}</d:getcontentlength>",
            ]
        return (
            f"<d:response><d:href>{href}</d:href>"
            f"<d:propstat><d:prop>{''.join(found)}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
            f"<d:propstat><d:prop>{''.join(missing)}</d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>"
            "</d:response>"
        )

    def _activity(self, params: httpx.QueryParams) -> httpx.Response:
        if not self.activities:
            return httpx.Response(204)
        since = int(params["since"]) if "since" in params else None
        limit = int(params.get("limit", "50"))
        descending = params.get("sort", "desc") == "desc"
        items = sorted(self.activities, key=lambda a: a["activity_id"], reverse=descending)
        if since is not None:
            items = [a for a in items if (a["activity_id"] < since if descending else a["activity_id"] > since)]
        if not items:
            return httpx.Response(304)
        page = items[:limit]
        headers = {"X-Activity-First-Known": str(self.activities[0]["activity_id"]),
                   "X-Activity-Last-Given": str(page[-1]["activity_id"])}
        if len(items) > limit:
            headers["Link"] = f'<{ACTIVITY_PATH}?since={page[-1]["activity_id"]}>; rel="next"'
        response = ocs(page)
        response.headers.update(headers)
        return response

    def network(self) -> Callable[[httpx.AsyncHTTPTransport, httpx.Request], Awaitable[httpx.Response]]:
        """A stand-in for ``httpx.AsyncHTTPTransport.handle_async_request``.

        Patching the last hop, instead of swapping the client's transport, keeps
        whatever transport the client configured (such as ``ResilientHTTPTransport``
        with its retries) running for real on top of this server.
        """
        async def handle_async_request(_transport: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
            await request.aread()
            return self(request)

        return handle_async_request


class FakeRecordsDb:
    """In-memory stand-in for ``DataSourceEntitiesProcessor`` and the graph behind it.

    Parent links are kept as edges, the way the graph keeps them, and follow
    ``_handle_parent_record``: a write drops the old edge when the parent changes
    and adds one only when the parent record already exists. ``get_record_path``
    follows the Arango query: it walks those edges, keeping only the ancestor each
    record names as its parent. ``on_record_deleted`` removes one record and
    nothing below it, as production does.
    """

    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.records: dict[str, Any] = {}
        self.permissions: dict[str, list[Any]] = {}
        self.edges: dict[str, str] = {}  # child record id -> parent record id
        self.record_groups: dict[str, Any] = {}
        self.record_group_permissions: dict[str, list[Any]] = {}
        self.app_users: list[Any] = []
        self.batches: list[list[str]] = []
        self.deleted: list[str] = []
        self.content_updates: list[Any] = []
        self.fail_lookup_for: set[str] = set()
        self.fail_write_for: set[str] = set()
        self.fail_delete_for: set[str] = set()
        self.messaging_producer: Any = None

    def _by_id(self, record_id: str) -> Optional[FileRecord]:
        return next((r for r in self.records.values() if r.id == record_id), None)

    def by_name(self, name: str) -> FileRecord:
        matches = [r for r in self.records.values() if r.record_name == name]
        assert len(matches) == 1, f"expected one record named {name!r}, found {len(matches)}"
        return matches[0]

    def names(self) -> set[str]:
        return {r.record_name for r in self.records.values()}

    def path_of(self, name: str) -> Optional[str]:
        return self._path(self.by_name(name).id)

    def _upsert(self, record: FileRecord) -> None:
        existing = self.records.get(record.external_record_id)
        if existing is not None:
            record.id = existing.id
            if existing.parent_external_record_id and record.parent_external_record_id != existing.parent_external_record_id:
                self.edges.pop(record.id, None)
        stored = record.model_copy(deep=True)
        self.records[record.external_record_id] = stored
        if stored.parent_external_record_id:
            parent = self.records.get(stored.parent_external_record_id)
            if parent is not None:
                self.edges[stored.id] = parent.id

    # ---- DataSourceEntitiesProcessor surface used by the connector ----

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Optional[FileRecord]:
        if external_record_id in self.fail_lookup_for:
            raise RuntimeError(f"database unavailable for {external_record_id}")
        found = self.records.get(external_record_id)
        return found.model_copy(deep=True) if found is not None else None

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        for record, _ in records_with_permissions:
            if record.record_name in self.fail_write_for:
                raise RuntimeError(f"write failed for {record.record_name}")
        self.batches.append([r.record_name for r, _ in records_with_permissions])
        for record, permissions in records_with_permissions:
            self._upsert(record)
            kept = self.permissions.setdefault(record.external_record_id, [])
            for p in permissions:
                if all((p.email, p.type) != (k.email, k.type) for k in kept):
                    kept.append(p)

    async def on_record_content_update(self, record: FileRecord) -> None:
        self.content_updates.append(record)
        self._upsert(record)

    async def on_record_deleted(self, record_id: str) -> None:
        record = self._by_id(record_id)
        if record is not None and record.external_record_id in self.fail_delete_for:
            raise RuntimeError(f"delete failed for {record.record_name}")
        self.edges.pop(record_id, None)
        if record is not None:
            del self.records[record.external_record_id]
            self.deleted.append(record.record_name)

    async def delete_parent_child_edge_to_record(self, record_id: str) -> int:
        return 1 if self.edges.pop(record_id, None) else 0

    async def on_new_app_users(self, users: list[Any]) -> None:
        self.app_users.extend(users)

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in groups:
            self.record_groups[group.external_group_id] = group
            self.record_group_permissions[group.external_group_id] = list(permissions)

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[RecordGroup]:
        return self.record_groups.get(external_id)

    async def get_file_record_by_id(self, record_id: str) -> Optional[FileRecord]:
        return self._by_id(record_id)

    def _path(self, record_id: str) -> Optional[str]:
        record = self._by_id(record_id)
        if record is None:
            return None
        names = [record.record_name]
        current = record
        while current.id in self.edges:
            parent = self._by_id(self.edges[current.id])
            if parent is None or parent.external_record_id != current.parent_external_record_id:
                break
            names.insert(0, parent.record_name)
            current = parent
        return "/".join(n for n in names if n)

    async def get_record_path(self, record_id: str) -> Optional[str]:
        return self._path(record_id)

    async def get_first_user_with_permission_to_node(self, node_id: str, node_collection: str) -> Optional[SimpleNamespace]:
        record = self._by_id(node_id)
        if record is None or not self.permissions.get(record.external_record_id):
            return None
        return SimpleNamespace(email=self.permissions[record.external_record_id][0].email, source_user_id=None)


class FakeStore:
    """In-memory sync-point collection behind ``DataStoreProvider.transaction()``."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}
        self.fail_reads = 0

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> Optional[dict[str, Any]]:
        if self.fail_reads:
            self.fail_reads -= 1
            raise RuntimeError("database unavailable")
        return self.sync_points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.sync_points[key] = dict(data)

    async def get_record_by_path(self, connector_id: str, path: list[str], external_record_group_id: str) -> Optional[Record]:
        """Same signature as ``GraphDataStore.get_record_by_path``; Nextcloud records store no path."""
        return None

    def cursor(self) -> Optional[str]:
        for key, value in self.sync_points.items():
            if key.endswith("/activity_cursor"):
                return value.get("cursor")
        return None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["FakeStore"]:
        yield self


class FakeConfigService:
    """Serves one connector's etcd config document (auth + filters)."""

    def __init__(self, connector_id: str, config: Optional[dict[str, Any]]) -> None:
        self.connector_id = connector_id
        self.config = config

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        if path == f"/services/connectors/{self.connector_id}/config":
            return self.config
        return default
