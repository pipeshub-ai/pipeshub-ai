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
import re
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape

import httpx

from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    is_live_record,
    matches_visibility,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
    from app.models.entities import FileRecord, Record, RecordGroup

WEBDAV_PREFIX = "/remote.php/dav/files/"
ACTIVITY_PATH = "/ocs/v2.php/apps/activity/api/v2/activity/all"
SHARES_PATH = "/ocs/v2.php/apps/files_sharing/api/v1/shares"
CAPABILITIES_PATH = "/ocs/v1.php/cloud/capabilities"
USERS_PREFIX = "/ocs/v1.php/cloud/users/"
CURRENT_USER_PATH = "/ocs/v1.php/cloud/user"
WIPE_CHECK_PATH = "/index.php/core/wipe/check"
WIPE_SUCCESS_PATH = "/index.php/core/wipe/success"

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
    created: int = 0  # nc:creation_time: set only when the uploading client sent one
    download: bool = True  # False: shared with "Allow download" unticked

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class Fault:
    method: str
    matches: Callable[[str], bool]
    responses: list[httpx.Response | Exception]
    hits: int = 0
    make: Callable[[], httpx.Response | Exception] | None = None

    def live(self) -> bool:
        return bool(self.responses) or self.make is not None

    def end(self) -> None:
        self.responses.clear()
        self.make = None


@dataclass
class FakeNextcloud:
    """One user's Nextcloud: a file tree, an activity feed and the OCS endpoints the connector uses.

    Every change made through the helpers is logged to the activity feed the way
    Nextcloud logs it, so incremental sync can be driven by editing the tree.
    """

    user: str = "alice"
    app_password: str = "Xk3p9-QmT7r-Ab2Cd-Ef4Gh-Ij5Kl"
    # The name typed into the form, when it isn't the user id (an email or LDAP login).
    login: str | None = None
    # Set by marking the app password for remote wipe; every request then answers 401.
    wipe_requested: bool = False
    wipe_confirmed: bool = False
    email: Optional[str] = "alice@example.com"
    base_path: str = ""
    nodes: dict[str, Node] = field(default_factory=dict)
    activities: list[dict[str, Any]] = field(default_factory=list)
    shares: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)
    trash: dict[str, list[Node]] = field(default_factory=dict)
    search_supported: bool = True
    redirect_downloads_to: str | None = None
    # False: the Activity app is disabled, and its endpoint answers 404 as Nextcloud does.
    activity_enabled: bool = True
    # False: the app password's "Allow filesystem access" is unticked. It still signs in, but the
    # home lists as an empty folder with file id -1 and files answer 404 (seen live, Nextcloud 31).
    filesystem_access: bool = True
    # Mount points whose storage is unavailable (an offline external storage or federated share).
    unavailable: set[str] = field(default_factory=set)
    # Unavailable mounts their parent's listing leaves out, as the first one after the outage does (seen live).
    left_out: set[str] = field(default_factory=set)
    # What each host name resolves to: the client connects only to an address it checked.
    dns: dict[str, str] = field(default_factory=lambda: {"cloud.example.com": "93.184.215.14"})

    def __post_init__(self) -> None:
        self._ids = itertools.count(100)
        self._activity_ids = itertools.count(1)
        self._clock = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
        self.nodes[""] = Node(str(next(self._ids)), "", True, "root-etag", self._clock)

    # ---- editing the tree (each edit is logged like Nextcloud logs it) ----

    def _tick(self) -> datetime:
        self._clock += timedelta(minutes=1)
        return self._clock

    def _log(self, kind: str, subject: str, targets: dict[str, str], app: str = "files") -> int:
        activity_id = next(self._activity_ids)
        first_id, first_path = next(iter(targets.items()))
        self.activities.append({
            "activity_id": activity_id,
            "app": app,
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
        removed = [p for p in self.nodes if p == path or p.startswith(path + "/")]
        self.trash[node.file_id] = [self.nodes[p] for p in removed]
        for p in removed:
            del self.nodes[p]
        self._tick()
        self._log("file_deleted", "deleted_self", {node.file_id: f"/{path}"})
        return node

    def restore(self, node: Node) -> None:
        """Restoring from the trash brings a folder back with everything it held, under one activity."""
        self._ensure_parents(node.path)
        for restored in self.trash.pop(node.file_id, [node]):
            self.nodes[restored.path] = restored
        self._tick()
        self._log("file_restored", "restored_self", {node.file_id: f"/{node.path}"})

    def copy(self, source: str, target: str) -> Node:
        """A server-side copy: new ids for the whole subtree, but one activity for the top folder only (seen live)."""
        for p in sorted(p for p in self.nodes if p == source or p.startswith(source + "/")):
            old = self.nodes[p]
            path = target + p[len(source):]
            self.nodes[path] = Node(str(next(self._ids)), path, old.is_dir, f"e{next(self._ids)}", self._tick(),
                                    old.content, old.content_type)
        self._log("file_created", "created_self", {self.nodes[target].file_id: f"/{target}"})
        return self.nodes[target]

    def share_in(self, path: str, files: dict[str, bytes]) -> Node:
        """Another user shares a folder with this one: it appears with what it holds, and the
        only activity is one ``shared`` entry from files_sharing, outside the 'files' stream (seen live)."""
        folder = Node(str(next(self._ids)), path, True, f"e{next(self._ids)}", self._tick())
        self.nodes[path] = folder
        for name, content in files.items():
            node = Node(str(next(self._ids)), f"{path}/{name}", False, f"e{next(self._ids)}", self._tick(), content, "text/plain")
            self.nodes[node.path] = node
        self._log("shared", "shared_with_by", {folder.file_id: f"/{path}"}, app="files_sharing")
        return folder

    def unshare(self, path: str) -> None:
        """The owner removes the share: the folder leaves this user's tree, logged like the share itself."""
        folder = self.nodes[path]
        for p in [p for p in self.nodes if p == path or p.startswith(path + "/")]:
            del self.nodes[p]
        self._tick()
        self._log("shared", "unshared_by", {folder.file_id: f"/{path}"}, app="files_sharing")

    def fail(self, method: str, matches: Callable[[str], bool], *responses: httpx.Response | Exception) -> Fault:
        """Answer the next matching requests with ``responses`` (one each; an exception is raised), then behave normally."""
        fault = Fault(method.upper(), matches, list(responses))
        self.faults.append(fault)
        return fault

    def outage(self, method: str, matches: Callable[[str], bool], make: Callable[[], httpx.Response | Exception]) -> Fault:
        """Answer every matching request with a fresh ``make()``, retries included, until ``end()`` is called."""
        fault = Fault(method.upper(), matches, [], make=make)
        self.faults.append(fault)
        return fault

    @property
    def latest_activity_id(self) -> int:
        return self.activities[-1]["activity_id"] if self.activities else 0

    def webdav_url_path(self, rel: str) -> str:
        return f"{self.base_path}{WEBDAV_PREFIX}{self.user}/{rel}"

    # ---- serving requests ----

    def _authorised(self, request: httpx.Request) -> bool:
        if self.wipe_requested:
            return False
        expected = base64.b64encode(f"{self.login or self.user}:{self.app_password}".encode()).decode()
        return request.headers.get("authorization") == f"Basic {expected}"

    def _wipe(self, request: httpx.Request, path: str) -> httpx.Response:
        """Nextcloud's remote wipe calls: unauthenticated, keyed by the app password in a form body."""
        token = dict(httpx.QueryParams(request.content.decode())).get("token")
        if not self.wipe_requested or token != self.app_password:
            return httpx.Response(404, content=b"[]")
        if path == WIPE_SUCCESS_PATH:
            self.wipe_confirmed = True
            return httpx.Response(200, content=b"[]")
        return httpx.Response(200, content=b'{"wipe":true}')

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
            if fault.live() and fault.method == request.method and fault.matches(path):
                fault.hits += 1
                answer = fault.responses.pop(0) if fault.responses else fault.make()
                if isinstance(answer, Exception):
                    raise answer
                return answer
        if request.method == "POST" and path[len(self.base_path):] in (WIPE_CHECK_PATH, WIPE_SUCCESS_PATH):
            return self._wipe(request, path[len(self.base_path):])
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
                if self.redirect_downloads_to:
                    return httpx.Response(302, headers={"location": self.redirect_downloads_to})
                node = self.nodes.get(rel)
                if node is None or node.is_dir or not self.filesystem_access:
                    return httpx.Response(404)
                if self._blocked(rel) is not None:
                    return httpx.Response(503)
                if not node.download:
                    return httpx.Response(403)
                return httpx.Response(200, content=node.content, headers={"content-type": node.content_type})
        if request.method == "SEARCH" and path.rstrip("/") == "/remote.php/dav":
            return self._search(request.content.decode())
        # Every stream answers alike: the fake logs only what the user's view of the feed holds.
        if request.method == "GET" and path.startswith(ACTIVITY_PATH.rsplit("/", 1)[0] + "/"):
            return self._activity(request.url.params)
        if request.method == "GET" and path in (USERS_PREFIX + self.user, CURRENT_USER_PATH):
            return ocs({"id": self.user, "email": self.email, "displayname": self.user.title()})
        if request.method == "GET" and path == CAPABILITIES_PATH:
            return ocs({"version": {"major": 30}, "capabilities": {}})
        if request.method == "GET" and path == SHARES_PATH:
            return ocs(self.shares.get(request.url.params.get("path", ""), []))
        return self._unrouted(request)

    def _unrouted(self, request: httpx.Request) -> httpx.Response:
        self.unrouted.append(f"{request.method} {request.url}")
        return httpx.Response(404)

    def _blocked(self, path: str) -> str | None:
        """The unavailable mount ``path`` is or lies below, if any."""
        return next((m for m in self.unavailable if path == m or path.startswith(m + "/")), None)

    def _propfind(self, rel: str, depth_header: str) -> httpx.Response:
        root = self.nodes.get(rel)
        if root is None:
            return httpx.Response(404, content=b"<d:error xmlns:d='DAV:'/>")
        mount = self._blocked(rel)
        if mount is not None and rel != mount:
            return httpx.Response(503, content=(
                '<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">'
                "<s:exception>Sabre\\DAV\\Exception\\ServiceUnavailable</s:exception>"
                "<s:message>Storage with mount id 4 is not available</s:message></d:error>"
            ).encode())
        depth = 10**6 if depth_header == "infinity" else int(depth_header)
        prefix = f"{rel}/" if rel else ""
        below = sorted(
            (n for p, n in self.nodes.items()
             if p and p.startswith(prefix) and p != rel and p[len(prefix):].count("/") < depth
             and (self._blocked(p) in (None, p)) and p not in self.left_out),
            key=lambda n: n.path,
        )
        if not self.filesystem_access or mount is not None:
            below = []
        body = "".join(self._response_xml(n) for n in [root, *below])
        xml = (
            '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns" '
            'xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">'
            f"{body}</d:multistatus>"
        )
        return httpx.Response(207, content=xml.encode(), headers={"content-type": "application/xml; charset=utf-8"})

    def _search(self, body: str) -> httpx.Response:
        """WebDAV SEARCH for one file ID in the user's files (the trash is not searched)."""
        if not self.search_supported:
            return httpx.Response(501)
        scope = re.search(r"<d:href>/files/([^<]+)</d:href>", body)
        wanted = re.search(r"<d:literal>([^<]+)</d:literal>", body)
        if not scope or not wanted or scope.group(1) != self.user:
            return httpx.Response(400)
        found = [n for p, n in self.nodes.items() if p and n.file_id == wanted.group(1)]
        xml = (
            '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" '
            'xmlns:nc="http://nextcloud.org/ns">'
            f"{''.join(self._response_xml(n) for n in found)}</d:multistatus>"
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
        # What Nextcloud answers for a node it cannot open (seen live): id -1, no etag, size 0.
        unopened = node.path in self.unavailable or (not self.filesystem_access and not node.path)
        found = [
            f"<d:getlastmodified>{node.modified.strftime(HTTP_DATE)}</d:getlastmodified>",
            "<d:getetag>&quot;&quot;</d:getetag>" if unopened else f"<d:getetag>&quot;{node.etag}&quot;</d:getetag>",
            f"<oc:fileid>{-1 if unopened else node.file_id}</oc:fileid>",
            "<oc:permissions>MG</oc:permissions>" if unopened else "<oc:permissions>RGDNVW</oc:permissions>",
            f"<oc:size>{0 if unopened else self._size(node)}</oc:size>",
            f"<d:displayname>{escape(node.name)}</d:displayname>",
            f"<nc:creation_time>{node.created}</nc:creation_time>",
            "<nc:share-attributes>{}</nc:share-attributes>".format(escape(json.dumps(
                [] if node.download else [{"scope": "permissions", "key": "download", "value": False}]))),
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
        if not self.activity_enabled:
            return ocs([], status=404)
        if not self.activities:
            return httpx.Response(204)
        since = int(params["since"]) if "since" in params else None
        # Nextcloud's Data::get caps every page at 200, whatever is asked for.
        limit = min(200, int(params.get("limit", "50")))
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

    def getaddrinfo(self, host: str, port: object, *args: object, **kwargs: object) -> list[tuple]:
        """A stand-in for ``socket.getaddrinfo`` that answers from ``dns``."""
        if host not in self.dns:
            raise socket.gaierror(socket.EAI_NONAME, f"{host} not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.dns[host], port or 0))]

    def network(self) -> Callable[[httpx.AsyncHTTPTransport, httpx.Request], Awaitable[httpx.Response]]:
        """A stand-in for ``httpx.AsyncHTTPTransport.handle_async_request``.

        Patching the last hop, instead of swapping the client's transport, keeps
        whatever transport the client configured (such as ``ResilientHTTPTransport``
        with its retries) running for real on top of this server.
        """
        async def handle_async_request(_transport: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
            await request.aread()
            response = self(request)
            # Nextcloud starts a session on every answer, as the live server does.
            response.headers["set-cookie"] = "oc_sessionPassphrase=s3ss10n; path=/; HttpOnly"
            return response

        return handle_async_request


class FakeRecordsDb:
    """In-memory stand-in for ``DataSourceEntitiesProcessor`` and the graph behind it.

    Parent links are kept as edges, the way the graph keeps them, and follow
    ``_handle_parent_record``: a write drops the old edge when the parent changes
    and adds one only when the parent record already exists. ``get_record_path``
    follows the Arango query: it walks those edges, keeping only the ancestor each
    record names as its parent. ``on_record_deleted`` removes one record and
    nothing below it, as production does; ``on_records_deleted_cascade`` removes
    the subtree below each record too.
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
        self.fail_file_record_for: set[str] = set()
        self.fail_write_for: set[str] = set()
        self.fail_delete_for: set[str] = set()
        self.unreadable_paths: set[str] = set()
        self.fail_record_scan = False
        self.messaging_producer: Any = None
        self.kept_records: list[str] = []
        self.moves: list[str] = []
        self.full_sync_run: Any = None
        self.lookups = 0
        self.edge_deletes = 0

    def note_kept_records(self, connector_id: str, record_ids: list[str]) -> None:
        self.kept_records.extend(record_ids)
        self._seen("records", record_ids)

    # ---- full-sync tracking, as the processor does it for run_sync_task ----

    def begin_full_sync_run(self, connector_id: str) -> FullSyncSeen:
        from app.connectors.core.sync.full_sync_coverage import FullSyncSeen

        self.full_sync_run = FullSyncSeen()
        return self.full_sync_run

    def end_full_sync_run(self, connector_id: str, run: FullSyncSeen) -> None:
        self.full_sync_run = None

    def _seen(self, collection: str, node_ids: list[str]) -> None:
        if self.full_sync_run is not None:
            for node_id in node_ids:
                self.full_sync_run.add(collection, node_id)

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

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        """A base Record, as both graph stores rebuild it: no ``is_file``, ``path`` or ``etag``."""
        from app.models.entities import Record

        self.lookups += 1
        if external_record_id in self.fail_lookup_for:
            raise RuntimeError(f"database unavailable for {external_record_id}")
        found = self.records.get(external_record_id)
        if found is None:
            return None
        return Record.model_validate(found.model_dump(include=set(Record.model_fields)))

    def stored_document_at(self, names: list[str], external_record_group_id: str) -> dict[str, Any] | None:
        """Walks names down parent edges from a top-level folder, as the graph stores' path query does."""
        from app.config.constants.arangodb import MimeTypes
        from app.models.entities import Record

        if not names:
            return None
        current = next((r for r in self.records.values()
                        if r.record_name == names[0] and r.parent_external_record_id is None
                        and r.mime_type == MimeTypes.FOLDER.value
                        and r.external_record_group_id == external_record_group_id), None)
        for name in names[1:]:
            if current is None:
                return None
            current = next((r for r in self.records.values()
                            if self.edges.get(r.id) == current.id and r.record_name == name), None)
        return None if current is None else Record.to_arango_base_record(current)

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        for record, _ in records_with_permissions:
            if record.record_name in self.fail_write_for:
                raise RuntimeError(f"write failed for {record.record_name}")
        self.batches.append([r.record_name for r, _ in records_with_permissions])
        for record, permissions in records_with_permissions:
            self._upsert(record)
            self._seen("records", [record.id])
            kept = self.permissions.setdefault(record.external_record_id, [])
            for p in permissions:
                if all((p.email, p.type) != (k.email, k.type) for k in kept):
                    kept.append(p)

    async def on_records_moved(self, moves: list[tuple[str, Any, list[Any]]], **_: object) -> None:
        """Saves each record in place under its id, re-pointing its parent link, as the processor does."""
        for _old_external_id, record, _permissions in moves:
            if record.record_name in self.fail_write_for:
                raise RuntimeError(f"write failed for {record.record_name}")
        self.moves.extend(record.record_name for _, record, _ in moves)
        for _, record, _ in moves:
            self._upsert(record)

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

    async def on_records_deleted_cascade(self, record_ids: list[str], connector_id: str,
                                         cascade_children: bool = True) -> dict[str, Any]:
        """Deletes each record with everything below it along parent edges, all or nothing."""
        doomed: list[str] = []
        pending = list(record_ids)
        while pending:
            record_id = pending.pop()
            if record_id in doomed or self._by_id(record_id) is None:
                continue
            doomed.append(record_id)
            pending.extend(child for child, parent in self.edges.items() if parent == record_id)
        failed = [rid for rid in doomed if self._by_id(rid).external_record_id in self.fail_delete_for]
        if failed:
            return {"success": False, "reason": "delete failed", "deleted_records": [],
                    "failed_records": failed, "successfully_deleted": 0, "failed_count": len(failed)}
        for record_id in doomed:
            record = self._by_id(record_id)
            self.edges.pop(record_id, None)
            del self.records[record.external_record_id]
            self.deleted.append(record.record_name)
        return {"success": True, "deleted_records": doomed, "failed_records": [],
                "successfully_deleted": len(doomed), "failed_count": 0}

    async def get_records_by_status(self, connector_id: str, status_filters: list[str] | None,
                                    limit: int | None = None, offset: int = 0,
                                    after_key: str | None = None,
                                    visibility: RecordVisibility = RecordVisibility.LIVE,
                                    **_: object) -> list[FileRecord]:
        """Keyset pages ordered by record id, live only unless asked, and an unreadable listing raises, as both graph stores do."""
        if self.fail_record_scan:
            raise RuntimeError("database unavailable")
        ordered = sorted((r for r in self.records.values() if matches_visibility(r, visibility)), key=lambda r: r.id)
        if after_key is not None:
            ordered = [r for r in ordered if r.id > after_key]
        page = ordered[offset:offset + limit] if limit else ordered[offset:]
        return [r.model_copy(deep=True) for r in page]

    async def get_records_by_parent(self, connector_id: str, parent_external_record_id: str,
                                    record_type: str | None = None) -> list[FileRecord]:
        return [r.model_copy(deep=True) for r in self.records.values()
                if r.parent_external_record_id == parent_external_record_id and is_live_record(r)]

    async def delete_parent_child_edge_to_record(self, record_id: str) -> int:
        self.edge_deletes += 1
        return 1 if self.edges.pop(record_id, None) else 0

    async def on_new_app_users(self, users: list[Any]) -> None:
        # One user-to-app edge per user, as in the graph.
        for user in users:
            self.app_users = [u for u in self.app_users if u.email != user.email] + [user]

    async def get_all_app_users(self, connector_id: str, *, raise_on_error: bool = False) -> list[Any]:
        return list(self.app_users)

    async def withdraw_app_user_access(self, connector_id: str, users: list[Any]) -> None:
        gone = {u.email for u in users}
        self.app_users = [u for u in self.app_users if u.email not in gone]

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in groups:
            self.record_groups[group.external_group_id] = group
            self._seen("recordGroups", [group.id])
            self.record_group_permissions[group.external_group_id] = list(permissions)

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[RecordGroup]:
        return self.record_groups.get(external_id)

    async def get_file_record_by_id(self, record_id: str) -> Optional[FileRecord]:
        """A copy rebuilt as a ``FileRecord``, so changing it does not change what is stored.

        None only when nothing is stored; a read that fails raises ``GraphQueryError``, as both providers do.
        """
        from app.exceptions.graph_db_exceptions import GraphQueryError
        from app.models.entities import FileRecord

        if record_id in self.fail_file_record_for:
            raise GraphQueryError(f"database unavailable for file record {record_id}")
        stored = self._by_id(record_id)
        if not isinstance(stored, FileRecord):
            return None
        return FileRecord.from_arango_record(stored.to_arango_record(), stored.to_arango_base_record())

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
        """Like the graph providers, a path read that fails comes back as None, not an error."""
        if record_id in self.unreadable_paths:
            return None
        return self._path(record_id)


def _neo4j_property(value: object) -> bool:
    primitive = (str, int, float, bool, type(None))
    if isinstance(value, list):
        return all(isinstance(v, primitive) and v is not None for v in value)
    return isinstance(value, primitive)


class FakeStore:
    """In-memory sync-point collection behind ``DataStoreProvider.transaction()``."""

    def __init__(self, records: FakeRecordsDb | None = None) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}
        # The PipesHub users by user id; "creator-user-1" created the connector in these tests.
        self.users: dict[str, dict[str, Any]] = {"creator-user-1": {"_key": "u-creator", "email": "creator@pipeshub.test"}}
        self.fail_reads = 0
        self.records = records
        self.fail_path_lookups = False

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> Optional[dict[str, Any]]:
        if self.fail_reads:
            self.fail_reads -= 1
            raise RuntimeError("database unavailable")
        return self.sync_points.get(key)

    async def get_user_by_user_id(self, user_id: str, **_: object) -> dict[str, Any] | None:
        return self.users.get(user_id)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        """Merges into the stored document, as Arango's UPDATE and Neo4j's ``SET +=`` do.

        A field that is left out keeps its stored value; clearing one takes an explicit write.
        Values Neo4j can't hold as a node property (a map, or a list of anything but
        primitives) are refused, as Neo4j refuses them.
        """
        for field_name, value in data.items():
            if not _neo4j_property(value):
                raise TypeError(f"Neo4j cannot store {field_name!r} as a property: {value!r}")
        self.sync_points.setdefault(key, {}).update(data)

    def checkpoint(self) -> dict[str, Any]:
        for key, value in self.sync_points.items():
            if key.endswith("/activity_cursor"):
                return value
        return {}

    async def get_record_by_path(self, connector_id: str, path: list[str],
                                 external_record_group_id: str) -> dict[str, Any] | None:
        """Same signature as ``GraphDataStore.get_record_by_path``, and like it returns the stored document."""
        if self.fail_path_lookups:
            raise RuntimeError("database unavailable")
        return self.records.stored_document_at(path, external_record_group_id) if self.records else None

    def cursor(self) -> Optional[str]:
        for key, value in self.sync_points.items():
            if key.endswith("/activity_cursor"):
                return value.get("cursor")
        return None

    @asynccontextmanager
    async def transaction(self, explicit: bool | None = None) -> AsyncIterator["FakeStore"]:
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
