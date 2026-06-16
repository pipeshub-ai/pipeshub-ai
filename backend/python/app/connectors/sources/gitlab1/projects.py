"""
Project synchronisation for the GitLab connector.

Responsibilities:
- Resolve the set of projects to sync (applying GROUP_IDS / PROJECT_IDS filters).
- Create group-level and project-level ``RecordGroup`` nodes with correct permissions.
- Build the included-group hierarchy so the browse-view tree resolves correctly.
- Handle the ``list_project_members_all`` failure / empty-result fallback chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gitlab.v4.objects import GroupMember, Project

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
from app.models.entities import AppUserGroup, RecordGroup, RecordGroupType
from app.models.permission import EntityType, Permission, PermissionType

from .constants import PSEUDO_USER_GROUP_PREFIX

if TYPE_CHECKING:
    from app.connectors.sources.gitlab1.connector import GitLabConnector


def _filter_op_val(f: Any) -> str:
    """Lower-cased string value of a Filter's operator."""
    op = f.operator
    return (op.value if hasattr(op, "value") else str(op)).lower()


class ProjectsSync:
    """Handles project-level record-group creation and permission syncing."""

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def sync_all_projects(self) -> None:
        """Sync all project record groups, issues, MRs, and code repos."""
        from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
        from app.utils.time_conversion import get_epoch_timestamp_in_ms
        from .models import GitlabLiterals

        current_timestamp = get_epoch_timestamp_in_ms()
        gitlab_record_group_sync_key = generate_record_sync_point_key(
            Connectors.GITLAB.value,
            GitlabLiterals.RECORD_GROUP.value,
            GitlabLiterals.GLOBAL.value,
        )
        await self._sync_projects()
        await self.c.record_sync_point.update_sync_point(
            gitlab_record_group_sync_key,
            {GitlabLiterals.LAST_SYNC_TIME.value: current_timestamp},
        )

    async def _sync_projects(self) -> None:
        """Sync each project: members, issues, MRs, and code repository."""
        projects = await self._resolve_projects_with_filters()
        if not projects:
            self.logger.warning("No projects to sync after applying filters")
            return
        for project in projects:
            project_id: int = project.id
            project_path: str = project.path_with_namespace
            for step_name, step in (
                ("members", lambda: self._sync_project_members_as_pseudo(project)),
                ("issues", lambda: self.c.issues.fetch_issues_batched(project_id)),
                ("merge_requests", lambda: self.c.merge_requests.fetch_prs_batched(project_id)),
                (
                    "code",
                    lambda: self.c.repos.run(
                        project_id,
                        project_path,
                        project.default_branch or "main",
                    ),
                ),
            ):
                try:
                    await step()
                except Exception as e:
                    self.logger.error(
                        "Unhandled error syncing %s for project %s (%s); continuing: %s",
                        step_name, project_id, project_path, e, exc_info=True,
                    )

    # ------------------------------------------------------------------
    # Project resolution
    # ------------------------------------------------------------------

    async def _resolve_projects_with_filters(self) -> list[Project]:
        """Resolve projects to sync from sync filters.

        Semantics:
        - ``PROJECT_IDS IN`` is authoritative: only listed projects sync.
        - ``GROUP_IDS IN`` (without ``PROJECT_IDS IN``): expand to all projects
          under each listed group/subgroup hierarchy.
        - ``NOT_IN`` filters are subtractive.

        Also seeds ``c._gitlab_included_group_paths`` so each project's
        ``RecordGroup`` can be linked to a parent group node.
        """
        c = self.c
        if not c.data_source:
            raise Exception("GitLab data source not initialized")
        c._gitlab_included_group_paths = None
        sf = c.sync_filters

        grp_f = sf.get(SyncFilterKey.GROUP_IDS) if sf else None
        proj_f = sf.get(SyncFilterKey.PROJECT_IDS) if sf else None
        grp_paths = list(grp_f.value) if (grp_f and not grp_f.is_empty()) else []  # type: ignore[arg-type]
        proj_paths = list(proj_f.value) if (proj_f and not proj_f.is_empty()) else []  # type: ignore[arg-type]
        grp_op = _filter_op_val(grp_f) if grp_paths else None
        proj_op = _filter_op_val(proj_f) if proj_paths else None

        grp_in = grp_paths if grp_op == FilterOperator.IN else []
        grp_not_in = grp_paths if grp_op == FilterOperator.NOT_IN else []
        proj_in = proj_paths if proj_op == FilterOperator.IN else []
        proj_not_in = proj_paths if proj_op == FilterOperator.NOT_IN else []

        by_id: dict[int, Project] = {}

        if proj_in:
            for pth in proj_in:
                res = await c.runtime.ds_call(c.data_source.get_project, pth)
                if not res.success or not res.data:
                    self.logger.error("Repository not found or inaccessible: %s (%s)", pth, res.error)
                    continue
                by_id[int(res.data.id)] = res.data
        elif grp_in:
            for gp in grp_in:
                gres = await c.runtime.paged_list(
                    c.data_source.list_group_projects, gp,
                    include_subgroups=True,
                    progress_label=f"list_group_projects({gp})",
                )
                if not gres.success:
                    self.logger.error("Could not list projects for group %s: %s", gp, gres.error)
                    continue
                for p in gres.data or []:
                    by_id[int(p.id)] = p
        else:
            res = await c.scope.paged_list_projects_with_role_fallback(
                pagination="keyset", order_by="id", sort="asc", per_page=100,
                progress_label="list_projects unscoped",
            )
            if not res.success:
                raise Exception("Error in fetching projects")
            for p in res.data or []:
                by_id[int(p.id)] = p

        candidates = list(by_id.values())
        if proj_not_in:
            excluded = set(proj_not_in)
            candidates = [
                p for p in candidates
                if getattr(p, "path_with_namespace", None) not in excluded
            ]
        if grp_not_in:
            candidates = [
                p for p in candidates
                if not _namespace_under_any_prefix(_namespace_full_path(p), grp_not_in)
            ]

        if not candidates:
            return []

        included_group_paths = await self._build_included_group_hierarchy(
            candidates=candidates, grp_in=grp_in, grp_not_in=grp_not_in, proj_in=proj_in,
        )
        if included_group_paths:
            await self._ensure_gitlab_group_record_groups(
                included_group_paths, candidate_projects=candidates
            )
            c._gitlab_included_group_paths = included_group_paths

        return candidates

    async def _build_included_group_hierarchy(
        self,
        *,
        candidates: list[Project],
        grp_in: list[str],
        grp_not_in: list[str],
        proj_in: list[str],
    ) -> list[str]:
        """Compute namespace paths whose ``RecordGroup`` nodes must exist for
        candidate projects to be reachable in the browse view."""
        seen: set[str] = set()
        ordered: list[str] = []

        def _add(path: str | None) -> None:
            if path and path not in seen:
                seen.add(path)
                ordered.append(path)

        for gp in grp_in:
            _add(gp)

        if proj_in:
            for p in candidates:
                if _namespace_is_group(p):
                    _add(_namespace_full_path(p))

        if grp_not_in and not grp_in and not proj_in:
            groups_res = await self.c.scope.paged_list_groups_with_role_fallback(
                per_page=100,
                progress_label="list_groups group NOT_IN hierarchy",
            )
            if groups_res.success and groups_res.data:
                excluded_set = set(grp_not_in)
                for g in groups_res.data:
                    gfp = getattr(g, "full_path", None)
                    if (
                        gfp
                        and gfp not in excluded_set
                        and not _namespace_under_any_prefix(gfp, grp_not_in)
                    ):
                        _add(gfp)

        return ordered

    # ------------------------------------------------------------------
    # Group RecordGroups
    # ------------------------------------------------------------------

    async def _ensure_gitlab_group_record_groups(
        self,
        group_paths: list[str],
        candidate_projects: list[Project] | None = None,
    ) -> None:
        """Create top-level GitLab group record groups before project groups reference them.

        Fallback chain when the direct group-members listing is unusable:
          1. Union of members across ``candidate_projects`` whose namespace is under
             ``group_path``.
          2. Creator-only permission.
        """
        c = self.c
        if not c.data_source:
            return
        self.logger.info("Ensuring GitLab group record groups for %s", group_paths)
        for group_path in group_paths:
            group_res = await c.runtime.ds_call(c.data_source.get_group, group_path)
            if not group_res.success or not group_res.data:
                creator_permission = c.creator_user_permission()
                if creator_permission is None:
                    self.logger.error(
                        "GitLab group %s not found/inaccessible (%s) and no creator identity; "
                        "child projects will be orphaned in the browse view.",
                        group_path, group_res.error,
                    )
                    continue
                self.logger.warning(
                    "GitLab group %s not found/inaccessible (%s); creating creator-only RecordGroup.",
                    group_path, group_res.error,
                )
                group_rg = RecordGroup(
                    org_id=c.data_entities_processor.org_id,
                    name=group_path,
                    group_type=RecordGroupType.PROJECT.value,
                    connector_name=c.connector_name,
                    connector_id=c.connector_id,
                    external_group_id=group_path,
                    web_url=None,
                )
                await c.data_entities_processor.on_new_record_groups([(group_rg, [creator_permission])])
                continue

            group = group_res.data
            full_path = getattr(group, "full_path", None) or str(getattr(group, "id", group_path))

            group_permissions: list[Permission] = []
            members_res = await c.runtime.ds_call(
                c.data_source.list_group_members_all, group_id=group_path, get_all=True,
            )
            if not members_res.success:
                self.logger.warning(
                    "Could not list members for GitLab group %s: %s. Attempting child-project union.",
                    group_path, members_res.error,
                )
            else:
                for member in members_res.data or []:
                    if getattr(member, "access_level", 0) == 0:
                        continue
                    permission = await self._transform_restrictions_to_permissions(member)
                    if permission:
                        group_permissions.append(permission)

            # Tier 1 fallback: union members from child projects
            if not group_permissions and candidate_projects:
                group_permissions = await self._group_permissions_from_child_projects(
                    group_path=group_path, candidate_projects=candidate_projects,
                )

            # Always ensure the creator has access to the group node
            creator_permission = c.creator_user_permission()
            if creator_permission is not None and not any(
                getattr(p, "email", None) == creator_permission.email for p in group_permissions
            ):
                if not group_permissions and not candidate_projects:
                    self.logger.warning(
                        "GitLab group %s: group-members and child-project union both produced 0 "
                        "permissions; applying creator-only fallback for %s.",
                        group_path, c.creator_email,
                    )
                group_permissions.append(creator_permission)

            group_rg = RecordGroup(
                org_id=c.data_entities_processor.org_id,
                name=getattr(group, "name", full_path) or full_path,
                group_type=RecordGroupType.PROJECT.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                external_group_id=full_path,
                web_url=getattr(group, "web_url", None),
            )
            await c.data_entities_processor.on_new_record_groups([(group_rg, group_permissions)])

    async def _group_permissions_from_child_projects(
        self,
        group_path: str,
        candidate_projects: list[Project],
    ) -> list[Permission]:
        """Union child-project members into group-level USER permissions.

        Used as a fallback when ``list_group_members_all`` returns nothing usable
        (403, empty, or EE-Auditor token without group membership).
        """
        c = self.c
        child_projects = [
            p for p in candidate_projects
            if _namespace_under_any_prefix(_namespace_full_path(p), [group_path])
        ]
        if not child_projects:
            self.logger.debug(
                "child-project union: no candidate projects under group %s", group_path
            )
            return []

        member_map: dict[int, Any] = {}
        listed_projects = failed_projects = 0
        for proj in child_projects:
            proj_key = getattr(proj, "id", None) or getattr(proj, "path_with_namespace", None)
            if proj_key is None:
                continue
            pm_res = await c.runtime.ds_call(
                c.data_source.list_project_members_all, project_id=proj_key, get_all=True,
            )
            if not pm_res.success:
                failed_projects += 1
                self.logger.debug(
                    "child-project union: list_project_members_all(%s) failed under group %s: %s",
                    proj_key, group_path, pm_res.error,
                )
                continue
            listed_projects += 1
            for m in pm_res.data or []:
                uid = getattr(m, "id", None)
                if uid is None:
                    continue
                m_level = getattr(m, "access_level", 0) or 0
                if m_level == 0:
                    continue
                existing = member_map.get(uid)
                existing_level = getattr(existing, "access_level", 0) or 0
                if existing is None or m_level > existing_level:
                    member_map[uid] = m

        permissions: list[Permission] = []
        for m in member_map.values():
            permission = await self._transform_restrictions_to_permissions(m)
            if permission:
                permissions.append(permission)

        self.logger.info(
            "child-project union for group %s: listed=%s failed=%s, %s unique member(s) -> %s permission(s)",
            group_path, listed_projects, failed_projects, len(member_map), len(permissions),
        )
        return permissions

    # ------------------------------------------------------------------
    # Project member sync
    # ------------------------------------------------------------------

    async def _sync_project_members_as_pseudo(self, project: Project) -> None:
        """Sync project members; create RecordGroups with correct permissions.

        Falls back to creator-only permissions when ``list_project_members_all``
        fails or returns an empty list (GitLab returns ``200 OK + []`` instead of
        403 for tokens below the required role on private projects).
        """
        c = self.c
        project_id = project.id
        project_name = project.name
        dict_member: dict[int, GroupMember] = {}
        self.logger.info("Syncing users for project %s", project_name)
        members_res = await c.runtime.ds_call(
            c.data_source.list_project_members_all, project_id=project_id, get_all=True,
        )
        listing_failed = not members_res.success
        listing_empty = members_res.success and not (members_res.data or [])

        if listing_failed or listing_empty:
            creator_perm = c.creator_user_permission()
            if creator_perm is None:
                if listing_failed:
                    self.logger.error(
                        "Error fetching members for project %s (%s): %s",
                        project_id, project_name, getattr(members_res, "error", "unknown"),
                    )
                else:
                    self.logger.info("No members found for project %s", project_id)
                return
            if listing_failed:
                self.logger.error(
                    "Error fetching members for project %s (%s): %s. "
                    "Falling back to creator-only permissions.",
                    project_id, project_name, getattr(members_res, "error", "unknown"),
                )
            else:
                self.logger.warning(
                    "No members returned for project %s (%s); GitLab returns [] for tokens "
                    "below the required role on private projects. Falling back to creator-only.",
                    project_id, project_name,
                )
            await self._apply_creator_fallback_for_project(project)
            return

        members = members_res.data
        # Keep highest access_level when a user appears via multiple sources
        for member in members:
            existing = dict_member.get(member.id)
            if existing is None:
                dict_member[member.id] = member
                continue
            new_level = getattr(member, "access_level", 0) or 0
            old_level = getattr(existing, "access_level", 0) or 0
            if new_level > old_level:
                dict_member[member.id] = member

        # Inject creator so Admin/Auditor personas (no membership row) get access
        c.users._inject_creator_member_into(dict_member)

        permission_project_level: list[Permission] = []
        permission_work_items_level: list[Permission] = []
        permission_code_repo_level: list[Permission] = []
        permission_merge_requests_level: list[Permission] = []

        for member in dict_member.values():
            permission = await self._transform_restrictions_to_permissions(member)
            if not permission:
                continue
            permission_project_level.append(permission)
            level: int = getattr(member, "access_level", 0)
            if level == 0:
                self.logger.info("Member %s has no access level, skipping", member.name)
            elif level == 10:
                permission_work_items_level.append(permission)
            elif level >= 15:
                permission_work_items_level.append(permission)
                permission_merge_requests_level.append(permission)
                permission_code_repo_level.append(permission)
            else:
                self.logger.warning(
                    "Member %s has unrecognized access level %s, skipping", member.name, level
                )

        (
            project_record_group,
            work_items_record_group,
            merge_requests_record_group,
            code_repo_record_group,
        ) = self._build_project_record_groups(project)
        await c.data_entities_processor.on_new_record_groups(
            [
                (project_record_group, permission_project_level),
                (work_items_record_group, permission_work_items_level),
                (code_repo_record_group, permission_code_repo_level),
                (merge_requests_record_group, permission_merge_requests_level),
            ]
        )

    def _build_project_record_groups(
        self, project: Project
    ) -> tuple[RecordGroup, RecordGroup, RecordGroup, RecordGroup]:
        """Return ``(project, work_items, merge_requests, code_repo)`` record groups.

        Single source of truth for the four-RG shape to keep the creator-fallback
        path and the normal member-sync path in sync.
        """
        c = self.c
        parent_for_project_rg: str | None = None
        if c._gitlab_included_group_paths:
            ns_path = _namespace_full_path(project)
            parent_for_project_rg = _longest_matching_group_path(
                ns_path, c._gitlab_included_group_paths
            )

        return (
            RecordGroup(
                org_id=c.data_entities_processor.org_id,
                name=project.path_with_namespace,
                group_type=RecordGroupType.PROJECT.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                external_group_id=str(project.id),
                parent_external_group_id=parent_for_project_rg,
            ),
            RecordGroup(
                org_id=c.data_entities_processor.org_id,
                name="Work items",
                group_type=RecordGroupType.PROJECT.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                external_group_id=f"{project.id}-work-items",
                parent_external_group_id=str(project.id),
            ),
            RecordGroup(
                org_id=c.data_entities_processor.org_id,
                name="Merge requests",
                group_type=RecordGroupType.PROJECT.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                external_group_id=f"{project.id}-merge-requests",
                parent_external_group_id=str(project.id),
            ),
            RecordGroup(
                org_id=c.data_entities_processor.org_id,
                name="Code repository",
                group_type=RecordGroupType.PROJECT.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                external_group_id=f"{project.id}-code-repository",
                parent_external_group_id=str(project.id),
            ),
        )

    async def _apply_creator_fallback_for_project(self, project: Project) -> None:
        """Create the four project ``RecordGroup`` nodes with creator-only ACLs.

        Called when ``list_project_members_all`` fails or returns empty.
        """
        c = self.c
        creator_permission = c.creator_user_permission()
        if creator_permission is None:
            self.logger.error(
                "Cannot fall back to creator-only permissions for project %s (%s): "
                "no creator identity. Downstream records on this project will not be ingested.",
                project.id, project.path_with_namespace,
            )
            return
        (
            project_record_group,
            work_items_record_group,
            merge_requests_record_group,
            code_repo_record_group,
        ) = self._build_project_record_groups(project)
        perms = [creator_permission]
        await c.data_entities_processor.on_new_record_groups(
            [
                (project_record_group, perms),
                (work_items_record_group, perms),
                (code_repo_record_group, perms),
                (merge_requests_record_group, perms),
            ]
        )
        self.logger.info(
            "Applied creator-only fallback permissions to project %s (%s) for %s",
            project.id, project.path_with_namespace, c.creator_email,
        )

    # ------------------------------------------------------------------
    # Permission helpers
    # ------------------------------------------------------------------

    async def _transform_restrictions_to_permissions(
        self, member: GroupMember
    ) -> Permission | None:
        """Build a ``Permission`` object for a member by looking up the AppUser or creating a pseudo-group."""
        return await self._create_permission_from_principal(
            EntityType.USER.value,
            str(member.id),
            PermissionType.OWNER.value,
            create_pseudo_group_if_missing=True,
        )

    async def _create_permission_from_principal(
        self,
        principal_type: str,
        principal_id: str,
        permission_type: PermissionType,
        *,
        create_pseudo_group_if_missing: bool = False,
    ) -> Permission | None:
        """Look up a principal in the DB and return the appropriate ``Permission`` object.

        When a user is not found and ``create_pseudo_group_if_missing`` is True,
        creates a pseudo-group to preserve the permission until a real AppUser
        row is created on the next sync.
        """
        c = self.c
        try:
            if principal_type == EntityType.USER.value:
                async with c.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_source_id(
                        source_user_id=principal_id,
                        connector_id=c.connector_id,
                    )
                    if user:
                        return Permission(
                            email=user.email,
                            type=permission_type,
                            entity_type=EntityType.USER,
                        )
                    if create_pseudo_group_if_missing:
                        pseudo_group = await tx_store.get_user_group_by_external_id(
                            connector_id=c.connector_id,
                            external_id=principal_id,
                        )
                        if not pseudo_group:
                            pseudo_group = await self._create_pseudo_group(principal_id)
                        if pseudo_group:
                            self.logger.debug(
                                "Using pseudo-group for user %s (no email available)", principal_id
                            )
                            return Permission(
                                external_id=pseudo_group.source_user_group_id,
                                type=permission_type,
                                entity_type=EntityType.GROUP,
                            )
                    self.logger.debug("User %s not found in DB, skipping permission", principal_id)
                    return None
        except Exception as e:
            self.logger.error("Failed to create permission from principal: %s", e)
            return None

    async def _create_pseudo_group(self, account_id: str) -> AppUserGroup | None:
        """Create a pseudo-group for a user without an email address.

        The pseudo-group preserves the permission until the user's real
        AppUser row is created on the next sync.
        """
        c = self.c
        try:
            pseudo_group = AppUserGroup(
                app_name=Connectors.GITLAB,
                connector_id=c.connector_id,
                source_user_group_id=account_id,
                name=f"{PSEUDO_USER_GROUP_PREFIX}_{account_id}",
                org_id=c.data_entities_processor.org_id,
            )
            await c.data_entities_processor.on_new_user_groups([(pseudo_group, [])])
            self.logger.info("Created pseudo-group for user without email: %s", account_id)
            return pseudo_group
        except Exception as e:
            self.logger.error("Failed to create pseudo-group for %s: %s", account_id, e)
            return None


# ------------------------------------------------------------------
# Module-level static helpers (used by both projects.py and users.py)
# ------------------------------------------------------------------

def _namespace_full_path(project: Project) -> str | None:
    """Return the namespace ``full_path`` for a project, or ``None``."""
    ns = getattr(project, "namespace", None)
    if ns is None:
        return None
    fp = getattr(ns, "full_path", None)
    if isinstance(fp, str):
        return fp
    if isinstance(ns, dict):
        return ns.get("full_path")
    return None


def _namespace_is_group(project: Project) -> bool:
    """False for personal projects (user namespace); True for group namespaces."""
    ns = getattr(project, "namespace", None)
    if ns is None:
        return False
    kind = getattr(ns, "kind", None) or (ns.get("kind") if isinstance(ns, dict) else None)
    return kind != "user"


def _namespace_under_any_prefix(namespace_path: str | None, prefixes: list[str]) -> bool:
    """True if ``namespace_path`` equals or starts with any of the given prefixes."""
    if not namespace_path:
        return False
    for p in prefixes:
        if namespace_path == p or namespace_path.startswith(p + "/"):
            return True
    return False


def _longest_matching_group_path(
    namespace_path: str | None, group_paths: list[str]
) -> str | None:
    """Return the longest group path that is a prefix of ``namespace_path``."""
    if not namespace_path or not group_paths:
        return None
    best: str | None = None
    best_len = -1
    for p in group_paths:
        if namespace_path == p or namespace_path.startswith(p + "/"):
            if len(p) > best_len:
                best = p
                best_len = len(p)
    return best
