"""One PipesHub org wired to one simulated GitLab.

Real: the GitLab (or GitLab Personal) connector and every helper module, python-gitlab
and httpx over real sockets to the simulator, DataSourceEntitiesProcessor,
GraphDataStore, SyncPoint, the product's ``run_sync_task`` (which unlinks what a full
sync did not see) and the graph database (Neo4j or ArangoDB). Simulated: GitLab
(``glsim``), the message broker (events are captured) and the config store.

``World.sync(full=True)`` repeats what ``EventService`` does before a full sync (delete
the connector's sync points), then runs the product's ``run_sync_task`` with
FULL_SYNCING. A filter change makes the next sync a full one, as ``pendingFullSync``
does in the product.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import types
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import gitlab.base
import gitlab.utils

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.sync import sync_runner
from app.connectors.sources.gitlab import repos as repos_module
from app.connectors.sources.gitlab.connector import GitLabConnector
from app.connectors.sources.gitlab_personal.connector import GitLabPersonalConnector
from app.modules.parsers.code_parser.path_policy import PathPolicy
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.gitlab_simulated.glsim import spec as gl_spec
from tests.integration.gitlab_simulated.glsim.model import (
    Behaviour,
    Fault,
    Group,
    Issue,
    MergeRequest,
    Project,
    User,
)
from tests.integration.gitlab_simulated.glsim.model import World as GitLab
from tests.integration.gitlab_simulated.glsim.server import Server
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from app.models.entities import Record

ARANGO_DB = "gitlab_sim_it"

# Fields GitLab really leaves out of some payloads, so ``getattr(x, f, None)`` on them is a
# real code path rather than a simulator gap. Every other read of a missing field fails.
ABSENT_ON_PURPOSE: dict[str, str] = {
    "is_admin": "GitLab includes is_admin only for administrators (docs/users.md)",
    "is_auditor": "GitLab includes is_auditor only for auditors (docs/users.md)",
    "email": "members and other users' profiles carry no email for a non-admin (docs/project_members.md)",
    "position": "only diff notes carry a position (docs/notes.md)",
    "kind": "namespace kind read through getattr on a dict",
    "commit": "a dict branch payload",
    "public_email": "older GitLab (UserBasic without public_email); switch members_expose_public_email",
    "size": "repository file payload without size",
    "default_branch": "an empty repository has none",
}

_providers: dict[str, Any] = {}


async def graph_provider(backend: str, monkeypatch: Any) -> Any:  # noqa: ANN401
    """The repository's integration graph stores (tests/integration/real_graph.py)."""
    if backend in _providers:
        return _providers[backend]
    logger = logging.getLogger(f"gl_sim.{backend}")
    logger.setLevel(logging.WARNING)
    try:
        if backend == "neo4j":
            provider = await connect_neo4j(logger, monkeypatch)
        else:
            provider = await connect_arango(logger, ARANGO_DB)
    except Exception as e:
        backend_unavailable(backend, e)
    _providers[backend] = provider
    return provider


class Producer:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> None:
        return None

    async def send_message(self, topic: str, message: dict[str, Any], key: str | None = None, **_: Any) -> bool:  # noqa: ANN401
        self.events.append((topic, message))
        return True

    async def send_messages(self, topic: str, items: list[Any], **_: Any) -> list[bool]:  # noqa: ANN401
        for _key, message in items:
            self.events.append((topic, message))
        return [True] * len(items)

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [m for _, m in self.events if isinstance(m, dict) and m.get("eventType") == event_type]


class ConfigService:
    def __init__(self, connector_id: str, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = config
        self.platform: dict[str, Any] = {}

    async def get_config(self, path: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
        if path == f"/services/connectors/{self.connector_id}/config":
            return json.loads(json.dumps(self.config))
        if path == "/services/platform/settings":
            return self.platform or default
        return default

    async def set_config(self, path: str, value: Any, **_: Any) -> bool:  # noqa: ANN401
        if path == f"/services/connectors/{self.connector_id}/config":
            self.config.clear()
            self.config.update(value)
        return True


class LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self, level: int = logging.WARNING) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno >= level]

    def text(self) -> str:
        out = []
        for r in self.records:
            out.append(r.getMessage())
            if r.exc_info and r.exc_info[1] is not None:
                out.append(repr(r.exc_info[1]))
        return "\n".join(out)


@dataclass
class Diff:
    over: list[tuple[str, str]] = field(default_factory=list)   # PipesHub shows, GitLab would not
    under: list[tuple[str, str]] = field(default_factory=list)  # GitLab shows, PipesHub does not
    stale: list[str] = field(default_factory=list)              # indexed, gone from GitLab or scope
    missing: list[str] = field(default_factory=list)            # in scope on GitLab, not indexed

    def clean(self) -> bool:
        return not (self.over or self.under or self.stale or self.missing)

    def __str__(self) -> str:
        return (f"over-granted={sorted(self.over)} under-granted={sorted(self.under)} "
                f"stale={sorted(self.stale)} missing={sorted(self.missing)}")


_UPLOAD_RE = re.compile(r"/api/v4/projects/(\d+)/uploads/([^/]+)/(.+)$")
_CODE_RE = re.compile(r"^/(.+)/-/(blob|tree)/HEAD/(.+)$")


class World:
    def __init__(self, backend: str, graph: Any, gl: GitLab, *, personal: bool = False,  # noqa: ANN401
                 domain: str = "acme.com") -> None:
        self.backend, self.graph, self.gl = backend, graph, gl
        self.domain = domain
        self.server = Server(gl)
        self.personal = personal
        suffix = uuid.uuid4().hex[:10]
        self.org_id = f"org-gl-{suffix}"
        self.connector_id = f"gitlab-{suffix}"
        self.creator_id = f"user-creator-{suffix}"
        self.creator_email = f"admin@{domain}"
        self.logger = logging.getLogger(f"gl_audit.world.{suffix}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.logs = LogCapture()
        self.logger.addHandler(self.logs)
        self.producer = Producer()
        self.store = GraphDataStore(self.logger, graph)
        self.processor = DataSourceEntitiesProcessor(self.logger, self.store, MagicMock())
        self.processor.org_id = self.org_id
        self.processor.messaging_producer = self.producer
        self.users: dict[str, str] = {}  # email -> PipesHub userId
        self.config: dict[str, Any] = {}
        self.config_service: ConfigService | None = None
        self.connector: GitLabConnector | None = None
        self.sleeps: list[float] = []
        self.scope: int | None = None  # the selected project id
        self.synced_once = False
        self.pending_full_sync = False
        self.absent_reads: list[str] = []
        self.index_failures: list[tuple[str, str]] = []
        self.sync_errors: list[BaseException] = []

    # ------------------------------------------------------------------ setup

    @classmethod
    async def create(cls, backend: str, monkeypatch: Any, *, behaviour: Behaviour | None = None,  # noqa: ANN401
                     personal: bool = False, domain: str = "acme.com") -> "World":
        graph = await graph_provider(backend, monkeypatch)
        world = cls(backend, graph, GitLab(behaviour), personal=personal, domain=domain)
        world.server.start()
        world._patch(monkeypatch)
        now = get_epoch_timestamp_in_ms()
        await graph.batch_upsert_nodes(
            [{"id": world.org_id, "name": "Acme", "accountType": "enterprise", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.ORGS.value)
        app_type = Connectors.GITLAB_PERSONAL.value if personal else Connectors.GITLAB.value
        await graph.batch_upsert_nodes(
            [{"id": world.connector_id, "name": "GitLab", "type": app_type, "appGroup": "GitLab",
              "scope": "personal" if personal else "team", "isActive": True, "createdBy": world.creator_id,
              "authType": "API_TOKEN", "isConfigured": True, "isAuthenticated": True, "orgId": world.org_id,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.APPS.value)
        await world.add_pipeshub_user(world.creator_email, user_id=world.creator_id)
        return world

    def _patch(self, monkeypatch: Any) -> None:  # noqa: ANN401
        world, gl = self, self.gl

        # python-gitlab's retry and Retry-After waits run on GitLab's clock, not the wall's.
        def _sleep(delay: float) -> None:
            world.sleeps.append(delay)
            gl.clock.advance(delay)

        monkeypatch.setattr(gitlab.utils, "time", types.SimpleNamespace(
            sleep=_sleep, time=lambda: gl.clock.t, monotonic=lambda: gl.clock.t))

        async def _asleep(delay: float, *_: Any, **__: Any) -> None:  # noqa: ANN401
            world.sleeps.append(delay)
            gl.clock.advance(delay)

        aio = types.SimpleNamespace(**{n: getattr(asyncio, n) for n in dir(asyncio) if not n.startswith("__")})
        aio.sleep = _asleep
        monkeypatch.setattr(repos_module, "asyncio", aio)

        original = gitlab.base.RESTObject.__getattr__

        def audited(obj: Any, name: str) -> Any:  # noqa: ANN401
            try:
                value = original(obj, name)
            except AttributeError:
                if not name.startswith("_") and name not in ABSENT_ON_PURPOSE:
                    gl.gaps.append(f"connector read field {name!r} of {type(obj).__name__}, which the "
                                   f"simulator did not put in the payload")
                world.absent_reads.append(name)
                raise
            if value == gl_spec.FILLER_STR or value == gl_spec.FILLER_INT or (
                    isinstance(value, str) and gl_spec.FILLER_STR in value):
                gl.gaps.append(f"connector read field {name!r}, which the simulator does not model")
            return value

        monkeypatch.setattr(gitlab.base.RESTObject, "__getattr__", audited)

    def email(self, name: str, org: str = "") -> str:
        """An address on this world's domain (a second organisation gets its own)."""
        return f"{name}@{org + '-' if org else ''}{self.domain}"

    async def add_pipeshub_user(self, email: str, *, user_id: str | None = None, active: bool = True) -> str:
        user_id = user_id or f"user-{uuid.uuid4().hex[:10]}"
        key = f"ukey-{uuid.uuid4().hex[:12]}"
        now = get_epoch_timestamp_in_ms()
        await self.graph.batch_upsert_nodes(
            [{"id": key, "userId": user_id, "orgId": self.org_id, "email": email, "fullName": email,
              "isActive": active, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection=CollectionNames.USERS.value)
        await self.graph.batch_create_edges(
            [{"from_id": key, "from_collection": CollectionNames.USERS.value, "to_id": self.org_id,
              "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION",
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.BELONGS_TO.value)
        await self.graph.add_user_to_all_team(self.org_id, key)
        self.users[email] = user_id
        return user_id

    def configure(self, project: Project, *, indexing: dict[str, bool] | None = None,
                  extra_sync: dict[str, Any] | None = None, oauth: bool = False) -> None:
        token = self.gl.sync_token
        assert token is not None, "call gl.set_sync_account first"
        if self.synced_once:
            self.pending_full_sync = True
        self.scope = project.id
        self.config.clear()
        # The product's default per-request timeout; a busy test machine needs all of it.
        auth: dict[str, Any] = {"authType": "API_TOKEN", "token": token.value, "instanceUrl": self.gl.base_url,
                                "timeout": 60}
        self.config.update({
            "auth": auth, "credentials": {},
            "filters": {
                "sync": {"values": {"project_ids": {"operator": "in", "type": "select",
                                                    "value": [project.path_with_namespace]}, **(extra_sync or {})}},
                "indexing": {"values": {k: {"operator": "is", "type": "boolean", "value": v}
                                        for k, v in (indexing or {}).items()}},
            }})

    def set_sync_filter(self, key: str, value: Any, operator: str = "in", type_: str = "list") -> None:  # noqa: ANN401
        self.config["filters"]["sync"]["values"][key] = {"operator": operator, "type": type_, "value": value}
        if self.synced_once:
            self.pending_full_sync = True

    def select(self, project: Project, path: str | None = None) -> None:
        """The admin picks a different repository: the next sync is a full one."""
        self.config["filters"]["sync"]["values"]["project_ids"]["value"] = [path or project.path_with_namespace]
        self.scope = project.id
        if self.synced_once:
            self.pending_full_sync = True

    def set_indexing(self, **values: bool) -> None:
        self.config["filters"]["indexing"]["values"] = {
            k: {"operator": "is", "type": "boolean", "value": v} for k, v in values.items()}

    def trash(self, on: bool = True) -> None:
        assert self.config_service is not None
        self.config_service.platform = {"featureFlags": {"ENABLE_SOFT_DELETE": on}} if on else {}

    async def start(self) -> GitLabConnector:
        if self.config_service is None:
            self.config_service = ConfigService(self.connector_id, self.config)
            if os.environ.get("GITLAB_SIM_TRASH"):
                self.trash(True)
            self.processor.config_service = self.config_service
        cls = GitLabPersonalConnector if self.personal else GitLabConnector
        connector = cls(self.logger, self.processor, self.store, self.config_service,
                        self.connector_id, "personal" if self.personal else "team", self.creator_id)
        assert await connector.init() is True, self.logs.text()[-2000:]
        self.connector = connector
        return connector

    # ------------------------------------------------------------------ running

    async def index(self) -> None:
        """What the indexing service does between syncs: stream every record a
        newRecord/updateRecord/reindexRecord event named, unless its indexing is off."""
        assert self.connector is not None
        events, self.producer.events = self.producer.events, []
        ids: list[str] = []
        for _, m in events:
            if isinstance(m, dict) and m.get("eventType") in ("newRecord", "updateRecord", "reindexRecord"):
                rid = (m.get("payload") or {}).get("recordId")
                if rid and rid not in ids:
                    ids.append(rid)
        for rid in ids:
            record = await self.graph.get_record_by_id(rid)
            if record is None or getattr(record, "indexing_status", None) == "AUTO_INDEX_OFF":
                continue
            if getattr(record, "mime_type", None) == "text/directory" or getattr(record, "is_file", True) is False:
                continue
            try:
                response = await self.connector.stream_record(record)
                async for _ in response.body_iterator:
                    pass
            except Exception as e:
                self.index_failures.append((rid, repr(e)[:200]))
                continue
            await self.graph.update_node(rid, CollectionNames.RECORDS.value, {
                "indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED",
                "virtualRecordId": getattr(record, "virtual_record_id", None) or f"vrid-{rid}"})

    async def sync(self, *, full: bool = False, expect_error: bool = False,
                   index: bool = True) -> BaseException | None:
        if self.connector is None:
            await self.start()
        assert self.connector is not None
        self.gl.clock.advance(180)  # syncs are minutes apart
        full, self.pending_full_sync, self.synced_once = full or self.pending_full_sync, False, True
        if full:
            await self.graph.delete_sync_points_by_connector_id(connector_id=self.connector_id)
        status = "FULL_SYNCING" if full else "SYNCING"
        error: BaseException | None = None
        try:
            await sync_runner.run_sync_task(self.connector, self.connector_id, self.graph, self.logger,
                                            start_status=status)
        except Exception as exc:
            error = exc
            self.sync_errors.append(exc)
        await self._drain()
        if index:
            await self.index()
        if expect_error:
            if error is None:
                raise AssertionError("the sync reported success")
            return error
        if error is not None:
            raise error
        return None

    async def _drain(self) -> None:
        assert self.connector is not None
        task = getattr(self.connector, "_code_file_timestamp_backfill_task", None)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    async def restart(self) -> None:
        if self.connector is not None:
            await self.connector.cleanup()
        self.connector = None
        await self.start()

    # ------------------------------------------------------------------ reading PipesHub

    async def record_docs(self, **filters: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        docs = await self.graph.get_nodes_by_filters(
            CollectionNames.RECORDS.value, {"connectorId": self.connector_id, **filters})
        return [{**d, "id": d.get("id") or d.get("_key")} for d in docs]

    async def live_docs(self) -> dict[str, dict[str, Any]]:
        return {d["externalRecordId"]: d for d in await self.record_docs()
                if not d.get("isPlaceholder") and not d.get("isDeleted")}

    async def by_type(self, record_type: str) -> dict[str, dict[str, Any]]:
        return {k: d for k, d in (await self.live_docs()).items() if d.get("recordType") == record_type}

    async def names(self, record_type: str = "TICKET") -> list[str]:
        return sorted(str(d.get("recordName")) for d in (await self.by_type(record_type)).values())

    async def record(self, external_id: str) -> Record | None:
        return await self.processor.get_record_by_external_id(self.connector_id, external_id)

    async def visible_to(self, email: str) -> set[str]:
        docs = await self.record_docs()
        by_id = {d["id"]: d["externalRecordId"] for d in docs}
        allowed = await self.graph.filter_accessible_record_ids(list(by_id), self.users[email], self.org_id)
        return {by_id[i] for i in allowed if i in by_id}

    async def groups(self) -> dict[str, dict[str, Any]]:
        docs = await self.graph.get_nodes_by_filters(
            CollectionNames.RECORD_GROUPS.value, {"connectorId": self.connector_id})
        return {d["externalGroupId"]: d for d in docs}

    # ------------------------------------------------------------------ GitLab's answer

    def gl_user(self, email: str) -> User | None:
        for u in self.gl.users.values():
            if email in (u.email, u.public_email):
                return u
        return None

    def scoped(self) -> Project | None:
        p = self.gl.projects.get(self.scope) if self.scope else None
        return p if p and not p.deleted else None

    def _items(self, p: Project) -> list[Any]:
        return [*p.issues.values(), *p.mrs.values()]

    def attachment_owners(self, url: str) -> list[tuple[Any, bool]]:
        """(item, only through internal notes) for each live issue/MR of the scoped project linking ``url``."""
        m = _UPLOAD_RE.search(url)
        p = self.scoped()
        if not m or p is None or int(m.group(1)) != p.id:
            return []
        href = f"/uploads/{m.group(2)}/{m.group(3)}"
        out = []
        for item in self._items(p):
            if item.deleted:
                continue
            public = href in (item.description or "") or any(href in n.body for n in item.notes if not n.internal)
            internal = any(href in n.body for n in item.notes if n.internal)
            if public or internal:
                out.append((item, not public))
        return out

    def _readable_item(self, viewer: User | None, item: Any) -> bool:  # noqa: ANN401
        return self.gl.can_read_issue(viewer, item) if isinstance(item, Issue) else self.gl.can_read_mr(viewer, item)

    def _truth(self, viewer: User | None, doc: dict[str, Any]) -> bool | None:
        """Would GitLab show this record to ``viewer``? None = gone or out of scope."""
        external_id = doc["externalRecordId"]
        p = self.scoped()
        if p is None:
            return None
        rtype = doc.get("recordType")
        if rtype == "TICKET":
            item = next((i for i in p.issues.values() if str(i.id) == external_id and not i.deleted), None)
            return None if item is None else self.gl.can_read_issue(viewer, item)
        if rtype == "PULL_REQUEST":
            item = next((m for m in p.mrs.values() if str(m.id) == external_id and not m.deleted), None)
            return None if item is None else self.gl.can_read_mr(viewer, item)
        if rtype == "FILE" and "/uploads/" in external_id:
            owners = self.attachment_owners(external_id)
            if not owners:
                return None
            return any(self._readable_item(viewer, o) and (not only_internal or self.gl.can_read_internal_note(viewer, p))
                       for o, only_internal in owners)
        m = _CODE_RE.match(external_id)
        if m:
            pwn, kind, path = m.groups()
            if pwn != p.path_with_namespace:
                return None
            tree = p.tree()
            exists = path in tree if kind == "blob" else any(f.startswith(path + "/") for f in tree)
            return self.gl.can_read_code(viewer, p) if exists else None
        return None

    async def diff(self, emails: list[str] | None = None, *, check_missing: bool = True) -> Diff:
        out = Diff()
        docs = await self.live_docs()
        p = self.scoped()
        if check_missing and p is not None:
            token_user = self.gl.sync_token.user if self.gl.sync_token else None
            for i in p.issues.values():
                if not i.deleted and self.gl.can_read_issue(token_user, i) and str(i.id) not in docs:
                    out.missing.append(f"issue #{i.iid} {i.title}")
            for m in p.mrs.values():
                if not m.deleted and self.gl.can_read_mr(token_user, m) and str(m.id) not in docs:
                    out.missing.append(f"mr !{m.iid} {m.title}")
            policy = PathPolicy()
            for path in p.tree():
                name = path.rsplit("/", 1)[-1]
                if name.startswith(".") or not policy.keeps_file(path, name):
                    continue
                if f"/{p.path_with_namespace}/-/blob/HEAD/{path}" not in docs:
                    out.missing.append(f"file {path}")
        seen_by = {email: await self.visible_to(email) for email in self.users}
        reachable = set().union(*seen_by.values()) if seen_by else set()
        token_user = self.gl.sync_token.user if self.gl.sync_token else None
        for external_id, doc in docs.items():
            # Gone from GitLab or out of scope, and still readable by someone in PipesHub.
            # (A full sync unlinks what it did not see; an unlinked node is reachable by nobody.)
            if self._truth(token_user, doc) is None and external_id in reachable:
                out.stale.append(f"{doc.get('recordType')}:{doc.get('recordName', external_id)}")
        for email in emails or list(self.users):
            seen = seen_by[email]
            viewer = self.gl_user(email)
            # The PipesHub user who connected the connector also reads what its GitLab
            # account reads (the product's authenticatedAs link), whatever their email.
            linked = token_user if email == self.creator_email else None
            for external_id, doc in docs.items():
                truth = bool(self._truth(viewer, doc)) or (linked is not None and bool(self._truth(linked, doc)))
                name = f"{doc.get('recordType')}:{doc.get('recordName', external_id)}"
                if external_id in seen and not truth:
                    out.over.append((email, name))
                if truth and external_id not in seen:
                    out.under.append((email, name))
        return out

    # ------------------------------------------------------------------ content

    async def typed(self, external_id: str) -> Record:
        base = await self.record(external_id)
        assert base is not None, f"{external_id} is not indexed"
        record = await self.graph.get_record_by_id(base.id)
        assert record is not None
        return record

    async def stream(self, external_id: str) -> bytes:
        assert self.connector is not None
        record = await self.typed(external_id)
        response = await self.connector.stream_record(record)
        chunks = [chunk async for chunk in response.body_iterator]
        return b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)

    async def stream_text(self, external_id: str) -> str:
        body = await self.stream(external_id)
        try:
            container = json.loads(body)
        except ValueError:
            return body.decode(errors="replace")
        parts = []
        for group in container.get("block_groups", []):
            parts.append(str(group.get("data") or ""))
            for comment_list in group.get("comments") or []:
                parts.extend(str(c.get("text") or "") for c in comment_list or [])
        parts.extend(str(block.get("data") or "") for block in container.get("blocks", []))
        return "\n".join(parts)

    async def checkpoint(self, group: str) -> dict[str, Any]:
        from app.connectors.core.base.sync_point.sync_point import (
            generate_record_sync_point_key,
        )
        assert self.connector is not None
        key = generate_record_sync_point_key(Connectors.GITLAB.value, group, "")
        return await self.connector.record_sync_point.read_sync_point(key) or {}

    def api_calls(self, *, since: int = 0, resource: str | None = None) -> list[Any]:
        return [c for c in self.gl.calls[since:] if resource is None or c.resource == resource]

    # ------------------------------------------------------------------ teardown

    async def destroy(self) -> None:
        if self.connector is not None:
            with contextlib.suppress(Exception):
                await self.connector.cleanup()
        self.server.stop()
        with contextlib.suppress(Exception):
            await self.graph.delete_connector_instance(self.connector_id, self.org_id)


def acme(gl: GitLab, domain: str = "acme.com") -> dict[str, Any]:
    """A small ordinary instance: the sync account (group owner), a maintainer, a developer,
    a reporter, a planner, a guest, an outsider, one group and one private project."""
    admin = gl.add_user("admin", f"admin@{domain}")
    maya = gl.add_user("maya", f"maya@{domain}")      # maintainer
    dev = gl.add_user("dev", f"dev@{domain}")         # developer
    rita = gl.add_user("rita", f"rita@{domain}")      # reporter
    pat = gl.add_user("pat", f"pat@{domain}")         # planner
    gus = gl.add_user("gus", f"gus@{domain}")         # guest
    olga = gl.add_user("olga", f"olga@{domain}")      # no access
    group = gl.add_group("acme", members={admin: 50, maya: 40})
    project = gl.add_project(group, "widgets", members={dev: 30, rita: 20, pat: 15, gus: 10},
                             files={"README.md": "# widgets\n", "src/app.py": "print('hi')\n",
                                    "src/util/helpers.py": "def h():\n    return 1\n"})
    gl.set_sync_account(admin)
    return {"admin": admin, "maya": maya, "dev": dev, "rita": rita, "pat": pat, "gus": gus, "olga": olga,
            "group": group, "project": project}


__all__ = ["Diff", "Fault", "Group", "Issue", "MergeRequest", "Project", "User", "World", "acme", "Behaviour"]
