"""Reconcile a project's hidden knowledge base with the project's members.

Node publishes the desired membership as a ``projectKbSync`` event; this module turns the
difference with the graph into grants and removals. It is a full reconcile rather than a
patch, so a replayed or duplicated event converges to the same graph.
"""

import logging
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.config.constants.arangodb import CollectionNames, Connectors
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

PROJECT_KB_NAME_PREFIX = "project:"
SYNC_VERSION_FIELD = "projectSyncVersion"
USER_KEY_CHUNK_SIZE = 500


class ProjectKbReconcileError(Exception):
    """The reconcile could not be applied; the event should be retried."""


class PrincipalType(str, Enum):
    USER = "user"
    TEAM = "team"


class ProjectTeam(BaseModel):
    """A team with access to the project. A team->KB edge carries no role: each member
    gets their own role on the team, so any role sent alongside is ignored."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    team_id: str


class ProjectKbDesired(BaseModel):
    """Desired KB membership as Node sends it: Mongo user ids and team ids, not graph keys."""

    project_id: str
    owner_user_id: str
    editor_user_ids: list[str] = Field(default_factory=list)
    viewer_user_ids: list[str] = Field(default_factory=list)
    teams: list[ProjectTeam] = Field(default_factory=list)
    org_visible: bool = False
    # Project updatedAt (epoch ms) of the snapshot; None from publishers that predate it.
    version: int | None = None


class ProjectKbSyncPayload(BaseModel):
    """Wire shape of the ``projectKbSync`` entity event (camelCase, written by Node ``enqueueSync``)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    org_id: str
    project_id: str
    kb_id: str
    owner_user_id: str
    editor_user_ids: list[str] = Field(default_factory=list)
    viewer_user_ids: list[str] = Field(default_factory=list)
    teams: list[ProjectTeam] = Field(default_factory=list)
    org_visible: bool = False
    version: int | None = None

    @property
    def desired(self) -> ProjectKbDesired:
        return ProjectKbDesired(
            project_id=self.project_id,
            owner_user_id=self.owner_user_id,
            editor_user_ids=self.editor_user_ids,
            viewer_user_ids=self.viewer_user_ids,
            teams=self.teams,
            org_visible=self.org_visible,
            version=self.version,
        )


class KbPermissions(BaseModel):
    """Current PERMISSION edges into the KB, keyed by graph id."""

    users: dict[str, str | None] = Field(default_factory=dict)
    teams: set[str] = Field(default_factory=set)
    created_by: str | None = None


class KbDesired(BaseModel):
    users: dict[str, Literal["OWNER", "WRITER", "READER"]]
    teams: set[str] = Field(default_factory=set)
    created_by: str

    @model_validator(mode="after")
    def _requires_an_owner(self) -> "KbDesired":
        if "OWNER" not in self.users.values():
            raise ValueError("a knowledge base needs at least one OWNER")
        return self


class KbGrant(BaseModel):
    principal_type: PrincipalType
    principal_id: str
    role: str | None = None


class KbPrincipalRef(BaseModel):
    principal_type: PrincipalType
    principal_id: str


class KbOps(BaseModel):
    grants: list[KbGrant] = Field(default_factory=list)
    removals: list[KbPrincipalRef] = Field(default_factory=list)
    created_by: str | None = None

    @property
    def is_noop(self) -> bool:
        return not self.grants and not self.removals and self.created_by is None


def plan_kb_permission_ops(current: KbPermissions, desired: KbDesired) -> KbOps:
    """Grants for every missing or differently-roled desired user and every missing team, removals for every
    other edge, and a ``createdBy`` change when it differs from the desired owner.

    ``KbDesired`` always holds an OWNER and the caller applies grants before removals, so the
    KB never passes through a state with no owner.
    """
    ops = KbOps()
    for user_id, role in sorted(desired.users.items()):
        if current.users.get(user_id) != role:
            ops.grants.append(KbGrant(principal_type=PrincipalType.USER, principal_id=user_id, role=role))
    for team_id in sorted(desired.teams - current.teams):
        ops.grants.append(KbGrant(principal_type=PrincipalType.TEAM, principal_id=team_id))
    ops.removals.extend(
        KbPrincipalRef(principal_type=PrincipalType.USER, principal_id=user_id)
        for user_id in sorted(current.users.keys() - desired.users.keys())
    )
    ops.removals.extend(
        KbPrincipalRef(principal_type=PrincipalType.TEAM, principal_id=team_id)
        for team_id in sorted(current.teams - desired.teams)
    )
    if current.created_by != desired.created_by:
        ops.created_by = desired.created_by
    return ops


class ReconcileResult(BaseModel):
    ops: KbOps = Field(default_factory=KbOps)
    skipped_reason: str | None = None
    stale: bool = False


def _all_org_team_id(org_id: str) -> str:
    return f"all_{org_id}"


async def _resolve_desired(
    graph: IGraphDBProvider, org_id: str, desired: ProjectKbDesired
) -> KbDesired:
    users: dict[str, Literal["OWNER", "WRITER", "READER"]] = {}
    for user_id in desired.viewer_user_ids:
        users[user_id] = "READER"
    for user_id in desired.editor_user_ids:
        users[user_id] = "WRITER"
    users[desired.owner_user_id] = "OWNER"

    # A member without a graph User node yet is left out instead of failing the whole event.
    keys = await graph.get_graph_user_keys_by_mongo_user_ids(
        list(users), org_id, chunk_size=USER_KEY_CHUNK_SIZE, allow_missing=True
    )
    if desired.owner_user_id not in keys:
        raise ProjectKbReconcileError("project owner has no user node in the graph")
    graph_users = {keys[mongo_id]: role for mongo_id, role in users.items() if mongo_id in keys}

    team_ids = {t.team_id for t in desired.teams}
    if desired.org_visible:
        team_ids.add(_all_org_team_id(org_id))
    teams: set[str] = set()
    if team_ids:
        found = await graph.get_nodes_by_field_in(
            CollectionNames.TEAMS.value, "id", sorted(team_ids), ["id", "orgId"], raise_on_error=True
        )
        teams = {t.get("id") for t in found or [] if t.get("orgId") == org_id} & team_ids

    return KbDesired(users=graph_users, teams=teams, created_by=desired.owner_user_id)


async def _current_permissions(
    graph: IGraphDBProvider, kb_id: str, created_by: str | None
) -> KbPermissions:
    current = KbPermissions(created_by=created_by)
    # A failed read must fail the event (retry), not plan as if the KB had no edges.
    for row in await graph.list_kb_permissions(kb_id, raise_on_error=True):
        principal_id = row.get("id")
        if not principal_id:
            continue
        edge_type = row.get("type")
        if edge_type == "USER":
            current.users[principal_id] = row.get("role")
        elif edge_type == "TEAM":
            current.teams.add(principal_id)
    return current


async def reconcile_project_kb(
    graph: IGraphDBProvider,
    org_id: str,
    kb_id: str,
    desired: ProjectKbDesired,
    logger: logging.Logger | None = None,
) -> ReconcileResult:
    """Bring the KB's permission edges and ``createdBy`` in line with ``desired``.

    Refuses (no writes) unless the KB is the hidden ``project:{projectId}`` KB of this org.
    Skips a snapshot older than the last one applied (``stale``). Idempotent. Raises
    ``ProjectKbReconcileError`` when a write fails so the caller retries.
    """
    log = logger or logging.getLogger(__name__)
    kb = await graph.get_document(kb_id, CollectionNames.APPS.value, raise_on_error=True)
    if kb is None:
        return ReconcileResult(skipped_reason="knowledge base not found")
    if (
        kb.get("type") != Connectors.KNOWLEDGE_BASE.value
        or kb.get("isHidden") is not True
        or kb.get("name") != f"{PROJECT_KB_NAME_PREFIX}{desired.project_id}"
        or kb.get("orgId") != org_id
    ):
        log.warning("Refusing project KB reconcile: %s is not the hidden KB of project %s", kb_id, desired.project_id)
        return ReconcileResult(skipped_reason="not the project's hidden knowledge base")

    applied_version = kb.get(SYNC_VERSION_FIELD)
    if desired.version is not None and applied_version is not None and desired.version < applied_version:
        log.info(
            "Skipping stale project KB sync for %s: version %s is older than applied %s",
            kb_id, desired.version, applied_version,
        )
        return ReconcileResult(stale=True)

    target = await _resolve_desired(graph, org_id, desired)
    ops = plan_kb_permission_ops(await _current_permissions(graph, kb_id, kb.get("createdBy")), target)
    await _apply_ops(graph, kb_id, ops)

    # Recorded only after the apply succeeded, so a failed apply is retried rather than
    # letting an older snapshot win.
    if desired.version is not None and desired.version != applied_version and not await graph.update_node(
        kb_id, CollectionNames.APPS.value, {SYNC_VERSION_FIELD: desired.version}
    ):
        raise ProjectKbReconcileError("recording the applied sync version failed")
    return ReconcileResult(ops=ops)


async def _apply_ops(graph: IGraphDBProvider, kb_id: str, ops: KbOps) -> None:
    if ops.grants:
        timestamp = get_epoch_timestamp_in_ms()
        edges = [
            {
                "from_id": g.principal_id,
                "from_collection": (
                    CollectionNames.USERS.value if g.principal_type is PrincipalType.USER else CollectionNames.TEAMS.value
                ),
                "to_id": kb_id,
                "to_collection": CollectionNames.APPS.value,
                "externalPermissionId": "",
                "type": g.principal_type.value.upper(),
                **({"role": g.role} if g.role else {}),
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
                "lastUpdatedTimestampAtSource": timestamp,
            }
            for g in ops.grants
        ]
        try:
            # Upserts on (principal, KB), so a changed role overwrites the existing edge.
            await graph.batch_create_edges(edges, CollectionNames.PERMISSION.value)
        except Exception as e:
            raise ProjectKbReconcileError(f"granting KB permissions failed: {e}") from e

    if ops.created_by is not None and not await graph.update_node(
        kb_id, CollectionNames.APPS.value, {"createdBy": ops.created_by}
    ):
        raise ProjectKbReconcileError("updating the KB creator failed")

    if ops.removals:
        if await graph.count_kb_owners(kb_id) < 1:
            raise ProjectKbReconcileError("refusing to remove permissions from a KB with no OWNER")
        for ref in ops.removals:
            from_collection = (
                CollectionNames.USERS.value if ref.principal_type is PrincipalType.USER else CollectionNames.TEAMS.value
            )
            # False is either "already gone" or (on ArangoDB) a failed delete; the re-read below tells them apart.
            await graph.delete_edge(
                ref.principal_id,
                from_collection,
                kb_id,
                CollectionNames.APPS.value,
                CollectionNames.PERMISSION.value,
            )
        after = await _current_permissions(graph, kb_id, None)
        left = [
            ref.principal_id
            for ref in ops.removals
            if ref.principal_id in (after.users if ref.principal_type is PrincipalType.USER else after.teams)
        ]
        if left:
            raise ProjectKbReconcileError(f"removing KB permissions failed for {len(left)} principal(s)")
