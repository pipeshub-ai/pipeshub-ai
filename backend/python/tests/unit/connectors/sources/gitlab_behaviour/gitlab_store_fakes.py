"""In-memory stand-ins for our own databases, as the GitLab connector sees them.

``FakeRecordsDb`` replaces ``DataSourceEntitiesProcessor`` and keeps the
semantics the connector relies on: records and record groups are upserted by
external id, and writing a record group *replaces* its stored permissions
(``on_new_record_groups`` deletes the old permission edges first). A second
sync therefore sees exactly what the first one left behind.
"""

from __future__ import annotations

import copy
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.models.permission import EntityType, Permission, PermissionType

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from gitlab_server_fake import FakeGitLab

    from app.models.entities import AppUser, AppUserGroup, Record, RecordGroup


def principal(permission: Permission) -> str:
    if permission.entity_type == EntityType.USER:
        return str(permission.email)
    return f"group:{permission.external_id}"


class FakeRecordsDb:
    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.creators: dict[str, str] = {"creator-1": "owner@example.com"}
        self.org_user_emails: set[str] | None = None
        self.records: dict[str, Record] = {}
        self.record_permissions: dict[str, list[Permission]] = {}
        self.record_groups: dict[str, RecordGroup] = {}
        self.record_group_permissions: dict[str, list[Permission]] = {}
        self.app_users: dict[str, AppUser] = {}
        self.user_groups: dict[str, AppUserGroup] = {}
        self.record_writes: list[list[str]] = []
        self.group_writes: list[list[str]] = []
        self.deleted: list[str] = []
        self.moves: list[tuple[str, str]] = []
        self.reindexed: list[Record] = []
        self.fail_lookup_for: set[str] = set()
        self.fail_writes_for: set[str] = set()

    # ------------------------------------------------------------------ reads

    async def get_user_by_user_id(self, user_id: str) -> SimpleNamespace | None:
        email = self.creators.get(user_id)
        return SimpleNamespace(id=user_id, email=email) if email else None

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        if external_record_id in self.fail_lookup_for:
            raise RuntimeError(f"database unavailable for {external_record_id}")
        return self.records.get(external_record_id)

    async def get_records_by_parent(self, connector_id: str, parent_external_record_id: str, **_: object) -> list[Record]:
        return [r for r in self.records.values() if r.parent_external_record_id == parent_external_record_id]

    async def get_record_path(self, record_id: str) -> str | None:
        record = self.record_by_id(record_id)
        return getattr(record, "file_path", None) if record else None

    async def get_user_by_source_id(self, source_user_id: str, connector_id: str) -> SimpleNamespace | None:
        user = self.app_users.get(source_user_id)
        if user is None or (self.org_user_emails is not None and user.email not in self.org_user_emails):
            return None
        return SimpleNamespace(id=user.id, email=user.email)

    async def get_user_group_by_external_id(self, connector_id: str, external_id: str) -> AppUserGroup | None:
        return self.user_groups.get(external_id)

    # ------------------------------------------------------------------ writes

    async def on_new_records(self, records_with_permissions: list[tuple[Record, list[Permission]]]) -> None:
        doomed = self.fail_writes_for & {r.external_record_id for r, _ in records_with_permissions}
        if doomed:
            self.fail_writes_for -= doomed
            raise RuntimeError(f"records write failed for {sorted(doomed)}")
        self.record_writes.append([r.external_record_id for r, _ in records_with_permissions])
        for record, permissions in records_with_permissions:
            existing = self.records.get(record.external_record_id)
            if existing is not None:
                record.id = existing.id
                if record.source_created_at is None:
                    record.source_created_at = existing.source_created_at
                if record.source_updated_at is None:
                    record.source_updated_at = existing.source_updated_at
            self.records[record.external_record_id] = record
            self.record_permissions[record.external_record_id] = list(permissions or [])

    async def on_new_record_groups(self, groups: list[tuple[RecordGroup, list[Permission]]]) -> None:
        self.group_writes.append([g.external_group_id for g, _ in groups])
        for group, permissions in groups:
            existing = self.record_groups.get(group.external_group_id)
            group.id = existing.id if existing else str(uuid.uuid4())
            self.record_groups[group.external_group_id] = group
            self.record_group_permissions[group.external_group_id] = list(permissions or [])

    async def on_new_app_users(self, users: list[AppUser]) -> None:
        for user in users:
            self.app_users[user.source_user_id] = user

    async def on_new_user_groups(self, groups: list[tuple[AppUserGroup, list[Any]]]) -> None:
        for group, _members in groups:
            self.user_groups[group.source_user_group_id] = group

    async def migrate_group_to_user_by_external_id(self, group_external_id: str, user_email: str, connector_id: str) -> None:
        if self.user_groups.pop(group_external_id, None) is None:
            return
        replacement = Permission(email=user_email, type=PermissionType.OWNER, entity_type=EntityType.USER)
        for store in (self.record_group_permissions, self.record_permissions):
            for key, permissions in store.items():
                store[key] = [
                    replacement if p.entity_type == EntityType.GROUP and p.external_id == group_external_id else p
                    for p in permissions
                ]

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        for ext_id, record in list(self.records.items()):
            if record.id == record_id:
                del self.records[ext_id]
                self.record_permissions.pop(ext_id, None)
        self.deleted.append(record_id)

    async def on_records_moved(self, moves: list[tuple[str, Record, list[Permission]]]) -> None:
        for old_external_id, new_record, permissions in moves:
            existing = self.records.pop(old_external_id, None)
            self.record_permissions.pop(old_external_id, None)
            if existing is not None:
                new_record.id = existing.id
                new_record.source_created_at = new_record.source_created_at or existing.source_created_at
                new_record.source_updated_at = new_record.source_updated_at or existing.source_updated_at
            self.records[new_record.external_record_id] = new_record
            self.record_permissions[new_record.external_record_id] = list(permissions or [])
            self.moves.append((old_external_id, new_record.external_record_id))

    async def reindex_existing_records(self, records: list[Record], **_: object) -> None:
        self.reindexed.extend(records)

    # ------------------------------------------------------------------ queries for assertions

    def record_by_id(self, record_id: str) -> Record | None:
        return next((r for r in self.records.values() if r.id == record_id), None)

    def by_type(self, record_type: str) -> dict[str, Record]:
        return {k: r for k, r in self.records.items() if str(getattr(r.record_type, "value", r.record_type)) == record_type}

    def group_access(self, external_group_id: str) -> set[str]:
        return {principal(p) for p in self.record_group_permissions.get(external_group_id, [])}

    def access(self, external_record_id: str) -> set[str]:
        """Principals who can open a record: its own grants plus its group's, when it inherits."""
        record = self.records[external_record_id]
        who = {principal(p) for p in self.record_permissions.get(external_record_id, [])}
        if record.inherit_permissions and record.external_record_group_id:
            who |= self.group_access(record.external_record_group_id)
        return who


class FakeCheckpointStore:
    """The connector's ``record_sync_point``: checkpoint dicts by key."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, dict[str, Any]]] = []

    async def read_sync_point(self, key: str) -> dict[str, Any] | None:
        point = self.points.get(key)
        return dict(point) if point else None

    async def update_sync_point(self, key: str, data: dict[str, Any], **_: object) -> None:
        self.writes.append((key, dict(data)))
        self.points[key] = {**self.points.get(key, {}), **data}

    def issues_checkpoint(self, project_id: int) -> int | None:
        return (self.points.get(f"GITLAB/{project_id}-work-items/") or {}).get("last_sync_time")

    def mrs_checkpoint(self, project_id: int) -> int | None:
        return (self.points.get(f"GITLAB/{project_id}-merge-requests/") or {}).get("last_sync_time")

    def code_checkpoint(self, project_id: int) -> str | None:
        return (self.points.get(f"GITLAB/{project_id}-code-repository/") or {}).get("last_commit_sha")


class FakeDataStore:
    """The graph queries the post-sync timestamp backfill makes, answered from ``FakeRecordsDb``."""

    def __init__(self, db: FakeRecordsDb) -> None:
        self.db = db

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[FakeDataStore]:
        yield self

    async def get_nodes_by_filters(self, collection: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for record in self.db.records.values():
            if str(getattr(record.record_type, "value", record.record_type)) != filters.get("recordType"):
                continue
            if record.external_record_group_id != filters.get("externalGroupId"):
                continue
            if record.source_created_at is not None or record.source_updated_at is not None:
                continue
            out.append({"id": record.id, "externalRecordId": record.external_record_id, "webUrl": record.weburl})
        return out

    async def batch_update_nodes(self, patches: list[dict[str, Any]], collection: str) -> None:
        for patch in patches:
            record = self.db.record_by_id(patch["id"])
            if record is None:
                continue
            if "sourceCreatedAtTimestamp" in patch:
                record.source_created_at = patch["sourceCreatedAtTimestamp"]
            if "sourceLastModifiedTimestamp" in patch:
                record.source_updated_at = patch["sourceLastModifiedTimestamp"]


class FakeConfigService:
    """etcd, reduced to the one key per connector the GitLab connector reads."""

    def __init__(self, configs: dict[str, dict[str, Any]]) -> None:
        self.configs = configs

    async def get_config(self, path: str, *_: object, **__: object) -> dict[str, Any] | None:
        config = self.configs.get(path)
        return copy.deepcopy(config)


class FakeTokenRefresher:
    """Stands in for ``TokenRefreshService``: rotates the stored OAuth token and tells GitLab about it."""

    def __init__(self, config_service: FakeConfigService, config_path: str, server: FakeGitLab, new_token: str) -> None:
        self.config_service = config_service
        self.config_path = config_path
        self.server = server
        self.new_token = new_token
        self.calls: list[tuple[str, str, str]] = []

    async def refresh_now(self, connector_id: str, connector_type: str, refresh_token: str, **_: object) -> None:
        self.calls.append((connector_id, connector_type, refresh_token))
        self.server.valid_tokens.add(self.new_token)
        creds = self.config_service.configs[self.config_path].setdefault("credentials", {})
        creds["access_token"] = self.new_token
        creds["refresh_token"] = f"refresh-after-{len(self.calls)}"

