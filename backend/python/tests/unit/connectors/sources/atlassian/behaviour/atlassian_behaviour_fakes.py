"""Fakes for behaviour tests of the Atlassian connectors.

Only two things are faked: the Atlassian REST API (an ``httpx.MockTransport``
behind the connector's real HTTP client, so request building, auth headers and
response parsing all run for real) and our own databases (records, record
groups and sync checkpoints, kept in memory so a second sync sees what the
first one wrote).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import httpx

from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    matches_visibility,
)

if TYPE_CHECKING:
    from app.connectors.core.base.connector.connector_service import BaseConnector
    from app.models.entities import Record
    from app.sources.client.http.http_client import HTTPClient

Handler = Callable[[httpx.Request], httpx.Response]


def record_logs(connector: BaseConnector) -> MagicMock:
    """Record the connector's own log calls.

    Unlike caplog, this does not depend on global logging state, which other tests in
    the suite can leave disabled or non-propagating. The real logger is still called.
    """
    recorder = MagicMock(wraps=connector.logger)
    connector.logger = recorder
    return recorder


def logged(recorder: MagicMock, level: str = "error") -> list[str]:
    """Messages logged at ``level`` through a ``record_logs`` recorder, %-args applied."""
    return [
        str(call.args[0]) % call.args[1:] if len(call.args) > 1 else str(call.args[0])
        for call in getattr(recorder, level).call_args_list
        if call.args
    ]


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


def as_base_record(record: Record) -> Record:
    """What the graph stores return from an external-id lookup: a base Record.

    Both providers rebuild it with ``Record.from_arango_base_record``, so subclass
    fields such as ``is_file``, ``path`` and ``extension`` are gone.
    """
    from app.models.entities import Record

    return Record.model_validate(record.model_dump(include=set(Record.model_fields)))


class FakeGrantEdges:
    """``DataStoreProvider.transaction()`` of a ``FakeRecordsDb`` for its permission edges: an edge into a record per
    stored grant. ``fail`` makes the read fail as the real providers do: it raises with ``raise_on_error`` and
    otherwise returns no edges."""

    def __init__(self, db: "FakeRecordsDb") -> None:
        self.db = db
        self.fail = False

    @asynccontextmanager
    async def transaction(self, explicit: bool | None = None) -> AsyncIterator["FakeGrantEdges"]:
        yield self

    async def get_edges_to_node(self, node_id: str, edge_collection: str, *, raise_on_error: bool = False) -> list[dict]:
        from app.config.constants.arangodb import CollectionNames

        if self.fail:
            if raise_on_error:
                raise RuntimeError("database unavailable")
            return []
        assert edge_collection == CollectionNames.PERMISSION.value
        collection, _, key = node_id.partition("/")
        assert collection == CollectionNames.RECORDS.value
        return [
            {"_from": f"grantee/{i}", "_to": node_id}
            for external_id, record in self.db.records.items() if record.id == key
            for i, _ in enumerate(self.db.record_permissions.get(external_id) or [])
        ]


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
        self.deleted_groups: list[str] = []
        self.withdrawn_users: list[Any] = []
        self.deleted: list[str] = []
        self.content_updates: list[Any] = []
        self.permission_updates: list[tuple[Any, list[Any]]] = []
        self.reindexed: list[Any] = []
        self.fail_lookup_for: set[str] = set()
        # Records whose write the processor quarantined (schema refused): not stored, not raised, not seen.
        self.quarantined: set[str] = set()
        self.fail_delete_for: set[str] = set()
        self.fail_stored_permission_reads = False
        self.kept_records: list[str] = []
        self.full_sync = False
        self.unlinked_record_groups: list[str] = []
        self.fail_group_listing = False
        self.fail_note_kept_group = False
        self.data_store_provider = FakeGrantEdges(self)

    async def get_user_by_user_id(self, user_id: str, *, org_id: str | None = None) -> Optional[SimpleNamespace]:
        if not self.creator_email:
            return None
        return SimpleNamespace(email=self.creator_email, id=user_id)

    async def get_record_by_external_id(
        self, connector_id: str, external_record_id: str, *, raise_on_error: bool = False
    ) -> Optional[Record]:
        if external_record_id in self.fail_lookup_for:
            raise RuntimeError(f"database unavailable for {external_record_id}")
        stored = self.records.get(external_record_id)
        return None if stored is None else as_base_record(stored)

    async def get_records_by_parent(
        self, connector_id: str, parent_external_record_id: str, record_type: Optional[str] = None,
        *, visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[Record]:
        """Base-record copies, as the stores return: changing them does not change what is stored."""
        return [
            as_base_record(r) for r in self.records.values()
            if r.parent_external_record_id == parent_external_record_id and record_type in (None, r.record_type)
            and matches_visibility(r, visibility)
        ]

    async def on_new_records(self, records_with_permissions: list[tuple[Any, list[Any]]]) -> None:
        self.record_batches.append([rec for rec, _ in records_with_permissions])
        for record, permissions in records_with_permissions:
            self.records[record.external_record_id] = record
            self.record_permissions[record.external_record_id] = list(permissions)
            # A lookup failure fails the build; the close of the failed record is read back after its write.
            self.fail_lookup_for.discard(record.external_record_id)

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in groups:
            self.record_groups[group.external_group_id] = group
            if permissions is not None:  # None: the grants are unknown and the stored ones stay
                self.record_group_permissions[group.external_group_id] = list(permissions)

    async def on_new_app_users(self, users: list[Any]) -> None:
        self.app_users.extend(users)

    async def withdraw_app_user_access(self, connector_id: str, users: list[Any], *, drop_grants: bool = False) -> None:
        self.withdrawn_users.extend(users)

    async def on_new_user_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        self.user_groups.extend(groups)

    async def on_user_group_deleted(self, external_group_id: str, connector_id: str) -> bool:
        self.deleted_groups.append(external_group_id)
        self.user_groups = [(g, m) for g, m in self.user_groups if g.source_user_group_id != external_group_id]
        return True

    async def has_stored_permissions(
        self, entity: str, connector_id: str, external_id: str, *, keep: bool = False
    ) -> bool:
        """Answers from what this fake stored: a non-empty member or grant list is stored
        edges. With ``keep``, a stored node is noted as seen by the full sync in progress."""
        from app.config.constants.arangodb import CollectionNames

        if self.fail_stored_permission_reads:
            raise RuntimeError("database unavailable")
        if entity == "record_group":
            stored, collection = bool(self.record_group_permissions.get(external_id)), CollectionNames.RECORD_GROUPS.value
        elif entity == "user_group":
            saved = [members for group, members in self.user_groups if group.source_user_group_id == external_id]
            stored, collection = bool(saved and saved[-1]), CollectionNames.GROUPS.value
        elif entity == "role":
            stored, collection = bool(getattr(self, "app_roles", {}).get(external_id)), CollectionNames.ROLES.value
        else:
            raise ValueError(entity)
        seen = getattr(self, "full_sync_seen", None)
        if stored and keep and seen is not None:
            seen.add(collection, external_id)
            if entity == "record_group":
                # The groups and roles its kept grants name are kept with it.
                for permission in self.record_group_permissions.get(external_id) or []:
                    kind = str(getattr(permission, "entity_type", ""))
                    if kind.endswith("ROLE"):
                        seen.add(CollectionNames.ROLES.value, permission.external_id)
                    elif kind.endswith("GROUP"):
                        seen.add(CollectionNames.GROUPS.value, permission.external_id)
        return stored

    async def get_records_in_record_group(
        self, connector_id: str, external_group_id: str, limit: int, after_key: str | None = None,
        *, visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[Any]:
        rows = sorted(
            (r for r in self.records.values()
             if r.external_record_group_id == external_group_id and matches_visibility(r, visibility)),
            key=lambda r: r.id,
        )
        return [r for r in rows if after_key is None or r.id > after_key][:limit]

    def note_kept_records(self, connector_id: str, record_ids: list[str]) -> None:
        """Noted as seen by the full sync in progress, under the external ids its record writes are tracked by."""
        from app.config.constants.arangodb import CollectionNames

        self.kept_records.extend(record_ids)
        seen = getattr(self, "full_sync_seen", None)
        if seen is not None:
            ids = set(record_ids)
            for record in self.records.values():
                if record.id in ids:
                    seen.add(CollectionNames.RECORDS.value, record.external_record_id)

    def full_sync_running(self, connector_id: str) -> bool:
        return self.full_sync or getattr(self, "full_sync_seen", None) is not None

    async def get_all_user_groups(self, connector_id: str, *, raise_on_error: bool = False) -> list[Any]:
        if self.fail_group_listing:
            if not raise_on_error:
                return []  # as the store does
            raise RuntimeError("database unavailable")
        return list({g.source_user_group_id: g for g, _ in self.user_groups}.values())

    def note_kept_user_groups(self, connector_id: str, groups: list[Any]) -> None:
        from app.config.constants.arangodb import CollectionNames

        seen = getattr(self, "full_sync_seen", None)
        if seen is not None:
            for group in groups:
                seen.add(CollectionNames.GROUPS.value, group.source_user_group_id)

    async def note_kept_user_group(self, connector_id: str, external_group_id: str) -> None:
        from app.config.constants.arangodb import CollectionNames

        if self.fail_note_kept_group:
            raise RuntimeError("database unavailable")
        seen = getattr(self, "full_sync_seen", None)
        if seen is not None:
            seen.add(CollectionNames.GROUPS.value, external_group_id)

    async def note_kept_record_group(self, connector_id: str, external_group_id: str) -> None:
        from app.config.constants.arangodb import CollectionNames

        seen = getattr(self, "full_sync_seen", None)
        if seen is not None:
            seen.add(CollectionNames.RECORD_GROUPS.value, external_group_id)

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        if record_id in self.fail_delete_for:
            self.fail_delete_for.discard(record_id)
            raise RuntimeError(f"delete of {record_id} failed")
        self.deleted.append(record_id)
        for external_id, record in list(self.records.items()):
            if record.id == record_id:
                del self.records[external_id]

    async def on_record_content_update(
        self, record: Record, permissions: list[Any] | None = None, *, content_changed: bool = True,
    ) -> None:
        self.content_updates.append(record)
        # The processor upserts the record before it publishes updateRecord.
        self.records[record.external_record_id] = record
        if permissions is not None:
            self.record_permissions[record.external_record_id] = list(permissions)

    async def on_record_metadata_update(self, record: Record) -> None:
        # Source metadata only (timestamps, name): the record is upserted, its grants are left as they are.
        self.records[record.external_record_id] = record

    async def on_updated_record_permissions(self, record: Record, permissions: list[Any]) -> None:
        self.permission_updates.append((record, permissions))
        # The stored grants are replaced, and the record inherits from its parent if it says so.
        self.record_permissions[record.external_record_id] = list(permissions)
        stored = self.records.get(record.external_record_id)
        if stored is not None:
            self.records[record.external_record_id] = stored.model_copy(
                update={"inherit_permissions": record.inherit_permissions}
            )

    async def reindex_existing_records(self, records: list[Any]) -> None:
        # The real one builds the indexing event from each record; a base Record raises here.
        for record in records:
            record.to_kafka_record()
        self.reindexed.extend(records)

    async def get_typed_record_by_external_id(self, connector_id: str, external_record_id: str) -> Optional[Record]:
        if external_record_id in self.fail_lookup_for:
            raise RuntimeError(f"database unavailable for {external_record_id}")
        return self.records.get(external_record_id)


class FakeCheckpointStore:
    """In-memory sync-point collection behind ``DataStoreProvider.transaction()``."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> Optional[dict[str, Any]]:
        return self.sync_points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        # Neo4j (the default DATA_STORE) only stores primitives or lists of primitives as properties.
        for field, value in data.items():
            items = value if isinstance(value, list) else [value]
            if any(isinstance(item, (dict, list, tuple, set)) for item in items):
                raise TypeError(f"sync point field {field!r} is not a primitive: {value!r}")
        # Both graph stores merge fields into an existing sync point (Neo4j SET +=, Arango UPDATE).
        self.sync_points[key] = {**self.sync_points.get(key, {}), **data}

    async def delete_sync_point(self, key: str) -> None:
        self.sync_points.pop(key, None)

    def values_for(self, key_suffix: str) -> Optional[dict[str, Any]]:
        for key, value in self.sync_points.items():
            if key.endswith(key_suffix):
                return value
        return None

    @asynccontextmanager
    async def transaction(self, explicit: bool | None = None) -> AsyncIterator["FakeCheckpointStore"]:
        yield self


class FakeConfigService:
    """Serves one connector's etcd config document (auth + filters)."""

    def __init__(self, connector_id: str, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = config
        self.saved: dict[str, Any] = {}

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        if path == f"/services/connectors/{self.connector_id}/config":
            return self.config
        return self.saved.get(path, default)

    async def set_config(self, path: str, value: object, **_: object) -> bool:
        self.saved[path] = value
        return True

    async def compare_and_set_config(self, path: str, expected: object, new: object) -> bool:
        if await self.get_config(path) != expected:
            return False
        return await self.set_config(path, new)


async def run_full_sync(connector: Any, db: "FakeRecordsDb") -> AsyncMock:  # noqa: ANN401
    """Run ``connector.run_sync`` as ``run_sync_task`` runs a full sync (#4119).

    The fake db notes what the sync writes. After a sync that returns, the stand-in
    graph provider takes the stored members and grants away from every group, role and
    record group the sync did not write, unless the connector reports an incomplete read,
    as ``unlink_nodes_missed_by_full_sync`` does. Returns that graph provider.
    """
    from app.config.constants.arangodb import AppStatus, CollectionNames
    from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
    from app.connectors.core.sync.sync_runner import run_sync_task

    seen = FullSyncSeen()
    tracked = {
        "on_new_user_groups": (CollectionNames.GROUPS.value, lambda g: g.source_user_group_id),
        "on_new_app_roles": (CollectionNames.ROLES.value, lambda r: r.source_role_id),
        "on_new_record_groups": (CollectionNames.RECORD_GROUPS.value, lambda g: g.external_group_id),
        "on_new_records": (CollectionNames.RECORDS.value, lambda r: r.external_record_id),
    }
    originals = {name: getattr(db, name) for name in tracked}

    def tracking(name: str) -> Any:  # noqa: ANN401
        collection, key = tracked[name]

        async def write(items: list[tuple[Any, list[Any]]]) -> None:
            await originals[name](items)
            for item, _ in items:  # as the processor notes them: once the write commits
                if name != "on_new_records" or key(item) not in db.quarantined:
                    seen.add(collection, key(item))

        return write

    async def unlink(connector_id: str, by_collection: dict[str, set[str]], **_: object) -> tuple[int, bool]:
        removed = 0
        groups = {g.source_user_group_id: g for g, _ in db.user_groups}
        for external_id, group in groups.items():
            if external_id not in by_collection[CollectionNames.GROUPS.value]:
                db.user_groups.append((group, []))
                if hasattr(db, "groups_saved"):
                    db.groups_saved[external_id] = []
                removed += 1
        for external_id in list(getattr(db, "app_roles", {})):
            if external_id not in by_collection[CollectionNames.ROLES.value]:
                db.app_roles[external_id] = []
                removed += 1
        for external_id in list(db.record_group_permissions):
            if external_id not in by_collection[CollectionNames.RECORD_GROUPS.value]:
                db.record_group_permissions[external_id] = []
                # Its records lose their BELONGS_TO and inherited-permission links into it too.
                db.unlinked_record_groups.append(external_id)
                removed += 1
        # The edges of an unseen node go in both directions, so a seen record group also
        # loses its grants from an unseen group or role.
        unseen = {
            "ROLE": {r for r in getattr(db, "app_roles", {}) if r not in by_collection[CollectionNames.ROLES.value]},
            "GROUP": {g for g in groups if g not in by_collection[CollectionNames.GROUPS.value]},
        }
        for external_id, grants in db.record_group_permissions.items():
            kept = [
                p for p in grants
                if p.external_id not in unseen.get(str(getattr(p, "entity_type", "")).rsplit(".", 1)[-1], set())
            ]
            removed += len(grants) - len(kept)
            db.record_group_permissions[external_id] = kept
        return removed, True

    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"id": connector.connector_id, "status": AppStatus.FULL_SYNCING.value})
    graph.unlink_nodes_missed_by_full_sync = AsyncMock(side_effect=unlink)
    for name in tracked:
        setattr(db, name, tracking(name))
    db.begin_full_sync_run = lambda connector_id: seen
    db.end_full_sync_run = lambda connector_id, run: None
    db.full_sync_seen = seen
    try:
        await run_sync_task(
            connector, connector.connector_id, graph, logging.getLogger("test.full_sync"),
            start_status=AppStatus.FULL_SYNCING.value,
        )
    finally:
        for name, original in originals.items():
            setattr(db, name, original)
        del db.begin_full_sync_run, db.end_full_sync_run, db.full_sync_seen
    return graph
