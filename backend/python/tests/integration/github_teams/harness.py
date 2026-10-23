"""One PipesHub org wired to one simulated GitHub.

Real: the GitHub Teams connector and every helper module, its async data source and
httpx client (over a real socket to the simulator), DataSourceEntitiesProcessor,
GraphDataStore, SyncPoint and the graph database (Neo4j or ArangoDB). Simulated:
GitHub (``sim``), the message broker (events are captured) and the config store.

``World.sync(full=True)`` repeats what ``EventService`` does before a full sync (delete
the connector's sync points and sync edges), then calls ``run_sync``. A filter change
makes the next sync a full one, as ``pendingFullSync`` does in the product.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import types
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import httpx

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.sync import sync_runner
from app.connectors.sources.github_teams import runtime as runtime_module
from app.connectors.sources.github_teams.connector import GitHubTeamsConnector
from app.sources.client.github import github as client_module
from app.sources.external.github import github_async as ds_module
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.github_teams.sim import spec as gh_spec
from tests.integration.github_teams.sim.model import (
    Behaviour,
    Issue,
    PullRequest,
    Repo,
    User,
)
from tests.integration.github_teams.sim.model import World as GitHub
from tests.integration.github_teams.sim.rest import ApiServer, Internet
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from app.models.entities import Record

ARANGO_DB = "github_teams_it"

# Fields GitHub really leaves out of some payloads, so the connector's ``getattr(x, f,
# None)`` on them is a real code path rather than a simulator gap. Every other read of a
# field the simulator did not put in the payload fails the test.
ABSENT_ON_PURPOSE: dict[str, tuple[str, Any]] = {
    # field: (why GitHub leaves it out, which payloads it may be missing from)
    "email": ("simple-user (member/collaborator listings) has no email field", None),
    "name": ("simple-user has no name field", None),
    "previous_filename": ("diff-entry carries it only for renamed files", None),
    "patch": ("diff-entry omits it for binary or very large diffs", None),
    "size": ("git tree entries for folders have no size", lambda d: d.get("type") == "tree"),
    "in_reply_to_id": ("review comments carry it only on replies", lambda d: "pull_request_review_id" in d),
    "created_at": ("pull-request-review has submitted_at only", lambda d: "submitted_at" in d),
    "updated_at": ("pull-request-review has submitted_at only", lambda d: "submitted_at" in d),
}

async def connect(backend: str, monkeypatch: Any) -> Any:  # noqa: ANN401
    """The production graph provider on the integration graph store (NEO4J_IT_URI /
    ARANGO_IT_URL, see ``tests.integration.real_graph``)."""
    logger = logging.getLogger(f"github_teams_it.{backend}")
    try:
        if backend == "neo4j":
            return await connect_neo4j(logger, monkeypatch)
        return await connect_arango(logger, ARANGO_DB)
    except Exception as exc:
        backend_unavailable(backend, exc)


# Every node this suite writes carries its org or its connector; nothing else is touched.
_OWNED_BY = ("orgId", "connectorId")


async def remove_owned(graph: Any, org_id: str, connector_id: str) -> None:  # noqa: ANN401
    """Delete what one World wrote, and only that: the database is shared with every
    other real-graph suite."""
    with contextlib.suppress(Exception):
        await graph.delete_connector_instance(connector_id, org_id)
    for collection in CollectionNames:
        keys: set[str] = set()
        for prop, value in zip(_OWNED_BY, (org_id, connector_id)):
            try:
                docs = await graph.get_nodes_by_filters(collection.value, {prop: value}) or []
            except Exception:
                continue
            keys.update(str(d.get("_key") or d.get("id")) for d in docs if d.get("_key") or d.get("id"))
        if keys:
            await graph.delete_nodes_and_edges(sorted(keys), collection.value)
    for key, collection in ((connector_id, CollectionNames.APPS), (org_id, CollectionNames.ORGS)):
        await graph.delete_nodes_and_edges([key], collection.value)


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
        self.fail_reads = 0  # the next N reads of the connector config raise

    async def get_config(self, path: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
        if path == f"/services/connectors/{self.connector_id}/config":
            if self.fail_reads:
                self.fail_reads -= 1
                raise ConnectionError("config store unavailable")
            return json.loads(json.dumps(self.config))
        if path == "/services/platform/settings":
            return self.platform or default
        return default


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
    over: list[tuple[str, str]] = field(default_factory=list)   # PipesHub shows, GitHub would not
    under: list[tuple[str, str]] = field(default_factory=list)  # GitHub shows, PipesHub does not
    stale: list[str] = field(default_factory=list)              # indexed, gone from GitHub or scope
    missing: list[str] = field(default_factory=list)            # in scope on GitHub, not indexed

    def clean(self) -> bool:
        return not (self.over or self.under or self.stale or self.missing)

    def __str__(self) -> str:
        return (f"over-granted={sorted(self.over)} under-granted={sorted(self.under)} "
                f"stale={sorted(self.stale)} missing={sorted(self.missing)}")


class World:
    def __init__(self, backend: str, graph: Any, gh: GitHub) -> None:  # noqa: ANN401
        self.backend, self.graph, self.gh = backend, graph, gh
        self.api = ApiServer(gh)
        self.internet = Internet(gh, self.api)
        suffix = uuid.uuid4().hex[:10]
        self.org_id = f"org-gh-{suffix}"
        self.connector_id = f"ghteams-{suffix}"
        self.creator_id = f"user-creator-{suffix}"
        self.creator_email = "admin@acme.com"
        self.logger = logging.getLogger(f"github_teams_it.world.{suffix}")
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
        self.connector: GitHubTeamsConnector | None = None
        self.sleeps: list[float] = []
        self.scope: set[int] = set()  # repo ids the sync filter selects
        self.synced_once = False
        self.pending_full_sync = False
        self.absent_reads: list[str] = []
        self.index_failures: list[tuple[str, str]] = []
        self.http_timeout = 30.0
        self.soft_delete = False

    # ------------------------------------------------------------------ setup

    @classmethod
    async def create(cls, backend: str, monkeypatch: Any, *, behaviour: Behaviour | None = None,  # noqa: ANN401
                     soft_delete: bool = False) -> "World":
        graph = await connect(backend, monkeypatch)
        world = cls(backend, graph, GitHub(behaviour))
        world.soft_delete = soft_delete
        await world.api.start()
        world._patch(monkeypatch)
        now = get_epoch_timestamp_in_ms()
        await graph.batch_upsert_nodes(
            [{"id": world.org_id, "name": "Acme", "accountType": "enterprise", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.ORGS.value)
        await graph.batch_upsert_nodes(
            [{"id": world.connector_id, "name": "GitHub Teams", "type": Connectors.GITHUB_TEAMS.value,
              "appGroup": "Github", "scope": "team", "isActive": True, "createdBy": world.creator_id,
              "authType": "API_TOKEN", "isConfigured": True, "isAuthenticated": True, "orgId": world.org_id,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.APPS.value)
        await world.add_pipeshub_user(world.creator_email, user_id=world.creator_id)
        return world

    def _patch(self, monkeypatch: Any) -> None:  # noqa: ANN401
        world, gh = self, self.gh

        def _async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:  # noqa: ANN401
            kwargs["transport"] = world.internet
            kwargs["timeout"] = min(float(kwargs.get("timeout") or 30.0), world.http_timeout)
            return httpx.AsyncClient(*args, **kwargs)

        shim = types.SimpleNamespace(**{n: getattr(httpx, n) for n in dir(httpx) if not n.startswith("__")})
        shim.AsyncClient = _async_client
        monkeypatch.setattr(ds_module, "httpx", shim)

        # GitHub's clock is the simulator's: reset epochs and Retry-After are judged by it.
        clock = types.SimpleNamespace(time=lambda: gh.clock.t, monotonic=lambda: gh.clock.t)
        monkeypatch.setattr(ds_module, "time", clock)
        monkeypatch.setattr(client_module, "time", clock)
        monkeypatch.setattr(runtime_module, "time", clock)

        async def _sleep(delay: float, *_: Any, **__: Any) -> None:  # noqa: ANN401
            world.sleeps.append(delay)
            gh.clock.advance(delay)

        aio = types.SimpleNamespace(**{n: getattr(asyncio, n) for n in dir(asyncio) if not n.startswith("__")})
        aio.sleep = _sleep
        monkeypatch.setattr(runtime_module, "asyncio", aio)

        original = ds_module.GhObject.__getattr__

        def audited(obj: Any, name: str) -> Any:  # noqa: ANN401
            try:
                value = original(obj, name)
            except AttributeError:
                allowed = ABSENT_ON_PURPOSE.get(name)
                if not name.startswith("_") and not (allowed and (allowed[1] is None or allowed[1](obj._data))):
                    gh.gaps.append(f"connector read field {name!r}, which the simulator did not put in "
                                   f"the payload (keys: {sorted(obj._data)[:12]}...)")
                world.absent_reads.append(name)
                raise
            if value == gh_spec.FILLER_STR or value == gh_spec.FILLER_INT or (
                    isinstance(value, str) and gh_spec.FILLER_STR in value):
                gh.gaps.append(f"connector read field {name!r}, which the simulator does not model")
            return value

        monkeypatch.setattr(ds_module.GhObject, "__getattr__", audited)

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
        # As the product's user-added flow does: every org user is in the org's "All" team.
        await self.graph.add_user_to_all_team(self.org_id, key)
        self.users[email] = user_id
        return user_id

    def configure(self, repos: list[Repo], *, indexing: dict[str, bool] | None = None,
                  repo_names: list[str] | None = None) -> None:
        token = self.gh.sync_token
        assert token is not None, "call gh.set_sync_account first"
        names = repo_names if repo_names is not None else [r.full_name for r in repos]
        if self.synced_once:
            self.pending_full_sync = True
        self.scope = {r.id for r in repos}
        self.config.clear()
        self.config.update({
            "auth": {"authType": "API_TOKEN", "token": token.value}, "credentials": {},
            "filters": {
                "sync": {"values": {"repo_ids": {"operator": "in", "type": "select", "value": names}} if names else {}},
                "indexing": {"values": {k: {"operator": "is", "type": "boolean", "value": v}
                                        for k, v in (indexing or {}).items()}},
            }})

    def configure_orgs(self, orgs: list[str]) -> None:
        """A legacy configuration that selects whole organizations (no repository)."""
        self.configure([], repo_names=[])
        self.config["filters"]["sync"]["values"] = {
            "org_ids": {"operator": "in", "type": "multiselect", "value": orgs}}
        self.scope = {r.id for r in self.gh.repos.values() if r.owner.login in orgs and not r.deleted}

    def select(self, repos: list[Repo]) -> None:
        """The admin picks a different repository: the next sync is a full one."""
        self.config["filters"]["sync"]["values"] = {
            "repo_ids": {"operator": "in", "type": "select", "value": [r.full_name for r in repos]}}
        self.scope = {r.id for r in repos}
        if self.synced_once:
            self.pending_full_sync = True

    def set_window(self, start_seconds: int) -> None:
        """The admin sets the "updated since" window (GitHub time): a filter change."""
        self.config["filters"]["sync"]["values"]["modified"] = {
            "operator": "is_after", "type": "datetime", "value": {"start": start_seconds * 1000, "end": None}}
        if self.synced_once:
            self.pending_full_sync = True

    def set_indexing(self, **values: bool) -> None:
        self.config["filters"]["indexing"]["values"] = {
            k: {"operator": "is", "type": "boolean", "value": v} for k, v in values.items()}
        if self.synced_once:
            self.pending_full_sync = True

    def trash(self, on: bool = True) -> None:
        assert self.config_service is not None
        self.config_service.platform = {"featureFlags": {"ENABLE_SOFT_DELETE": on}} if on else {}

    async def start(self) -> GitHubTeamsConnector:
        if self.config_service is None:
            self.config_service = ConfigService(self.connector_id, self.config)
            if self.soft_delete:
                self.trash(True)
            self.processor.config_service = self.config_service
        connector = GitHubTeamsConnector(self.logger, self.processor, self.store, self.config_service,
                                         self.connector_id, "team", self.creator_id)
        assert await connector.init() is True, self.logs.text()[-2000:]
        self.connector = connector
        return connector

    # ------------------------------------------------------------------ running

    async def index(self) -> None:
        """What the indexing service does between syncs: stream every record a
        newRecord/updateRecord/reindexRecord event named, unless its indexing is off.
        A failed stream is left for indexing to retry (recorded, not raised)."""
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
            # What the indexing service writes back once a record is processed.
            await self.graph.update_node(rid, CollectionNames.RECORDS.value, {
                "indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED",
                "virtualRecordId": getattr(record, "virtual_record_id", None) or f"vrid-{rid}"})

    async def sync(self, *, full: bool = False, expect_error: bool = False,
                   index: bool = True) -> BaseException | None:
        if self.connector is None:
            await self.start()
        assert self.connector is not None
        self.gh.tick(180)  # syncs are minutes apart
        full, self.pending_full_sync, self.synced_once = full or self.pending_full_sync, False, True
        # A full sync as the product runs one (EventService, then sync_runner): the sync
        # points go first; links are not deleted up front. The processor notes what the
        # sync writes, and only a sync that ran to the end unlinks what it did not write.
        processor, seen = None, None
        if full:
            await self.graph.delete_sync_points_by_connector_id(connector_id=self.connector_id)
            processor, seen = sync_runner._begin_full_sync_run(self.connector, self.connector_id)
        try:
            await self.connector.run_sync()
            if seen is not None:
                assert await sync_runner._unlink_what_full_sync_missed(
                    self.connector, self.connector_id, self.graph, self.logger, seen
                ), "the unlink after the full sync failed"
        except Exception as exc:
            await self._drain()
            if index:
                await self.index()
            if expect_error:
                return exc
            raise
        finally:
            if seen is not None:
                processor.end_full_sync_run(self.connector_id, seen)
        await self._drain()
        if index:
            await self.index()
        if expect_error:
            raise AssertionError("the sync reported success")
        return None

    async def _drain(self) -> None:
        assert self.connector is not None
        task = getattr(self.connector.repos.timestamps, "_task", None)
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
        return {d["externalGroupId"]: d for d in docs if not d.get("isDeletedAtSource")}

    # ------------------------------------------------------------------ GitHub's answer

    def gh_user(self, email: str) -> User | None:
        for u in self.gh.users.values():
            if email in (u.email, u.corporate_email):
                return u
        return None

    def _item(self, external_id: str) -> tuple[Repo | None, Any]:
        """(repo, item) for an issue, PR, code path or attachment; item None = gone."""
        gh = self.gh
        head, _, rest = external_id.lstrip("/").partition("/")
        if head.isdigit():
            repo = gh.repos.get(int(head))
            if repo is None or repo.deleted:
                return repo, None
            kind, _, tail = rest.partition("/")
            if kind == "issues":
                if not repo.has_issues:  # GitHub hides every issue (410) while they are off
                    return repo, None
                issue = repo.issues.get(int(tail))
                return repo, issue if issue and not issue.deleted and issue.transferred_to is None else None
            if kind == "pull":
                return repo, repo.pulls.get(int(tail))
            tree = repo.head().tree if repo.head() else {}
            if kind == "blob":
                return repo, tail if tail in tree else None
            if kind == "tree":
                return repo, tail if any(p.startswith(tail + "/") for p in tree) else None
        return None, None

    def attachment_owners(self, url: str) -> list[Any]:
        """Live issues and PRs whose text links the upload."""
        owners = []
        for repo in self.gh.repos.values():
            if repo.deleted:
                continue
            items: list[Any] = [i for i in repo.issues.values()
                                if repo.has_issues and not i.deleted and i.transferred_to is None]
            items += list(repo.pulls.values())
            for item in items:
                texts = [item.body or "", *(c.body for c in item.comments)]
                texts += [c.body for c in getattr(item, "review_comments", [])]
                texts += [r.body for r in getattr(item, "reviews", [])]
                if any(url in t for t in texts):
                    owners.append(item)
        return owners

    def _truth(self, viewer: User | None, doc: dict[str, Any]) -> bool | None:
        """Would GitHub show this record to ``viewer``? None = gone or out of scope."""
        external_id = doc["externalRecordId"]
        if doc.get("recordType") == "FILE" and external_id.startswith("https://"):
            url, _, parent = external_id.partition("#")
            owners = [o for o in self.attachment_owners(url) if o.repo.id in self.scope]
            if parent:
                owners = [o for o in owners if f"{o.repo.id}/{'pull' if isinstance(o, PullRequest) else 'issues'}/{o.number}" == parent]
            if not owners:
                return None
            return any(self._readable(viewer, o.repo) for o in owners)
        repo, item = self._item(external_id)
        if repo is None or item is None or repo.id not in self.scope:
            return None
        return self._readable(viewer, repo)

    def _readable(self, viewer: User | None, repo: Repo) -> bool:
        # Public repositories are mirrored as readable by the whole PipesHub org (the
        # connector's documented intent: anyone could open them on github.com).
        return repo.visibility == "public" or self.gh.can_read(viewer, repo)

    async def diff(self, emails: list[str] | None = None, *, check_missing: bool = True) -> Diff:
        gh, out = self.gh, Diff()
        docs = await self.live_docs()
        if check_missing:
            for rid in self.scope:
                repo = gh.repos.get(rid)
                if repo is None or repo.deleted:
                    continue
                for issue in (repo.issues.values() if repo.has_issues else ()):
                    if not issue.deleted and issue.transferred_to is None and f"{rid}/issues/{issue.number}" not in docs:
                        out.missing.append(f"issue {repo.name}#{issue.number}")
                for pr in repo.pulls.values():
                    if f"{rid}/pull/{pr.number}" not in docs:
                        out.missing.append(f"pr {repo.name}#{pr.number}")
                for path in (repo.head().tree if repo.head() else {}):
                    if f"/{rid}/blob/{path}" not in docs:
                        out.missing.append(f"file {repo.name}:{path}")
        for external_id, doc in docs.items():
            if self._truth(None, doc) is None:
                out.stale.append(f"{doc.get('recordType')}:{doc.get('recordName', external_id)}")
        for email in emails or list(self.users):
            seen = await self.visible_to(email)
            viewer = self.gh_user(email)
            # The user who connected the connector also reads what its GitHub account reads
            # (the product's authenticatedAs link), whatever their own email.
            linked = self.gh.token_user(self.gh.sync_token) if email == self.creator_email else None
            for external_id, doc in docs.items():
                truth = self._truth(viewer, doc) or (linked is not None and self._truth(linked, doc))
                name = f"{doc.get('recordType')}:{doc.get('recordName', external_id)}"
                if external_id in seen and not truth:
                    out.over.append((email, name))
                if truth and external_id not in seen:
                    out.under.append((email, name))
        return out

    # ------------------------------------------------------------------ content

    async def typed(self, external_id: str) -> Record:
        """The record as the stream route loads it (``get_record_by_id``: the typed record)."""
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
        parts = [str(group.get("data") or "") for group in container.get("block_groups", [])]
        parts += [str(block.get("data") or "") for block in container.get("blocks", [])]
        return "\n".join(parts)

    async def checkpoint(self, group: str) -> dict[str, Any]:
        from app.connectors.core.base.sync_point.sync_point import (
            generate_record_sync_point_key,
        )
        assert self.connector is not None
        key = generate_record_sync_point_key(Connectors.GITHUB_TEAMS.value, group, "")
        return await self.connector.record_sync_point.read_sync_point(key) or {}

    def api_calls(self, *, since: int = 0, resource: str | None = None) -> list[Any]:
        return [c for c in self.gh.calls[since:] if resource is None or c.resource == resource]

    # ------------------------------------------------------------------ teardown

    async def destroy(self) -> None:
        if self.connector is not None:
            with contextlib.suppress(Exception):
                await self.connector.cleanup()
        await self.api.stop()
        try:
            await remove_owned(self.graph, self.org_id, self.connector_id)
        finally:
            disconnect = getattr(self.graph, "disconnect", None)
            if disconnect is not None:
                await disconnect()


def acme(gh: GitHub) -> dict[str, Any]:
    """A small ordinary org: an owner (the sync account), two members, an outside
    collaborator, a team, and one private repository."""
    admin = gh.add_user("admin", "admin@acme.com")
    alice = gh.add_user("alice", "alice@acme.com")
    bob = gh.add_user("bob", "bob@acme.com")
    olga = gh.add_user("olga", "olga@contractor.io")
    org = gh.add_org("acme", owners=[admin], members=[alice, bob], base_permission=None)
    gh.set_sync_account(admin)
    core = gh.add_team(org, "core", [alice])
    repo = gh.add_repo(org, "widgets", files={"README.md": "# widgets\n", "src/app.py": "print('hi')\n"})
    core.repo_roles[repo.id] = "write"
    repo.collaborators[olga.id] = "read"
    return {"admin": admin, "alice": alice, "bob": bob, "olga": olga, "org": org, "core": core, "repo": repo}


__all__ = ["Diff", "Issue", "PullRequest", "Repo", "User", "World", "acme", "Behaviour"]
