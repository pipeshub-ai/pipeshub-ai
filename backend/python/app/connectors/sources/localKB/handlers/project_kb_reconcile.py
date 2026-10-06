"""Reconcile a project's hidden knowledge base with the project's members (H7).

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

PROJECT_KB_NAME_PREFIX = "project:"
USER_KEY_CHUNK_SIZE = 500


class ProjectKbReconcileError(Exception):
    """The reconcile could not be applied; the event should be retried."""


class PrincipalType(str, Enum):
    USER = "user"
    TEAM = "team"


class TeamEdgeRole(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    team_id: str
    role: Literal["WRITER", "READER"]


class ProjectKbDesired(BaseModel):
    """Desired KB membership as Node sends it: Mongo user ids and team ids, not graph keys."""

    project_id: str
    owner_user_id: str
    editor_user_ids: list[str] = Field(default_factory=list)
    viewer_user_ids: list[str] = Field(default_factory=list)
    teams: list[TeamEdgeRole] = Field(default_factory=list)
    org_visible: bool = False


class ProjectKbSyncPayload(BaseModel):
    """Wire shape of the ``projectKbSync`` entity event (camelCase, written by Node ``enqueueSync``)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    org_id: str
    project_id: str
    kb_id: str
    owner_user_id: str
    editor_user_ids: list[str] = Field(default_factory=list)
    viewer_user_ids: list[str] = Field(default_factory=list)
    teams: list[TeamEdgeRole] = Field(default_factory=list)
    org_visible: bool = False

    @property
    def desired(self) -> ProjectKbDesired:
        return ProjectKbDesired(
            project_id=self.project_id,
            owner_user_id=self.owner_user_id,
            editor_user_ids=self.editor_user_ids,
            viewer_user_ids=self.viewer_user_ids,
            teams=self.teams,
            org_visible=self.org_visible,
        )


class KbPermissions(BaseModel):
    """Current PERMISSION edges into the KB, keyed by graph id. A team edge may carry no role."""

    users: dict[str, str | None] = Field(default_factory=dict)
    teams: dict[str, str | None] = Field(default_factory=dict)
    created_by: str | None = None


class KbDesired(BaseModel):
    users: dict[str, Literal["OWNER", "WRITER", "READER"]]
    teams: dict[str, Literal["WRITER", "READER"]] = Field(default_factory=dict)
    created_by: str

    @model_validator(mode="after")
    def _requires_an_owner(self) -> "KbDesired":
        if "OWNER" not in self.users.values():
            raise ValueError("a knowledge base needs at least one OWNER")
        return self


class KbGrant(BaseModel):
    principal_type: PrincipalType
    principal_id: str
    role: str


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
    """Grants for every missing or differently-roled desired principal, removals for every
    other edge, and a ``createdBy`` change when it differs from the desired owner.

    ``KbDesired`` always holds an OWNER and the caller applies grants before removals, so the
    KB never passes through a state with no owner.
    """
    ops = KbOps()
    for user_id, role in sorted(desired.users.items()):
        if current.users.get(user_id) != role:
            ops.grants.append(KbGrant(principal_type=PrincipalType.USER, principal_id=user_id, role=role))
    for team_id, role in sorted(desired.teams.items()):
        if current.teams.get(team_id) != role:
            ops.grants.append(KbGrant(principal_type=PrincipalType.TEAM, principal_id=team_id, role=role))
    ops.removals.extend(
        KbPrincipalRef(principal_type=PrincipalType.USER, principal_id=user_id)
        for user_id in sorted(current.users.keys() - desired.users.keys())
    )
    ops.removals.extend(
        KbPrincipalRef(principal_type=PrincipalType.TEAM, principal_id=team_id)
        for team_id in sorted(current.teams.keys() - desired.teams.keys())
    )
    if current.created_by != desired.created_by:
        ops.created_by = desired.created_by
    return ops


class ReconcileResult(BaseModel):
    ops: KbOps = Field(default_factory=KbOps)
    skipped_reason: str | None = None


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

    keys = await graph.get_graph_user_keys_by_mongo_user_ids(
        list(users), org_id, chunk_size=USER_KEY_CHUNK_SIZE
    )
    if desired.owner_user_id not in keys:
        raise ProjectKbReconcileError("project owner has no user node in the graph")
    graph_users = {keys[mongo_id]: role for mongo_id, role in users.items() if mongo_id in keys}

    team_roles: dict[str, Literal["WRITER", "READER"]] = {t.team_id: t.role for t in desired.teams}
    if desired.org_visible:
        team_roles.setdefault(_all_org_team_id(org_id), "READER")
    teams: dict[str, Literal["WRITER", "READER"]] = {}
    if team_roles:
        found = await graph.get_nodes_by_field_in(
            CollectionNames.TEAMS.value, "id", list(team_roles), ["id", "orgId"]
        )
        in_org = {t.get("id") for t in found or [] if t.get("orgId") == org_id}
        teams = {team_id: role for team_id, role in team_roles.items() if team_id in in_org}

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
            current.teams[principal_id] = row.get("role") or None
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
    Idempotent. Raises ``ProjectKbReconcileError`` when a write fails so the caller retries.
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

    target = await _resolve_desired(graph, org_id, desired)
    ops = plan_kb_permission_ops(await _current_permissions(graph, kb_id, kb.get("createdBy")), target)
    if ops.is_noop:
        return ReconcileResult(ops=ops)

    if ops.grants:
        granted = await graph.create_kb_principal_permissions(
            kb_id,
            [
                {"principalType": g.principal_type.value, "principalId": g.principal_id, "role": g.role}
                for g in ops.grants
            ],
        )
        if not granted.get("success"):
            raise ProjectKbReconcileError(f"granting KB permissions failed: {granted.get('reason')}")

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
            # False means the edge is already gone, which is the state we want.
            await graph.delete_edge(
                ref.principal_id,
                from_collection,
                kb_id,
                CollectionNames.APPS.value,
                CollectionNames.PERMISSION.value,
            )
    return ReconcileResult(ops=ops)
